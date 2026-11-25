import express from 'express';
import fs from 'fs';
import path from 'path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'url';
import QRCode from 'qrcode';
import pino from 'pino';
import { InviteQueue } from './invite_queue.js';
import { sentMessageReceipt } from './delivery_tracking.js';
import { projectInviteDecision, createBlockedMembershipGuard } from './invite_policy.js';
import { CaptureQueue, deliverCapture, supplierEventTime, withTimeout } from './capture_queue.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const baileysModule = await import('@whiskeysockets/baileys');
const makeWASocket = baileysModule.makeWASocket || baileysModule.default;
const { useMultiFileAuthState, DisconnectReason, downloadMediaMessage, Browsers, BufferJSON } = baileysModule;

process.env.TZ ||= 'America/Sao_Paulo';
const app = express();
app.use(express.json({ limit: '10mb' }));

const PORT = Number(process.env.BRIDGE_PORT || 3051);
const PANEL_PORT = Number(process.env.PORT || 3050);
const AUTOMATION_PAUSED = process.env.BUSCABUSCA_PAUSED === '1';
const AUTOMATIC_INVITES_ENABLED = process.env.BUSCABUSCA_AUTO_JOIN === '1';
const PANEL_INGEST_URL = `http://127.0.0.1:${PANEL_PORT}/api/internal/ingest-message`;

const authFolder = path.join(__dirname, 'auth_session');
const qrFileLocal = path.join(__dirname, 'qr_whatsapp.png');
const communitiesFile = path.join(__dirname, 'comunidades_entradas.json');
const archivedMonitoredFile = path.join(__dirname, 'arquivados_monitorados.json');
const suppliersFile = path.join(__dirname, 'fornecedores.json');

let waSocket = null;
let connecting = false;
let reconnectTimer = null;
let reconnectAttempt = 0;
let socketGeneration = 0;
let stopping = false;
let lastDisconnectReason = null;
let saveCredentials = Promise.resolve();
let lastDeepScanAt = 0;
let lastDeepScanDay = '';
let lastHeartbeatAt = 0;
const subscribedNewsletters = new Set();

function scheduleReconnect(delay) {
  if (stopping || reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startWhatsApp();
  }, delay);
}

let connectionStatus = 'CONNECTING'; // CONNECTING, QR_READY, CONNECTED, DISCONNECTED
let currentQRDataUrl = null;
let connectedUser = null;
let recentLogs = [];
let connectionChangedAt = Date.now();
let lastReceivedAt = null;
let watchdogBusy = false;
let scanGeneration = null;

function setConnectionStatus(status) {
  connectionStatus = status;
  connectionChangedAt = Date.now();
}

const joinedCommunitiesMap = new Map();
const monitoredArchivedMap = new Map(); // Todos os contatos, grupos e comunidades arquivados monitorados
const autoArchiveJids = new Set();
const chatLastMsgMap = new Map();
const realMessageAnchorsMap = new Map(); // jid -> { key, messageTimestamp } real para fetchMessageHistory
const groupSubjectCache = new Map();
const groupMetaCache = new Map();
const contactNameCache = new Map();
const pendingInviteCodes = new Set();
const knownSupplierDigits = new Set();
let suppliersFileMtime = 0;
const historyRequestedJids = new Set();
const individualScanState = {
  scannedMessagesCount: 0,
  matchesByJid: new Map()
};

function recordIndividualScanMessage(msg) {
  if (!msg || !msg.message) return;
  const rawRemote = msg.key?.remoteJid || '';
  if (!rawRemote || rawRemote === 'status@broadcast' || rawRemote.endsWith('@g.us') || rawRemote.endsWith('@broadcast')) return;
  const altJid = msg.key?.remoteJidAlt || '';
  const resolvedJid = resolveLidJid(altJid || rawRemote);
  if (isKnownSupplierJid(rawRemote) || isKnownSupplierJid(resolvedJid)) return;

  individualScanState.scannedMessagesCount++;
  const text = extractPlainTextFromMessage(msg.message);
  const docName = msg.message?.documentMessage?.fileName || msg.message?.documentWithCaptionMessage?.message?.documentMessage?.fileName || '';
  const fullText = [text, docName].filter(Boolean).join(' ').trim();
  const hasImage = hasImageInMessage(msg.message);
  const hasDocument = !!(msg.message?.documentMessage || msg.message?.documentWithCaptionMessage?.message?.documentMessage);
  if (!fullText) return;

  const norm = fullText.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const matchedTerms = [];
  if (/lacradinh|lacrad[oa]/.test(norm)) matchedTerms.push('lacradinho');
  if (/comprovante|recibo/.test(norm)) matchedTerms.push('comprovante');
  if ((/foto|imagem|manda|envia|traz/.test(norm) && /lacrad/.test(norm)) || (hasImage && /lacrad|comprovante/.test(norm))) {
    matchedTerms.push('foto_lacrado');
  }
  if (matchedTerms.length === 0) return;

  const keyJid = resolvedJid || rawRemote;
  const phone = keyJid.replace(/@.*$/, '').replace(/\D/g, '');
  const name = msg.pushName || contactNameCache.get(keyJid) || contactNameCache.get(rawRemote) || phone || keyJid;
  let entry = individualScanState.matchesByJid.get(keyJid);
  if (!entry) {
    entry = { jid: keyJid, phone, name, categories: new Set(), snippets: [] };
    individualScanState.matchesByJid.set(keyJid, entry);
  } else if (name && (!entry.name || entry.name === entry.phone)) {
    entry.name = name;
  }
  for (const term of matchedTerms) entry.categories.add(term);

  const msgId = msg.key?.id || '';
  if (!entry.snippets.some(s => (msgId && s.id === msgId) || s.text === fullText.slice(0, 260))) {
    entry.snippets.push({
      id: msgId,
      timestamp: Number(msg.messageTimestamp || 0),
      fromMe: !!msg.key?.fromMe,
      text: fullText.slice(0, 260),
      hasImage,
      hasDocument,
      matchedTerms
    });
    if (entry.snippets.length > 20) entry.snippets.shift();
  }
}

function addLog(msg) {
  const line = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  console.log(line);
  recentLogs.unshift(line);
  if (recentLogs.length > 250) recentLogs.pop();
}

const captureQueue = new CaptureQueue({
  directory: path.join(__dirname, 'capturas_pendentes'),
  log: addLog,
  deliver: async payload => {
    const { mediaMessage, ...body } = payload;
    const dateOptions = { timeZone: 'America/Sao_Paulo' };
    const sameDay = new Date(Number(payload.timestamp) * 1000).toLocaleDateString('en-CA', dateOptions)
      === new Date().toLocaleDateString('en-CA', dateOptions);
    // Let the panel acknowledge expired lists without repeatedly downloading old media.
    if (mediaMessage && sameDay) {
      body.hasImage = true;
      try {
        const message = JSON.parse(mediaMessage, BufferJSON.reviver);
        if (message.message?.protocolMessage?.editedMessage) {
          message.message = message.message.protocolMessage.editedMessage;
        }
        const buffer = await withTimeout(downloadMediaMessage(message, 'buffer', {}), 20000, 'Tempo esgotado ao baixar imagem; lista preservada');
        if (Buffer.isBuffer(buffer) && buffer.length && buffer.length <= 10 * 1024 * 1024) {
          body.imageBase64 = buffer.toString('base64');
        } else {
          body.mediaIssue = 'imagem_vazia_ou_acima_de_10mb';
        }
      } catch (mediaErr) {
        if (/empty media key/i.test(mediaErr?.message || '')) {
          body.mediaIssue = 'imagem_sem_chave_de_download';
          // Newsletter/canal público sem chave de criptografia de mídia privada
          addLog(`[MEDIA] Mídia pública/newsletter sem media key; processando texto da legenda.`);
        } else {
          throw mediaErr;
        }
      }
    }
    const data = await deliverCapture(PANEL_INGEST_URL, body);
    if (data.parsedCount > 0) {
      addLog(`[PRODUTO] ${data.parsedCount} produtos capturados de "${data.supplierName}" (${payload.channelType}).`);
      registerMonitoredArchivedChat(payload.remoteJid, {
        name: payload.groupName || data.supplierName,
        type: payload.channelType,
        lastActivity: new Date().toLocaleString('pt-BR'),
        addProducts: data.parsedCount
      });
    }
  }
});

const outgoingMessages = new Map();
const earlyMessageAcks = new Map();
const pendingAckFile = path.join(__dirname, 'confirmacoes_pendentes.json');
const pendingAcks = new Map();
if (fs.existsSync(pendingAckFile)) {
  for (const item of readJsonSafe(pendingAckFile)) pendingAcks.set(item.messageId, item);
}
try {
  const historyPath = path.join(__dirname, 'historico_envios.json');
  if (fs.existsSync(historyPath)) {
    for (const item of Object.values(readJsonSafe(historyPath))) {
      if (item.message_id && item.jid) outgoingMessages.set(item.message_id, item.jid);
    }
  }
} catch (error) { addLog(`Aviso ao carregar IDs enviados: ${error.message}`); }
let flushingAcks = false;
async function flushMessageAcks() {
  if (flushingAcks || stopping) return;
  flushingAcks = true;
  try {
    for (const [id, item] of pendingAcks) {
      try {
        const response = await fetch(`http://127.0.0.1:${PANEL_PORT}/api/internal/message-ack`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item), signal: AbortSignal.timeout(8000)
        });
        if (response.ok && (await response.json()).recorded && pendingAcks.get(id) === item) {
          pendingAcks.delete(id);
          writeJsonAtomic(pendingAckFile, [...pendingAcks.values()]);
        }
      } catch (error) { addLog(`Confirmação pendente de gravação: ${error.message}`); break; }
    }
  } finally { flushingAcks = false; }
}
function queueMessageAck(messageId, status) {
  const phone = outgoingMessages.get(messageId);
  if (!phone || !Number.isInteger(status) || status < 0 || status > 5) return;
  const previous = pendingAcks.get(messageId);
  if (previous && previous.status >= status) return;
  pendingAcks.set(messageId, { phone, messageId, status });
  writeJsonAtomic(pendingAckFile, [...pendingAcks.values()]);
  void flushMessageAcks();
}
setInterval(() => void flushMessageAcks(), 10000).unref();

function writeJsonAtomic(filePath, value) {
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2), { mode: 0o600 });
  fs.renameSync(temporary, filePath);
}

function readJsonSafe(filePath) {
  const raw = fs.readFileSync(filePath, 'utf-8').replace(/^\uFEFF/, '');
  return JSON.parse(raw);
}

function resolveLidJid(jid) {
  if (!jid || typeof jid !== 'string') return jid || '';
  if (!jid.endsWith('@lid')) return jid;
  const lidNum = jid.split('@')[0].split(':')[0];
  try {
    const revFile = path.join(authFolder, `lid-mapping-${lidNum}_reverse.json`);
    if (fs.existsSync(revFile)) {
      const phone = JSON.parse(fs.readFileSync(revFile, 'utf-8')).replace(/\D/g, '');
      if (phone) {
        return `${phone}@s.whatsapp.net`;
      }
    }
  } catch {}
  return jid;
}

function normalizePhoneDigits(raw) {
  const d = String(raw || '').replace(/\D/g, '');
  if (!d) return [];
  const out = [d];
  if (d.startsWith('55') && d.length === 13) {
    out.push(d.slice(0, 4) + d.slice(5));
  } else if (d.startsWith('55') && d.length === 12) {
    out.push(d.slice(0, 4) + '9' + d.slice(4));
  } else if (d.length === 11) {
    out.push('55' + d);
    out.push('55' + d.slice(0, 2) + d.slice(3));
  } else if (d.length === 10) {
    out.push('55' + d);
    out.push('55' + d.slice(0, 2) + '9' + d.slice(2));
  }
  return out;
}

const anchorsFile = path.join(__dirname, 'ancoras_mensagens.json');
let saveAnchorsTimer = null;

function saveRealMessageAnchors(immediate = false) {
  const writeNow = () => {
    saveAnchorsTimer = null;
    try {
      const obj = {};
      for (const [jid, val] of realMessageAnchorsMap.entries()) {
        if (jid && val?.key?.id && !String(val.key.id).startsWith('ARCHIVE_')) {
          obj[jid] = val;
        }
      }
      writeJsonAtomic(anchorsFile, obj);
    } catch {}
  };
  if (immediate) {
    if (saveAnchorsTimer) clearTimeout(saveAnchorsTimer);
    writeNow();
    return;
  }
  if (!saveAnchorsTimer) {
    saveAnchorsTimer = setTimeout(writeNow, 2500);
    if (typeof saveAnchorsTimer.unref === 'function') saveAnchorsTimer.unref();
  }
}

function loadRealMessageAnchors() {
  try {
    if (fs.existsSync(anchorsFile)) {
      const obj = readJsonSafe(anchorsFile);
      if (obj && typeof obj === 'object') {
        for (const [jid, val] of Object.entries(obj)) {
          if (jid && val?.key?.id && !String(val.key.id).startsWith('ARCHIVE_')) {
            realMessageAnchorsMap.set(jid, val);
          }
        }
      }
    }
    const repliesFile = path.join(__dirname, 'respostas_fornecedores.json');
    if (fs.existsSync(repliesFile)) {
      const replies = readJsonSafe(repliesFile);
      if (replies && typeof replies === 'object') {
        for (const item of Object.values(replies)) {
          const digits = String(item?.telefone || '').replace(/\D/g, '');
          const msgId = String(item?.message_id || '').trim();
          const ts = Number(item?.timestamp || 0);
          if (!digits || !msgId || ts <= 0) continue;
          const jid = `${digits}@s.whatsapp.net`;
          const prevTs = Number(realMessageAnchorsMap.get(jid)?.messageTimestamp || 0);
          if (ts >= prevTs) {
            realMessageAnchorsMap.set(jid, {
              key: { remoteJid: jid, id: msgId, fromMe: false },
              messageTimestamp: ts
            });
          }
        }
      }
    }
    const histFile = path.join(__dirname, 'historico_envios.json');
    if (fs.existsSync(histFile)) {
      const hist = readJsonSafe(histFile);
      if (hist && typeof hist === 'object') {
        for (const item of Object.values(hist)) {
          const digits = String(item?.telefone || '').replace(/\D/g, '');
          const jid = String(item?.jid || (digits ? `${digits}@s.whatsapp.net` : '')).trim();
          const msgId = String(item?.message_id || '').trim();
          const ts = Number(item?.tentativa_timestamp || 0);
          if (!jid || !msgId) continue;
          if (!realMessageAnchorsMap.has(jid)) {
            realMessageAnchorsMap.set(jid, {
              key: { remoteJid: jid, id: msgId, fromMe: true },
              messageTimestamp: ts > 0 ? Math.floor(ts) : Math.floor(Date.now() / 1000)
            });
          }
        }
      }
    }
  } catch {}
}

function saveRealAnchorFromRange(jid, rangeObj) {
  if (!jid || !rangeObj) return;
  const msgs = rangeObj.messages || [];
  for (const item of msgs) {
    const k = item?.key || item?.message?.key;
    const ts = Number(item?.messageTimestamp || item?.message?.messageTimestamp || rangeObj.lastMessageTimestamp || 0);
    if (k && k.id && !String(k.id).startsWith('ARCHIVE_')) {
      const nextTs = ts || Math.floor(Date.now() / 1000);
      const prevTs = Number(realMessageAnchorsMap.get(jid)?.messageTimestamp || 0);
      if (nextTs >= prevTs) {
        realMessageAnchorsMap.set(jid, {
          key: {
            remoteJid: jid,
            id: k.id,
            fromMe: !!k.fromMe,
            participant: k.participant || undefined
          },
          messageTimestamp: nextTs
        });
        saveRealMessageAnchors();
      }
      if (pendingArchiveCommunityJids.has(jid) && jid.endsWith('@g.us')) {
        void archiveChatJid(jid);
      }
      return;
    }
  }
}

const NEVER_ARCHIVE_JIDS = new Set(['120363411941364864@g.us']);

function isNeverArchiveGroup(jid, name = '') {
  if (NEVER_ARCHIVE_JIDS.has(jid)) return true;
  const cleanName = String(name || groupSubjectCache.get(jid) || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  return cleanName.includes('quarteto fantastico');
}

function registerMonitoredArchivedChat(jid, info = {}) {
  if (!jid || jid === 'status@broadcast') return;
  const isGroup = jid.endsWith('@g.us');
  const isNewsletter = jid.endsWith('@newsletter');
  if (isGroup && isNeverArchiveGroup(jid, info.name)) {
    monitoredArchivedMap.delete(jid);
    return;
  }
  if (isGroup) autoArchiveJids.add(jid);
  const resolvedJid = (isGroup || isNewsletter) ? jid : resolveLidJid(jid);
  const existing = monitoredArchivedMap.get(jid) || {};
  const name =
    info.name ||
    existing.name ||
    groupSubjectCache.get(jid) ||
    contactNameCache.get(jid) ||
    contactNameCache.get(resolvedJid) ||
    resolvedJid.split('@')[0];

  // NÃO registrar grupos com nomes banidos ou ifindz
  if (!isSafeGroupName(name, jid)) {
    monitoredArchivedMap.delete(jid);
    return;
  }

  // Se for grupo, monitora se for comunidade/grupo de fornecedor permitido no escopo
  if (isGroup) {
    const inJoined = Array.from(joinedCommunitiesMap.values()).some(
      item => ['ENTROU', 'ENTROU_E_ARQUIVOU'].includes(item.status) && (item.groupJid === jid || item.linkedParent === jid)
    );
    const scopeAllowed = !isInviteBlocked(name, jid) && projectInviteDecision(name, { knownSupplier: true, blocked: false }).allowed;
    if (!isKnownSupplierJid(jid) && !inJoined && !scopeAllowed) {
      monitoredArchivedMap.delete(jid);
      return;
    }
  }

  // Se for conversa individual, monitora se for dos fornecedores conhecidos
  if (!isGroup && !isNewsletter && !isKnownSupplierJid(jid) && !isKnownSupplierJid(resolvedJid)) {
    monitoredArchivedMap.delete(jid);
    return;
  }

  const defaultType = isNewsletter
    ? 'Canal do WhatsApp'
    : (isGroup ? 'Comunidade Fornecedor' : 'Contato Fornecedor (Privado)');

  const record = {
    jid,
    name,
    type: info.type || existing.type || defaultType,
    lastActivity: info.lastActivity || existing.lastActivity || new Date().toLocaleString('pt-BR'),
    productsCaptured: (existing.productsCaptured || 0) + (info.addProducts || 0),
    status: isGroup ? 'MONITORANDO ARQUIVADO' : 'MONITORANDO ATIVO'
  };
  monitoredArchivedMap.set(jid, record);
}

function saveMonitoredArchived() {
  try {
    const arr = Array.from(monitoredArchivedMap.values()).filter(a => isSafeGroupName(a.name, a.jid));
    writeJsonAtomic(archivedMonitoredFile, arr);
  } catch {}
}

function loadMonitoredArchived() {
  try {
    if (fs.existsSync(archivedMonitoredFile)) {
      const arr = readJsonSafe(archivedMonitoredFile);
      if (Array.isArray(arr)) {
        for (const item of arr) {
          if (item && item.jid) {
            monitoredArchivedMap.set(item.jid, item);
            autoArchiveJids.add(item.jid);
            if (item.name) {
              if (item.jid.endsWith('@g.us')) groupSubjectCache.set(item.jid, item.name);
              else contactNameCache.set(item.jid, item.name);
            }
          }
        }
      }
    }
  } catch {}
}

function loadKnownSuppliers() {
  try {
    if (fs.existsSync(suppliersFile)) {
      const modified = fs.statSync(suppliersFile).mtimeMs;
      if (modified === suppliersFileMtime) return;
      const arr = readJsonSafe(suppliersFile);
      if (Array.isArray(arr)) {
        knownSupplierDigits.clear();
        suppliersFileMtime = modified;
        for (const s of arr) {
          const sName = String(s.nome || '').trim();
          for (const v of normalizePhoneDigits(s.telefone)) {
            knownSupplierDigits.add(v);
            const sJid = `${v}@s.whatsapp.net`;
            autoArchiveJids.add(sJid);
            if (sName) contactNameCache.set(sJid, sName);
          }
        }
        addLog(`[LISTA] ${arr.length} fornecedores carregados na memória para identificação e auto-arquivamento.`);
      }
    }
  } catch (err) {
    addLog(`Erro ao carregar fornecedores: ${err.message}`);
  }
}

function isKnownSupplierJid(jid) {
  if (!jid) return false;
  const userPart = jid.split('@')[0].split(':')[0];
  for (const v of normalizePhoneDigits(userPart)) {
    if (knownSupplierDigits.has(v)) return true;
  }
  return false;
}

function isKnownOrArchivedJid(jid) {
  if (!jid) return false;
  if (autoArchiveJids.has(jid) || monitoredArchivedMap.has(jid)) return true;
  const userPart = jid.split('@')[0].split(':')[0];
  for (const v of normalizePhoneDigits(userPart)) {
    if (knownSupplierDigits.has(v)) return true;
  }
  return false;
}

function loadJoinedCommunities() {
  try {
    if (fs.existsSync(communitiesFile)) {
      const arr = readJsonSafe(communitiesFile);
      if (Array.isArray(arr)) {
        for (const item of arr) {
          const key = item.code || item.groupJid;
          if (key) joinedCommunitiesMap.set(key, item);
          if (item.groupJid && ['ENTROU','ENTROU_E_ARQUIVOU'].includes(item.status)) {
            autoArchiveJids.add(item.groupJid);
            if (item.groupName) groupSubjectCache.set(item.groupJid, item.groupName);
            registerMonitoredArchivedChat(item.groupJid, {
              name: item.groupName,
              type: 'Comunidade / Grupo'
            });
          }
          if (item.linkedParent) autoArchiveJids.add(item.linkedParent);
        }
      }
    }
  } catch (err) {
    addLog(`Erro ao carregar comunidades salvas: ${err.message}`);
  }
}

const GROUP_BLACKLIST = [
  'universo feminino', 'miga', 'desapeg', 'feira do rolo', 'compra e venda',
  'compras e vendas', 'doacao', 'brecho', 'ifindz', 'divulga', 'brechó', 'doação',
  'bazar', 'renda extra', 'afiliado', 'cassino', 'tigre', 'aposta', 'bet'
];

function isSafeGroupName(name, jid) {
  const n = (name || '').toLowerCase();
  const j = (jid || '').toLowerCase();
  if (j.includes('status@broadcast')) return false;
  return !GROUP_BLACKLIST.some(b => n.includes(b) || j.includes(b));
}

function saveJoinedCommunities() {
  try {
    const arr = Array.from(joinedCommunitiesMap.values()).filter(c => isSafeGroupName(c.groupName || c.link, c.groupJid || c.senderJid));
    writeJsonAtomic(communitiesFile, arr);
  } catch (err) {
    addLog(`Erro ao salvar comunidades: ${err.message}`);
  }
}

loadKnownSuppliers();
loadMonitoredArchived();
loadJoinedCommunities();
loadRealMessageAnchors();

const pendingArchiveCommunityJids = new Set();
const archiveInFlight = new Map();
const archivedMessageIds = new Map();

// Arquiva SOMENTE comunidades e grupos (@g.us) permitidos e registra no Monitor — nunca arquiva chats individuais nem Quarteto Fantástico
async function archiveChatJid(chatJid, reason = '', customName = '', customType = '', forceFakeIfNoMsg = false) {
  if (!waSocket || !chatJid || chatJid === 'status@broadcast') return false;
  if (!chatJid.endsWith('@g.us')) return false;
  if (typeof isNeverArchiveGroup === 'function' && isNeverArchiveGroup(chatJid, customName)) return false;
  if (!isSafeGroupName(customName || groupSubjectCache.get(chatJid) || '', chatJid) || isInviteBlocked('', chatJid)) return false;
  if (archiveInFlight.has(chatJid)) {
    try { await archiveInFlight.get(chatJid); return true; }
    catch { return false; }
  }
  registerMonitoredArchivedChat(chatJid, {
    name: customName,
    type: customType
  });
  saveMonitoredArchived();

  const nowSec = Math.floor(Date.now() / 1000);
  const lastMsg = chatLastMsgMap.get(chatJid) || realMessageAnchorsMap.get(chatJid);

  if (lastMsg?.key?.id && !String(lastMsg.key.id).startsWith("ARCHIVE_") && Number(lastMsg.messageTimestamp) > 0) {
    if (archivedMessageIds.get(chatJid) === lastMsg.key.id) return true;
    try {
      const safeMsg = {
        key: {
          remoteJid: chatJid,
          id: lastMsg.key.id,
          fromMe: !!lastMsg.key.fromMe,
          ...(lastMsg.key.participant ? { participant: lastMsg.key.participant } : {})
        },
        messageTimestamp: Number(lastMsg.messageTimestamp || nowSec)
      };
      const request = waSocket.chatModify({ archive: true, lastMessages: [safeMsg] }, chatJid);
      archiveInFlight.set(chatJid, request);
      await request;
      archivedMessageIds.set(chatJid, lastMsg.key.id);
      if (typeof pendingArchiveCommunityJids !== 'undefined') pendingArchiveCommunityJids.delete(chatJid);
      if (reason) addLog(`[ARQUIVO] Arquivado e monitorado (${chatJid}) ${reason}`);
      return true;
    } catch (error) {
      addLog(`[ARQUIVO] Arquivamento adiado: ${error.message}`);
    } finally { archiveInFlight.delete(chatJid); }
  }

  // Sem âncora real ainda: marca para arquivar automaticamente assim que a primeira mensagem/âncora sincronizar
  if (typeof pendingArchiveCommunityJids !== 'undefined') pendingArchiveCommunityJids.add(chatJid);
  return false;
}

// Solicita ao WhatsApp do celular o histórico de mensagens de um contato ou comunidade arquivada
async function requestOnDemandHistoryForChat(jid, count = 45) {
  if (!waSocket || connectionStatus !== 'CONNECTED' || !jid) return false;
  const anchor = realMessageAnchorsMap.get(jid) || chatLastMsgMap.get(jid);
  if (!anchor || !anchor.key || !anchor.key.id || String(anchor.key.id).startsWith('ARCHIVE_')) {
    return false;
  }
  try {
    const tsSec = Number(anchor.messageTimestamp || Math.floor(Date.now() / 1000));
    const tsMs = tsSec < 20000000000 ? tsSec * 1000 : tsSec;
    await waSocket.fetchMessageHistory(count, anchor.key, tsMs);
    if (typeof waSocket.requestPlaceholderResend === 'function') {
      void waSocket.requestPlaceholderResend(anchor.key).catch(() => {});
    }
    return true;
  } catch {
    return false;
  }
}

// Desarquiva conversas individuais ou grupos protegidos (ex: Quarteto Fantástico) no WhatsApp
async function unarchiveChatJid(chatJid) {
  if (!waSocket || connectionStatus !== 'CONNECTED' || !chatJid || chatJid === 'status@broadcast') return false;
  if (chatJid.endsWith('@g.us') && isNeverArchiveGroup(chatJid)) {
    monitoredArchivedMap.delete(chatJid);
  }
  if (typeof pendingArchiveCommunityJids !== 'undefined') pendingArchiveCommunityJids.delete(chatJid);
  const nowSec = Math.floor(Date.now() / 1000);
  const lastMsg = chatLastMsgMap.get(chatJid) || realMessageAnchorsMap.get(chatJid);
  const safeMsg = (lastMsg?.key?.id && !String(lastMsg.key.id).startsWith('ARCHIVE_') && Number(lastMsg.messageTimestamp) > 0)
    ? {
        key: {
          remoteJid: chatJid,
          id: lastMsg.key.id,
          fromMe: !!lastMsg.key.fromMe,
          ...(lastMsg.key.participant ? { participant: lastMsg.key.participant } : {})
        },
        messageTimestamp: Number(lastMsg.messageTimestamp || nowSec)
      }
    : {
        key: {
          remoteJid: chatJid,
          id: `UNARCHIVE_${nowSec}`,
          fromMe: true
        },
        messageTimestamp: nowSec
      };
  try {
    await waSocket.chatModify({ archive: false, lastMessages: [safeMsg] }, chatJid);
    return true;
  } catch {
    return false;
  }
}

// Vistoria completa: sincroniza todos os contatos/grupos arquivados no aparelho e puxa o histórico de mensagens deles
let activeLeaveBlockedMembership = null;

async function deepScanAllArchivedAndCommunities() {
  const generation = socketGeneration;
  if (scanGeneration === generation) return { ok: true, inProgress: true };
  scanGeneration = generation;
  try { return await scanArchivedAndCommunities(generation); }
  finally { if (scanGeneration === generation) scanGeneration = null; }
}

async function scanArchivedAndCommunities(generation) {
  if (!waSocket || connectionStatus !== 'CONNECTED') return { ok: false, error: 'WhatsApp não conectado' };
  const socket = waSocket;
  const active = () => generation === socketGeneration && connectionStatus === 'CONNECTED' && !stopping;
  const nowBr = getBrasiliaTimeParts(new Date());
  lastDeepScanAt = Date.now();
  lastDeepScanDay = nowBr.dayKey;
  if (nowBr.hour >= 6 && nowBr.hour < 18) {
    lastWindowWakeDay = nowBr.dayKey;
    lastNewsletterSyncAt = Date.now();
  }
  addLog('[BUSCA] Iniciando VISTORIA COMPLETA nos Contatos Privados, Canais, Comunidades e Grupos Arquivados...');

  loadKnownSuppliers();
  loadRealMessageAnchors();
  saveRealMessageAnchors(true);

  // Registrar todos os contatos privados de fornecedores com âncora no monitoramento (sem arquivá-los)
  for (const [jid] of realMessageAnchorsMap.entries()) {
    if (!jid.endsWith('@g.us') && isKnownSupplierJid(jid)) {
      registerMonitoredArchivedChat(jid, {
        name: contactNameCache.get(jid) || jid.split('@')[0],
        type: 'Contato Fornecedor (Privado)'
      });
    }
  }
  saveMonitoredArchived();

  // Synchronize without deleting authentication/app-state files or leaving groups.
  try {
    await socket.resyncAppState(['regular_low', 'regular_high', 'critical_unblock_low'], false);
    if (realMessageAnchorsMap.size < 20) {
      if (!active()) return { ok: false, interrupted: true };
      await socket.resyncAppState(['regular_low', 'regular_high'], true);
    }
  } catch (err) { addLog(`Aviso ao sincronizar conversas: ${err.message}`); }

  // 2. Garantir que todas as comunidades/grupos monitorados (@g.us) fiquem arquivados (nunca arquivar chats individuais)
  let archivedCommunitiesCount = 0;
  try {
    if (!active()) return { ok: false, interrupted: true };
    const groupsObj = await socket.groupFetchAllParticipating();
    const hasBlocklist = fs.existsSync(path.join(__dirname, 'convites_bloqueados.json'));
    for (const g of Object.values(groupsObj || {})) {
      if (!active()) return { ok: false, interrupted: true };
      if (!g?.id || !g.id.endsWith('@g.us')) continue;
      if (hasBlocklist && typeof activeLeaveBlockedMembership === 'function') {
        await activeLeaveBlockedMembership(g.id);
      }
      if (isInviteBlocked(g.subject || '', g.id)) continue;
      if (isNeverArchiveGroup(g.id, g.subject || '')) {
        await unarchiveChatJid(g.id);
        continue;
      }
      const decision = projectInviteDecision(g.subject || '', { knownSupplier: isKnownSupplierJid(g.id), blocked: false });
      if (monitoredArchivedMap.has(g.id) || g.isCommunity || g.isCommunityAnnounce || decision.allowed) {
        const ok = await archiveChatJid(g.id, '', g.subject || '', g.isCommunity ? 'Comunidade' : 'Comunidade / Grupo');
        if (ok) {
          archivedCommunitiesCount++;
          await new Promise((r) => setTimeout(r, 250));
        }
      }
    }
  } catch (err) { addLog(`Aviso ao arquivar comunidades: ${err.message}`); }

  // 2.5. Sincronizar Canais do WhatsApp (@newsletter) monitorados (ex: Capitão Cel)
  for (const [jid] of monitoredArchivedMap.entries()) {
    if (!active()) return { ok: false, interrupted: true };
    if (jid.endsWith('@newsletter')) {
      try {
        if (!subscribedNewsletters.has(jid) && typeof waSocket.subscribeNewsletterUpdates === 'function') {
          await waSocket.subscribeNewsletterUpdates(jid).catch(() => {});
          subscribedNewsletters.add(jid);
        }
        if (typeof waSocket.newsletterFetchMessages === 'function') {
          await waSocket.newsletterFetchMessages(jid, 50).catch(() => {});
        }
      } catch {}
    }
  }

  // 3. Solicitar histórico sob demanda (fetchMessageHistory) de todos os arquivados, comunidades e privados com âncora de mensagem
  let requestedCount = 0;
  for (const [jid] of realMessageAnchorsMap.entries()) {
    if (!active()) return { ok: false, interrupted: true };
    const ok = await requestOnDemandHistoryForChat(jid, 50);
    if (ok) {
      requestedCount++;
      await new Promise((r) => setTimeout(r, 250));
    }
  }

  addLog(`[VISTORIA] Vistoria: ${monitoredArchivedMap.size} canais/privados/comunidades mapeados, ${archivedCommunitiesCount} comunidades arquivadas e ${requestedCount} pedidos de histórico enviados.`);
  return {
    ok: true,
    monitoredCount: monitoredArchivedMap.size,
    anchorsCount: realMessageAnchorsMap.size,
    historyRequestsSent: requestedCount
  };
}

function extractInviteCodesFromMessage(msgObj) {
  const codes = new Set();
  function visit(value, key = '') {
    if (['quotedMessage', 'thumbnail', 'jpegThumbnail', 'mediaKey', 'messageSecret'].includes(key)) return;
    if (typeof value === 'string') {
      const regex = /(?:^|[^A-Za-z0-9.\/-])(?:https?:\/\/)?chat\.whatsapp\.com\/(?:invite\/)?([A-Za-z0-9]{20,26})(?![A-Za-z0-9])/gi;
      for (const match of value.matchAll(regex)) codes.add(match[1]);
    } else if (value && typeof value === 'object' && !Buffer.isBuffer(value)) {
      if (value.groupInviteMessage?.inviteCode && /^[A-Za-z0-9]{20,26}$/.test(value.groupInviteMessage.inviteCode)) codes.add(value.groupInviteMessage.inviteCode);
      for (const [child, content] of Object.entries(value)) visit(content, child);
    }
  }
  visit(msgObj);
  return [...codes];
}

function findNativeGroupInvite(obj) {
  if (!obj || typeof obj !== 'object') return null;
  if (obj.groupInviteMessage && obj.groupInviteMessage.inviteCode) {
    return obj.groupInviteMessage;
  }
  for (const k of Object.keys(obj)) {
    const found = findNativeGroupInvite(obj[k]);
    if (found) return found;
  }
  return null;
}

// Extrai o texto completo de qualquer tipo de mensagem
function extractPlainTextFromMessage(m) {
  if (!m || typeof m !== 'object') return '';
  for (const wrapper of [
    'ephemeralMessage',
    'viewOnceMessage',
    'viewOnceMessageV2',
    'documentWithCaptionMessage',
    'editedMessage',
    'associatedChildMessage',
    'groupMentionedMessage',
    'commentMessage',
    'botInvokeMessage'
  ]) {
    if (m[wrapper]?.message) return extractPlainTextFromMessage(m[wrapper].message);
  }
  if (m.protocolMessage?.editedMessage) {
    return extractPlainTextFromMessage(m.protocolMessage.editedMessage);
  }

  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.documentMessage?.caption ||
    m.buttonsMessage?.contentText ||
    m.templateMessage?.hydratedTemplate?.hydratedContentText ||
    ''
  );
}

async function getGroupDetails(groupJid) {
  if (!groupJid || !groupJid.endsWith('@g.us')) return { subject: '', isCommunity: false };
  if (groupMetaCache.has(groupJid)) {
    const meta = groupMetaCache.get(groupJid);
    return {
      subject: meta.subject || groupSubjectCache.get(groupJid) || '',
      isCommunity: !!(meta.isCommunity || meta.isCommunityAnnounce || meta.linkedParent)
    };
  }
  if (!waSocket) return { subject: groupSubjectCache.get(groupJid) || '', isCommunity: false };
  try {
    const meta = await withTimeout(waSocket.groupMetadata(groupJid), 8000, 'Metadados do grupo indisponíveis');
    if (meta) {
      groupMetaCache.set(groupJid, meta);
      if (meta.subject) groupSubjectCache.set(groupJid, meta.subject);
      return {
        subject: meta.subject || '',
        isCommunity: !!(meta.isCommunity || meta.isCommunityAnnounce || meta.linkedParent)
      };
    }
  } catch {}
  return { subject: groupSubjectCache.get(groupJid) || '', isCommunity: false };
}

function hasImageInMessage(message) {
  const content = baileysModule.extractMessageContent(message?.protocolMessage?.editedMessage || message);
  return Boolean(content?.imageMessage || /^image\//i.test(content?.documentMessage?.mimetype || ''));
}

const brDateTimeFormatter = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23'
});

function getBrasiliaTimeParts(date = new Date()) {
  const parts = brDateTimeFormatter.formatToParts(date);
  const pick = type => parts.find(p => p.type === type)?.value || '00';
  const day = pick('day');
  const month = pick('month');
  const year = pick('year');
  const hour = parseInt(pick('hour'), 10);
  const minute = parseInt(pick('minute'), 10);
  return { dayKey: `${year}-${month}-${day}`, day, month, year, hour, minute };
}

async function forwardMessageToProductParser(msg, { isEdited = false, editTimestamp = 0 } = {}) {
  try {
    if (!msg || !msg.message) return false;
    const text = extractPlainTextFromMessage(msg.message);
    const hasImg = hasImageInMessage(msg.message);
    if ((!text || text.trim().length < 8) && !hasImg) return false;

    const remoteJid = msg.key?.remoteJid || '';
    if (remoteJid === 'status@broadcast') return false;

    const isGroup = remoteJid.endsWith('@g.us');
    const isNewsletter = remoteJid.endsWith('@newsletter');
    const isBroadcast = !!(msg.broadcast || msg.key?.remoteJid?.endsWith('@broadcast'));
    const candA = resolveLidJid(msg.key?.participantAlt || '');
    const candB = resolveLidJid(msg.key?.participant || '');
    const participantJid = isGroup
      ? (isKnownSupplierJid(candA) ? candA : (isKnownSupplierJid(candB) ? candB : (candA && !candA.endsWith('@lid') ? candA : (candB || candA))))
      : resolveLidJid(msg.key?.senderPn || msg.key?.remoteJidAlt || remoteJid);
    const resolvedRemoteJid = (isGroup || isNewsletter) ? remoteJid : resolveLidJid(remoteJid);

    // Bloqueio rigoroso: ifindz
    const groupSubject = (isGroup || isNewsletter) ? (groupSubjectCache.get(remoteJid) || monitoredArchivedMap.get(remoteJid)?.name || '') : '';
    if (groupSubject.toLowerCase().includes('ifindz') || remoteJid.toLowerCase().includes('ifindz')) {
      return false;
    }

    const joinedCommunity = isGroup
      ? Array.from(joinedCommunitiesMap.values()).find(
          item => ['ENTROU', 'ENTROU_E_ARQUIVOU'].includes(item.status) && (item.groupJid === remoteJid || item.linkedParent === remoteJid)
        )
      : null;
    const isMonitoredSupplierGroup =
      isGroup &&
      (!!joinedCommunity || monitoredArchivedMap.has(remoteJid)) &&
      !isNeverArchiveGroup(remoteJid, groupSubject) &&
      !isInviteBlocked('', remoteJid);

    const looksLikePrivateAppleList =
      !isGroup &&
      (/(?:iphone|ipad|macbook|imac|mac\s*(?:mini|studio|pro|air|neo)|apple\s*watch|watch\s*(?:ultra|s\d+|se)|airpods|apple\s*pencil|magic\s*(?:mouse|keyboard|trackpad)|airtag)/i.test(text || '') || hasImg);

    // Processar mensagens dos 204 fornecedores (inclusive no privado), canais (@newsletter) ou comunidades/grupos monitorados
    const isSupplier =
      isKnownSupplierJid(remoteJid) ||
      isKnownSupplierJid(participantJid) ||
      isMonitoredSupplierGroup ||
      isNewsletter ||
      looksLikePrivateAppleList;
    if (!isSupplier) {
      return false;
    }

    const effectiveParticipantJid = (!isKnownSupplierJid(participantJid) && joinedCommunity?.senderJid)
      ? resolveLidJid(joinedCommunity.senderJid)
      : participantJid;

    // Regra Estrita de Horário: Apenas mensagens do dia de HOJE entre 06:00 e 18:00 (Brasília)
    const edited = isEdited || !!msg.message?.editedMessage || !!msg.message?.protocolMessage?.editedMessage;
    const effectiveTs = supplierEventTime(msg.message, msg.messageTimestamp, editTimestamp);
    if (!effectiveTs) {
      addLog('[CAPTURA] Mensagem sem horário de origem; preço preservado.');
      return false;
    }
    const msgBr = getBrasiliaTimeParts(new Date(effectiveTs * 1000));
    const nowBr = getBrasiliaTimeParts(new Date());

    const isToday = msgBr.dayKey === nowBr.dayKey;
    if (!isToday || msgBr.hour < 6 || msgBr.hour >= 18) {
      return false;
    }

    const savedContactName =
      contactNameCache.get(remoteJid) ||
      contactNameCache.get(resolvedRemoteJid) ||
      contactNameCache.get(participantJid) ||
      '';
  const pushName = msg.pushName || savedContactName || '';
    if (pushName && !isGroup) {
      contactNameCache.set(remoteJid, pushName);
      contactNameCache.set(resolvedRemoteJid, pushName);
    }

    let groupName = '';
    let channelType = '';
    if (isGroup) {
      const details = await getGroupDetails(remoteJid);
      channelType = details.isCommunity ? 'Comunidade Arquivada' : 'Grupo Arquivado';
      groupName = details.subject || groupSubjectCache.get(remoteJid) || joinedCommunity?.groupName || monitoredArchivedMap.get(remoteJid)?.name || channelType;
    } else if (isNewsletter) {
      channelType = 'Canal do WhatsApp';
      groupName = groupSubjectCache.get(remoteJid) || monitoredArchivedMap.get(remoteJid)?.name || pushName || savedContactName || 'Canal Fornecedor';
    } else if (isBroadcast) {
      channelType = 'Lista de Transmissão Arquivada';
      groupName = pushName || savedContactName || '';
    } else {
      channelType = 'Contato Fornecedor (Privado)';
      groupName = pushName || savedContactName || '';
    }

    const editedContent = edited && hasImg
      ? baileysModule.extractMessageContent(msg.message.protocolMessage?.editedMessage || msg.message)
      : null;
    const mediaHash = editedContent?.imageMessage?.fileSha256 || editedContent?.documentMessage?.fileSha256;
    const editKey = edited
      ? createHash('sha256').update(String(text || '')).update(mediaHash || '').digest('hex').slice(0, 16)
      : '';

    const payload = {
      messageId: msg.message?.protocolMessage?.key?.id || msg.key?.id || '',
      ...(editKey ? { editKey } : {}),
      text: text || '',
      imageBase64: '',
      ...(hasImg ? { mediaMessage: JSON.stringify(msg, BufferJSON.replacer) } : {}),
      remoteJid: resolvedRemoteJid,
      participantJid: effectiveParticipantJid,
      pushName: pushName || savedContactName,
      groupName,
      channelType,
      isGroup,
      timestamp: effectiveTs
    };

    return captureQueue.enqueue(payload);
  } catch (error) { addLog(`[CAPTURA] Falha ao preparar lista: ${error.message}`); }
  return false;
}

function parseManualInvite(input) {
  const raw = String(input || '').trim();
  if (/^[A-Za-z0-9]{20,26}$/.test(raw)) return raw;
  try {
    const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
    if (url.hostname !== 'chat.whatsapp.com' || !['https:', 'http:'].includes(url.protocol)) return null;
    const match = url.pathname.match(/^\/(?:invite\/)?([A-Za-z0-9]{20,26})\/?$/);
    return match ? match[1] : null;
  } catch { return null; }
}

function inviteErrorMessage(error) {
  const code = Number(error?.output?.statusCode || error?.statusCode);
  if (code === 401) return 'A sessão do WhatsApp foi desconectada. Conecte novamente pelo QR Code.';
  if (code === 403) return 'O WhatsApp recusou a entrada. O grupo pode exigir aprovação do administrador ou ter uma restrição para este número.';
  if (code === 404 || code === 410) return 'O convite expirou, foi revogado ou não existe. Peça um novo link ao administrador.';
  if (code === 429) return 'O WhatsApp limitou as tentativas. Aguarde antes de tentar novamente.';
  return String(error?.message || 'Não foi possível entrar pelo convite.');
}

function isInviteBlocked(code, groupJid) {
  const file = path.join(__dirname, 'convites_bloqueados.json');
  if (!fs.existsSync(file)) return false;
  const records = readJsonSafe(file);
  if (!Array.isArray(records)) throw new Error('Bloqueios de convites inválidos; entrada impedida.');
  return records.some(item => item.code === code || (groupJid && item.groupJid === groupJid));
}

async function processInviteCode(code, senderJid = 'Desconhecido', senderName = '') {
  if (!code || !waSocket || connectionStatus !== 'CONNECTED') return null;
  const existing = joinedCommunitiesMap.get(code);
  if (existing && ['ENTROU', 'ENTROU_E_ARQUIVOU', 'AGUARDANDO_APROVACAO'].includes(existing.status)) return existing;
  if (pendingInviteCodes.has(code)) return null;
  pendingInviteCodes.add(code);
  let groupName = 'Comunidade / Grupo Fornecedor';
  let groupJid = '';
  let linkedParent = '';
  let requiresApproval = false;
  try {
    const info = await waSocket.groupGetInviteInfo(code);
    groupName = info?.subject || groupName;
    groupJid = info?.id || '';
    linkedParent = info?.linkedParent || '';
    requiresApproval = !!info?.joinApprovalMode;
    const scope = projectInviteDecision(groupName, {
      knownSupplier: isKnownSupplierJid(senderJid), blocked: isInviteBlocked(code, groupJid)
    });
    if (!scope.allowed) {
      const record = { code, link: `https://chat.whatsapp.com/${code}`, groupJid, groupName,
        senderJid, senderName, status: scope.status, error: scope.reason, retryable: false };
      joinedCommunitiesMap.set(code, record);
      saveJoinedCommunities();
      addLog(`[CONVITE] Entrada impedida em "${groupName}": ${scope.reason}`);
      return record;
    }
    let joinedJid;
    try {
      joinedJid = await waSocket.groupAcceptInvite(code);
    } catch (error) {
      const status = Number(error?.output?.statusCode || error?.statusCode);
      // A conflict is only a success when WhatsApp confirms current membership.
      if (status !== 409 || !groupJid) throw error;
      const metadata = await waSocket.groupMetadata(groupJid);
      const ownIds = [waSocket.user?.id, waSocket.user?.lid].filter(Boolean).map(id => id.replace(/:\d+@/, '@'));
      if (!metadata.participants?.some(participant => [participant.id, participant.phoneNumber, participant.lid].some(id => id && ownIds.includes(id.replace(/:\d+@/, '@'))))) throw error;
      joinedJid = groupJid;
    }
    let status = 'ENTROU';
    if (!joinedJid) {
      if (!requiresApproval) throw new Error('O WhatsApp não confirmou a entrada.');
      status = 'AGUARDANDO_APROVACAO';
    } else {
      groupJid = joinedJid;
      if (requiresApproval) {
        try {
          const metadata = await waSocket.groupMetadata(groupJid);
          const ownIds = [waSocket.user?.id, waSocket.user?.lid].filter(Boolean).map(id => id.replace(/:\d+@/, '@'));
          if (!metadata.participants?.some(participant => [participant.id, participant.phoneNumber, participant.lid].some(id => id && ownIds.includes(id.replace(/:\d+@/, '@'))))) status = 'AGUARDANDO_APROVACAO';
        } catch { status = 'AGUARDANDO_APROVACAO'; }
      }
    }
    const record = { code, link: `https://chat.whatsapp.com/${code}`, groupJid, linkedParent,
      groupName, senderJid, senderName: senderName || senderJid.split('@')[0], status,
      joinedAt: new Date().toLocaleString('pt-BR') };
    joinedCommunitiesMap.set(code, record);
    saveJoinedCommunities();
    if (status === 'ENTROU') {
      // Membership is saved before registration so this newly joined group is monitored.
      registerMonitoredArchivedChat(groupJid, { name: groupName, type: 'Grupo / Comunidade Fornecedor' });
      groupSubjectCache.set(groupJid, groupName);
      saveMonitoredArchived();
      if (await archiveChatJid(groupJid, '', groupName, 'Grupo / Comunidade Fornecedor')) {
        record.status = 'ENTROU_E_ARQUIVOU';
        saveJoinedCommunities();
      }
      if (linkedParent) {
        await archiveChatJid(linkedParent, '', `${groupName} (Comunidade)`, 'Comunidade');
      }
      addLog(`[CONVITE] Entrada confirmada em "${groupName}". Captura de listas ativa.`);
    } else addLog(`[CONVITE] "${groupName}" aguarda aprovação do administrador.`);
    return record;
  } catch (error) {
    const errorCode = Number(error?.output?.statusCode || error?.statusCode) || 0;
    const record = { code, link: `https://chat.whatsapp.com/${code}`, groupJid, groupName,
      senderJid, senderName, status: 'ERRO', error: inviteErrorMessage(error), errorCode,
      retryable: ![400, 401, 403, 404, 405, 406, 409, 410, 463].includes(errorCode), joinedAt: new Date().toLocaleString('pt-BR') };
    joinedCommunitiesMap.set(code, record);
    saveJoinedCommunities();
    addLog(`[CONVITE] ${groupName}: ${record.error}`);
    return record;
  } finally { pendingInviteCodes.delete(code); }
}

const inviteQueue = new InviteQueue({
  file: path.join(__dirname, 'fila_convites.json'),
  execute: item => processInviteCode(item.code, item.senderJid, item.senderName),
  connected: () => AUTOMATIC_INVITES_ENABLED && connectionStatus === 'CONNECTED' && !stopping && !AUTOMATION_PAUSED,
  log: addLog
});

function enqueueSupplierInvites(msg) {
  loadKnownSuppliers();
  if (msg.key?.fromMe) return;
  const rawSender = msg.key?.participantAlt || msg.key?.participant || msg.key?.remoteJidAlt || msg.key?.remoteJid || '';
  const senderJid = resolveLidJid(rawSender);
  const altRemote = resolveLidJid(msg.key?.remoteJid || '');
  const matchedJid = isKnownSupplierJid(senderJid) ? senderJid : (isKnownSupplierJid(altRemote) ? altRemote : '');
  if (!matchedJid) return;
  for (const code of extractInviteCodesFromMessage(msg.message)) {
    const existing = joinedCommunitiesMap.get(code);
    if (existing && ['ENTROU', 'ENTROU_E_ARQUIVOU', 'AGUARDANDO_APROVACAO'].includes(existing.status)) continue;
    const sName = msg.pushName || contactNameCache.get(matchedJid) || contactNameCache.get(rawSender) || 'Fornecedor';
    if (inviteQueue.enqueue({ code, senderJid: matchedJid, senderName: sName })) {
      addLog(`[CONVITE] Convite ${code} de ${sName} (${matchedJid}) colocado na fila.`);
    }
  }
}

function isSupplierReplyContent(message) {
  if (!message || typeof message !== 'object') return false;
  for (const wrapper of ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'documentWithCaptionMessage']) {
    if (message[wrapper]?.message) return isSupplierReplyContent(message[wrapper].message);
  }
  return !!extractPlainTextFromMessage(message).trim() || ['imageMessage','audioMessage','videoMessage','documentMessage','stickerMessage','contactMessage','contactsArrayMessage','locationMessage','liveLocationMessage','reactionMessage','buttonsResponseMessage','listResponseMessage'].some(type => !!message[type]);
}

async function inspectIncomingMessage(msg, options = {}) {
  if (!msg || !msg.message) return;
  lastReceivedAt = Date.now();
  const remoteJid = msg.key?.remoteJid || '';
  if (!remoteJid || remoteJid === 'status@broadcast') return;
  const pushName = msg.pushName || contactNameCache.get(remoteJid) || '';
  if (pushName && !remoteJid.endsWith('@g.us')) {
    contactNameCache.set(remoteJid, pushName);
  }

  chatLastMsgMap.set(remoteJid, msg);
  if (msg.key?.id && !String(msg.key.id).startsWith('ARCHIVE_')) {
    const nextTs = Number(msg.messageTimestamp || Math.floor(Date.now() / 1000));
    const prevTs = Number(realMessageAnchorsMap.get(remoteJid)?.messageTimestamp || 0);
    if (nextTs >= prevTs) {
      realMessageAnchorsMap.set(remoteJid, {
        key: msg.key,
        messageTimestamp: nextTs
      });
      saveRealMessageAnchors();
    }
  }

  recordIndividualScanMessage(msg);
  enqueueSupplierInvites(msg);

  // Se o contato/grupo está arquivado ou é fornecedor monitorado, mantém arquivado e atualiza atividade
  if (remoteJid.endsWith('@g.us') || isKnownOrArchivedJid(remoteJid)) {
    registerMonitoredArchivedChat(remoteJid, {
      name: remoteJid.endsWith('@g.us') ? groupSubjectCache.get(remoteJid) : pushName,
      lastActivity: new Date().toLocaleString('pt-BR')
    });
  }

  // Capture first: optional WhatsApp operations must not delay a price list.
  if (!msg.key?.fromMe) await forwardMessageToProductParser(msg, options);
  if (remoteJid.endsWith('@g.us')) {
    void archiveChatJid(remoteJid, '', '', '', false).catch(error => addLog(`[ARQUIVO] ${error.message}`));
  }


  // Track every direct supplier response, including short replies and history sync.
  if (!msg.key?.fromMe && !remoteJid.endsWith('@g.us') && !remoteJid.endsWith('@broadcast') && isSupplierReplyContent(msg.message)) {
    const phoneJid = resolveLidJid(msg.key?.remoteJidAlt || remoteJid);
    try {
      const response = await fetch(`http://127.0.0.1:${PANEL_PORT}/api/internal/supplier-reply`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phoneJid, timestamp: Number(msg.messageTimestamp || 0), messageId: msg.key?.id || '' }),
        signal: AbortSignal.timeout(10000)
      });
      if (!response.ok) addLog(`Registro de resposta retornou HTTP ${response.status}`);
    } catch (err) { addLog(`Falha ao registrar resposta: ${err.message}`); }
  }

}

async function startWhatsApp() {
  if (AUTOMATION_PAUSED) { setConnectionStatus('PAUSADO'); return; }
  if (connecting || stopping) return;
  connecting = true;
  const generation = ++socketGeneration;
  try {
    if (waSocket) {
      waSocket.ev.removeAllListeners();
      waSocket.end(undefined);
      waSocket = null;
    }
    await saveCredentials;
    setConnectionStatus('CONNECTING');
    const { state, saveCreds } = await useMultiFileAuthState(authFolder);

    // Use the protocol version bundled with the installed, locked Baileys release.
    const browserConfig = Browsers?.macOS ? Browsers.macOS('Chrome') : ['BuscaBusca', 'Chrome', '1.0.0'];
    await saveCreds();

    waSocket = makeWASocket({
      logger: pino({ level: 'warn' }),
      auth: state,
      printQRInTerminal: false,
      browser: browserConfig,
      keepAliveIntervalMs: 25000,
      connectTimeoutMs: 60000,
      defaultQueryTimeoutMs: 60000,
      retryRequestDelayMs: 2500,
      syncFullHistory: true,
      shouldSyncHistoryMessage: () => true
    });

    const leaveBlockedMembership = createBlockedMembershipGuard({
      getSocket: () => generation === socketGeneration ? waSocket : null,
      isConnected: () => connectionStatus === 'CONNECTED' && !stopping,
      isBlocked: jid => isInviteBlocked('', jid),
      onLeft: jid => {
        for (const [code, record] of joinedCommunitiesMap) {
          if (record.groupJid === jid) joinedCommunitiesMap.set(code, { ...record, status: 'SAIU_FORA_ESCOPO', leftAt: new Date().toISOString() });
        }
        monitoredArchivedMap.delete(jid);
        saveJoinedCommunities();
        saveMonitoredArchived();
        addLog(`[ESCOPO] Saída de grupo bloqueado confirmada (${jid}).`);
      },
      onError: (error, jid) => addLog(`[ESCOPO] Não foi possível sair do grupo bloqueado ${jid}: ${error.message}`)
    });
    activeLeaveBlockedMembership = leaveBlockedMembership;
    waSocket.ev.on('groups.upsert', groups => {
      for (const group of groups || []) void leaveBlockedMembership(group.id);
    });
    waSocket.ev.on('group-participants.update', update => {
      if (update.action !== 'add') return;
      const clean = id => String(id || '').replace(/:\d+@/, '@');
      const ownIds = [waSocket.user?.id, waSocket.user?.lid].filter(Boolean).map(clean);
      if ((update.participants || []).some(person => [typeof person === 'string' ? person : person?.id, person?.phoneNumber, person?.lid].some(id => id && ownIds.includes(clean(id))))) {
        void leaveBlockedMembership(update.id);
      }
    });

    waSocket.ev.on('creds.update', () => {
      saveCredentials = saveCredentials.then(saveCreds).catch(err => {
        addLog(`Falha ao salvar credenciais: ${err.message}`);
      });
    });

    // Capturar todos os contatos, conversas arquivadas e mensagens de histórico (inclusive ON_DEMAND)
    waSocket.ev.on('messaging-history.set', async ({ chats, contacts, messages, syncType }) => {
      try {
        if (Array.isArray(contacts)) {
          for (const c of contacts) {
            if (c && c.id) {
              const cName = c.name || c.notify || c.verifiedName || '';
              if (cName) contactNameCache.set(c.id, cName);
            }
          }
        }
        let archivedDetected = 0;
        if (Array.isArray(chats)) {
          for (const ch of chats) {
            if (!ch || !ch.id || ch.id === 'status@broadcast') continue;
            const chName = ch.name || ch.displayName || ch.subject || contactNameCache.get(ch.id) || '';
            if (chName) {
              if (ch.id.endsWith('@g.us')) groupSubjectCache.set(ch.id, chName);
              else contactNameCache.set(ch.id, chName);
            }
            if (isKnownSupplierJid(ch.id) || joinedCommunitiesMap.has(ch.id)) {
              registerMonitoredArchivedChat(ch.id, {
                name: chName || ch.id.split('@')[0],
                type: ch.id.endsWith('@g.us') ? 'Comunidade Oficial Fornecedor' : 'Fornecedor Arquivado'
              });
              archivedDetected++;
            }
          }
        }
        if (archivedDetected > 0) {
          saveMonitoredArchived();
          addLog(`[SINCRONIA] Sincronização de Arquivados: ${archivedDetected} contatos/grupos arquivados detectados!`);
        }
        if (Array.isArray(messages) && messages.length > 0) {
          addLog(`[MENSAGEM] Processando ${messages.length} mensagens do histórico sincronizado (tipo ${syncType ?? 'histórico'})...`);
          for (const m of messages) {
            if (m && m.key) {
              try { await inspectIncomingMessage(m); }
              catch (error) { addLog(`[CAPTURA] Mensagem do histórico falhou; seguindo com as próximas: ${error.message}`); }
            }
          }
        }
      } catch (err) {
        addLog(`Erro ao processar histórico: ${err.message}`);
      }
    });

    waSocket.ev.on('chats.upsert', (chats) => {
      if (!Array.isArray(chats)) return;
      let added = 0;
      for (const ch of chats) {
        if (!ch || !ch.id || ch.id === 'status@broadcast') continue;
        const chName = ch.name || ch.subject || contactNameCache.get(ch.id) || ch.id.split('@')[0];
        if (ch.archived || ch.archive) {
          if (!ch.id.endsWith('@g.us') || isNeverArchiveGroup(ch.id, chName)) {
            void unarchiveChatJid(ch.id);
            continue;
          }
          if (isKnownSupplierJid(ch.id) || joinedCommunitiesMap.has(ch.id)) {
            registerMonitoredArchivedChat(ch.id, {
              name: chName,
              type: 'Grupo / Comunidade Arquivada'
            });
            added++;
          }
        }
      }
      if (added > 0) saveMonitoredArchived();
    });

    waSocket.ev.on('chats.update', async (updates) => {
      if (!Array.isArray(updates)) return;
      let changed = false;
      for (const u of updates) {
        if (!u || !u.id || u.id === 'status@broadcast') continue;

        if (u.archiveMessageRange) {
          saveRealAnchorFromRange(u.id, u.archiveMessageRange);
        }
        if (u.markReadMessageRange) {
          saveRealAnchorFromRange(u.id, u.markReadMessageRange);
        }

        if (u.archived === true || u.archive === true) {
          const chName = u.name || u.subject || contactNameCache.get(u.id) || groupSubjectCache.get(u.id) || u.id.split('@')[0];
          if (!u.id.endsWith('@g.us') || isNeverArchiveGroup(u.id, chName)) {
            void unarchiveChatJid(u.id);
            changed = true;
            continue;
          }
          const isNew = !monitoredArchivedMap.has(u.id);
          registerMonitoredArchivedChat(u.id, {
            name: chName,
            type: 'Grupo / Comunidade Arquivada'
          });
          changed = true;
          if (isNew) {
            addLog(`[MONITORANDO] Comunidade/Grupo arquivado adicionado ao monitoramento: ${chName} (${u.id})`);
          }
          // Se tiver âncora real e ainda não pediu histórico, já solicita as últimas mensagens
          if (!historyRequestedJids.has(u.id) && realMessageAnchorsMap.has(u.id)) {
            historyRequestedJids.add(u.id);
            setTimeout(() => requestOnDemandHistoryForChat(u.id, 50), 1000);
          }
        }
      }
      if (changed) saveMonitoredArchived();
    });

    waSocket.ev.on('contacts.upsert', (contacts) => {
      if (!Array.isArray(contacts)) return;
      for (const c of contacts) {
        if (c && c.id) {
          const cName = c.name || c.notify || c.verifiedName || '';
          if (cName) {
            contactNameCache.set(c.id, cName);
            if (monitoredArchivedMap.has(c.id)) {
              registerMonitoredArchivedChat(c.id, { name: cName });
            }
          }
        }
      }
      saveMonitoredArchived();
    });

    waSocket.ev.on('connection.update', async (update) => {
      if (generation !== socketGeneration || stopping) return;
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        try {
          const qrDataUrl = await QRCode.toDataURL(qr, { width: 360, margin: 2 });
          if (generation !== socketGeneration || stopping) return;
          currentQRDataUrl = qrDataUrl;
          setConnectionStatus('QR_READY');
          const base64Data = currentQRDataUrl.replace(/^data:image\/png;base64,/, '');
          const buf = Buffer.from(base64Data, 'base64');
          fs.writeFileSync(qrFileLocal, buf);
          addLog('QR Code atualizado. Aguardando leitura no celular...');
        } catch (err) {
          addLog(`Erro ao gerar QR Code: ${err.message}`);
        }
      }

      if (connection === 'open') {
        reconnectAttempt = 0;
        lastDisconnectReason = null;
        lastHeartbeatAt = Date.now();
        subscribedNewsletters.clear();
        setConnectionStatus('CONNECTED');
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = null;
        inviteQueue.wake();
        if (captureQueue.status().pending > 0) captureQueue.wake();
        currentQRDataUrl = null;
        connectedUser = {
          id: waSocket.user?.id || '',
          name: waSocket.user?.name || waSocket.user?.notify || 'Conectado'
        };
        try {
          const credsSrc = path.join(authFolder, 'creds.json');
          if (fs.existsSync(credsSrc)) {
            const backupDir = path.join(__dirname, 'auth_session_backup');
            fs.mkdirSync(backupDir, { recursive: true });
            fs.copyFileSync(credsSrc, path.join(backupDir, 'creds.json'));
          }
        } catch {}

        const todayKey = getBrasiliaTimeParts(new Date()).dayKey;
        const needsDeepScan = lastDeepScanAt === 0
          || (Date.now() - lastDeepScanAt >= 45 * 60 * 1000)
          || (todayKey !== lastDeepScanDay);
        if (needsDeepScan) {
          historyRequestedJids.clear();
          archivedMessageIds.clear();
          addLog(`WhatsApp conectado: ${connectedUser.name} (${connectedUser.id}). Iniciando vistoria automática de Arquivados & Comunidades...`);
          setTimeout(() => {
            if (generation !== socketGeneration || stopping || connectionStatus !== 'CONNECTED') return;
            void deepScanAllArchivedAndCommunities().catch(error => addLog(`[VISTORIA] ${error.message}`));
          }, 3500);
        } else {
          addLog(`WhatsApp reconectado (${connectedUser.name}) — sessão preservada, captura ao vivo mantida sem rajada de pedidos.`);
        }
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const errorMsg = lastDisconnect?.error?.message || 'Motivo desconhecido';
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        lastDisconnectReason = { code: statusCode || null, message: errorMsg };

        addLog(`Conexão fechada (código ${statusCode || 'desconhecido'}: ${errorMsg}).`);
        currentQRDataUrl = null;

        if (loggedOut) {
          setConnectionStatus('DISCONNECTED');
          connectedUser = null;
          await saveCredentials;
          // Keep a recoverable copy instead of deleting authentication files.
          if (fs.existsSync(authFolder)) fs.renameSync(authFolder, `${authFolder}.expired-${Date.now()}`);
          addLog('Vínculo revogado pelo WhatsApp. Escaneie o novo QR Code no painel.');
          scheduleReconnect(1500);
        } else if (statusCode === DisconnectReason.connectionReplaced) {
          setConnectionStatus('DISCONNECTED');
          connectedUser = null;
          addLog('Sessão aberta em outra instância. Reconexão pausada para evitar disputa de conexão.');
        } else {
          const attempt = reconnectAttempt++;
          setConnectionStatus(attempt < 3 ? 'RECONNECTING' : 'DISCONNECTED');
          if (attempt >= 3) connectedUser = null;
          const isFastTransient = statusCode === DisconnectReason.restartRequired || statusCode === 428 || statusCode === 515;
          const delay = (isFastTransient && attempt === 0) ? 1500 : Math.min(60000, 3000 * 2 ** Math.min(attempt, 5));
          addLog(`Reconexão automática em ${(delay / 1000).toFixed(1)}s, preservando credenciais.`);
          scheduleReconnect(delay);
        }
      }
    });

    waSocket.ev.on('messages.update', async updates => {
      for (const { key, update } of updates || []) {
        if (key?.fromMe && key.id && Number.isInteger(update?.status)) {
          earlyMessageAcks.set(key.id, Math.max(earlyMessageAcks.get(key.id) || 0, update.status));
          if (earlyMessageAcks.size > 2000) earlyMessageAcks.delete(earlyMessageAcks.keys().next().value);
          queueMessageAck(key.id, update.status);
          continue;
        }
        if (!key?.fromMe && update?.message) {
          const editedInner =
            update.message.editedMessage?.message ||
            update.message.protocolMessage?.editedMessage ||
            (update.message.protocolMessage ? null : update.message);
          if (!editedInner) continue;
          const editTimestamp = supplierEventTime(update.message, update.messageTimestamp);
          try {
            await inspectIncomingMessage(
              { key, message: editedInner, messageTimestamp: editTimestamp },
              { isEdited: true, editTimestamp }
            );
          } catch (err) {
            addLog(`Erro ao processar mensagem editada: ${err.message}`);
          }
        }
      }
    });

    waSocket.ev.on('messages.upsert', async ({ messages }) => {
      if (!Array.isArray(messages)) return;
      for (const msg of messages) {
        try { await inspectIncomingMessage(msg); }
        catch (err) { addLog(`Erro ao processar mensagem: ${err.message}`); }
      }
    });

    waSocket.ev.on('group-participants.update', async ({ id, participants, action }) => {
      if (action !== 'add') return;
      const ownIds = [waSocket.user?.id, waSocket.user?.lid].filter(Boolean).map(jid => jid.replace(/:\d+@/, '@'));
      const ownAdded = (participants || []).some(value => {
        const ids = typeof value === 'string' ? [value] : [value.id, value.phoneNumber, value.lid];
        return ids.some(jid => jid && ownIds.includes(jid.replace(/:\d+@/, '@')));
      });
      if (!ownAdded) return;
      for (const record of joinedCommunitiesMap.values()) {
        if (record.groupJid === id && record.status === 'AGUARDANDO_APROVACAO') {
          record.status = 'ENTROU';
          registerMonitoredArchivedChat(id, { name: record.groupName, type: 'Grupo / Comunidade Fornecedor' });
          if (await archiveChatJid(id, '[Aprovação confirmada]', record.groupName, 'Grupo / Comunidade Fornecedor')) {
            record.status = 'ENTROU_E_ARQUIVOU';
          }
          if (record.linkedParent) {
            await archiveChatJid(record.linkedParent, '', `${record.groupName} (Comunidade)`, 'Comunidade');
          }
          saveJoinedCommunities();
          saveMonitoredArchived();
          addLog(`[CONVITE] Aprovação confirmada em "${record.groupName}".`);
        }
      }
    });

    waSocket.ev.on('groups.upsert', async (groups) => {
      if (!Array.isArray(groups)) return;
      for (const g of groups) {
        if (g.id) {
          groupMetaCache.set(g.id, g);
          if (g.subject) groupSubjectCache.set(g.id, g.subject);
        }
      }
    });
  } catch (err) {
    setConnectionStatus('DISCONNECTED');
    addLog(`Erro ao iniciar sessão WhatsApp: ${err.message}`);
    scheduleReconnect(10000);
  } finally {
    connecting = false;
  }
}

async function resolveWhatsAppJid(rawPhone) {
  const digits = String(rawPhone || '').replace(/\D/g, '');
  if (!digits) return null;

  const candidates = [digits];
  if (digits.startsWith('55') && digits.length === 13) {
    candidates.push(digits.slice(0, 4) + digits.slice(5));
  } else if (digits.startsWith('55') && digits.length === 12) {
    candidates.push(digits.slice(0, 4) + '9' + digits.slice(4));
  }

  for (const cand of candidates) {
    try {
      const results = await waSocket.onWhatsApp(`${cand}@s.whatsapp.net`);
      if (results && results.length > 0 && results[0].exists) {
        return results[0].jid;
      }
    } catch {}
  }

  return `${digits}@s.whatsapp.net`;
}

app.get('/api/status', (req, res) => {
  const communitiesList = Array.from(joinedCommunitiesMap.values());
  const basePayload = {
    status: connectionStatus,
    lastDisconnect: lastDisconnectReason,
    reconnectAttempt,
    connectionChangedAt,
    lastReceivedAt,
    capture: captureQueue.status(),
    captureWindow: { start: 6, end: 18, timeZone: 'America/Sao_Paulo' },
    autoJoin: AUTOMATIC_INVITES_ENABLED && !AUTOMATION_PAUSED,
    automationPaused: AUTOMATION_PAUSED,
    pendingInvites: [...inviteQueue.items.values()].filter(item => item.status === 'PENDENTE').length,
    user: connectedUser,
    hasQr: !!currentQRDataUrl,
    archivedMonitoredCount: monitoredArchivedMap.size,
    communitiesCount: communitiesList.filter(c => c.status === 'ENTROU' || c.status === 'ENTROU_E_ARQUIVOU').length
  };
  if (req.query?.lite === '1') {
    return res.json(basePayload);
  }
  res.json({
    ...basePayload,
    qrDataUrl: currentQRDataUrl,
    logs: recentLogs.slice(0, 60),
    communities: communitiesList,
    archivedMonitored: Array.from(monitoredArchivedMap.values())
  });
});

app.post('/api/send', async (req, res) => {
  const { phone, name, message } = req.body || {};
  if (AUTOMATION_PAUSED || connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  if (!phone || !message) {
    return res.status(400).json({ ok: false, error: 'Telefone e mensagem são obrigatórios.' });
  }

  try {
    const jid = await resolveWhatsAppJid(phone);
    for (const v of normalizePhoneDigits(phone)) {
      knownSupplierDigits.add(v);
    }
    if (name) contactNameCache.set(jid, name);

    try {
      await waSocket.presenceSubscribe(jid);
      await waSocket.sendPresenceUpdate('composing', jid);
      const typingMs = Math.min(7000, Math.max(2200, message.length * 45 + Math.floor(Math.random() * 1400)));
      await new Promise((r) => setTimeout(r, typingMs));
      await waSocket.sendPresenceUpdate('paused', jid);
    } catch {}

    const sentMsg = await waSocket.sendMessage(jid, { text: message });
    const receipt = sentMessageReceipt(sentMsg, jid);
    chatLastMsgMap.set(jid, sentMsg);
    outgoingMessages.set(receipt.messageId, jid);
    if (earlyMessageAcks.has(receipt.messageId)) receipt.ack = Math.max(receipt.ack, earlyMessageAcks.get(receipt.messageId));
    for (let i = 0; i < 16 && receipt.ack < 2; i++) {
      await new Promise((r) => setTimeout(r, 150));
      const updatedAck = earlyMessageAcks.get(receipt.messageId);
      if (updatedAck !== undefined) receipt.ack = Math.max(receipt.ack, updatedAck);
    }
    queueMessageAck(receipt.messageId, receipt.ack);
    addLog(`[MENSAGEM] Criada para ${name || phone}; ID ${receipt.messageId}; confirmação ${receipt.ack}.`);
    // Garantir que a conversa individual fique visível (desarquivada) no WhatsApp do usuário
    try { await unarchiveChatJid(jid); } catch {}
    return res.json({ ok: true, ...receipt });
  } catch (err) {
    addLog(`[ERRO] Erro ao enviar para ${name || phone}: ${err.message}`);
    return res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/api/join-community', async (req, res) => {
  const { linkOrCode } = req.body || {};
  if (AUTOMATION_PAUSED || connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  const code = parseManualInvite(linkOrCode);
  if (!code) {
    return res.status(400).json({ ok: false, error: 'Link ou código inválido.' });
  }
  const captured = inviteQueue.items.get(code);
  const result = await processInviteCode(code, captured?.senderJid || 'Manual', captured?.senderName || 'Painel');
  if (!result) return res.status(409).json({ ok: false, error: 'Este convite já está sendo processado. Aguarde.' });
  if (['ERRO', 'FORA_ESCOPO', 'REVISAR_ESCOPO'].includes(result.status)) return res.status(422).json({ ok: false, error: result.error, result });
  res.json({ ok: true, result });
});

app.post('/api/archive-all', async (req, res) => {
  if (AUTOMATION_PAUSED || connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  const scanRes = await deepScanAllArchivedAndCommunities();
  res.json({
    ok: true,
    groupCount: scanRes.monitoredCount || 0,
    chatCount: scanRes.historyRequestsSent || 0
  });
});

app.post('/api/scan-archived', async (req, res) => {
  const scanRes = await deepScanAllArchivedAndCommunities();
  res.json(scanRes);
});

app.get('/api/groups', async (req, res) => {
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  try {
    const groupsObj = await waSocket.groupFetchAllParticipating();
    const groups = Object.values(groupsObj || {}).map(g => {
      const subject = g.subject || groupSubjectCache.get(g.id) || '';
      const decision = projectInviteDecision(subject, {
        knownSupplier: isKnownSupplierJid(g.id),
        blocked: false
      });
      const blockedOrExcluded = !isSafeGroupName(subject, g.id) || isInviteBlocked(subject, g.id) || isNeverArchiveGroup(g.id, subject);
      return {
        id: g.id,
        subject,
        size: Array.isArray(g.participants) ? g.participants.length : (g.size || 0),
        isCommunity: !!g.isCommunity,
        isCommunityAnnounce: !!g.isCommunityAnnounce,
        linkedParent: g.linkedParent || '',
        blacklisted: blockedOrExcluded,
        decision
      };
    });
    res.json({ ok: true, count: groups.length, groups });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/api/groups/leave-and-delete', async (req, res) => {
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  const jids = Array.isArray(req.body?.jids) ? req.body.jids.filter(j => typeof j === 'string' && j.endsWith('@g.us')) : [];
  if (!jids.length) {
    return res.status(400).json({ ok: false, error: 'Informe uma lista válida de jids.' });
  }

  const results = [];
  for (const jid of jids) {
    const entry = { jid, left: false, deleted: false, errors: [] };
    try {
      await waSocket.groupLeave(jid);
      entry.left = true;
    } catch (err) {
      entry.errors.push(`leave: ${err.message}`);
    }

    await new Promise(r => setTimeout(r, 350));

    try {
      const nowSec = Math.floor(Date.now() / 1000);
      const lastMsg = chatLastMsgMap.get(jid) || realMessageAnchorsMap.get(jid);
      const safeMsg = (lastMsg?.key?.id && Number(lastMsg.messageTimestamp) > 0)
        ? {
            key: {
              remoteJid: jid,
              id: lastMsg.key.id,
              fromMe: !!lastMsg.key.fromMe,
              ...(lastMsg.key.participant ? { participant: lastMsg.key.participant } : {})
            },
            messageTimestamp: Number(lastMsg.messageTimestamp)
          }
        : {
            key: {
              remoteJid: jid,
              id: `DELETE_${nowSec}`,
              fromMe: true
            },
            messageTimestamp: nowSec
          };
      await waSocket.chatModify({ delete: true, lastMessages: [safeMsg] }, jid);
      entry.deleted = true;
    } catch (err) {
      entry.errors.push(`delete: ${err.message}`);
    }

    monitoredArchivedMap.delete(jid);
    for (const [code, item] of joinedCommunitiesMap.entries()) {
      if (item?.groupJid === jid || code === jid) {
        joinedCommunitiesMap.delete(code);
      }
    }
    results.push(entry);
    await new Promise(r => setTimeout(r, 250));
  }

  saveMonitoredArchived();
  saveJoinedCommunities();
  addLog(`[LIMPEZA] Processados ${results.length} grupos (saída e exclusão do WhatsApp).`);
  res.json({ ok: true, count: results.length, results });
});

app.post('/api/chats/unarchive', async (req, res) => {
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  const requested = Array.isArray(req.body?.jids) ? req.body.jids.filter(j => typeof j === 'string' && j.includes('@')) : [];
  const targets = new Set(requested);
  for (const jid of NEVER_ARCHIVE_JIDS) targets.add(jid);
  for (const jid of monitoredArchivedMap.keys()) {
    if (!jid.endsWith('@g.us') || isNeverArchiveGroup(jid)) targets.add(jid);
  }
  let okCount = 0;
  for (const jid of targets) {
    if (await unarchiveChatJid(jid)) okCount++;
    await new Promise(r => setTimeout(r, 120));
  }
  saveMonitoredArchived();
  addLog(`[DESARQUIVAR] Desarquivados ${okCount}/${targets.size} chats individuais/grupos protegidos.`);
  res.json({ ok: true, total: targets.size, unarchived: okCount });
});

app.post('/api/scan-individual-history', async (req, res) => {
  if (!waSocket || connectionStatus !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'WhatsApp não conectado' });
  }
  const count = Math.min(200, Math.max(10, Number(req.body?.count || 50)));
  const targets = new Set();
  for (const jid of [...realMessageAnchorsMap.keys(), ...chatLastMsgMap.keys()]) {
    if (!jid || jid === 'status@broadcast' || jid.endsWith('@g.us') || jid.endsWith('@broadcast')) continue;
    const resolved = resolveLidJid(jid);
    if (isKnownSupplierJid(jid) || isKnownSupplierJid(resolved)) continue;
    targets.add(jid);
  }
  let requested = 0;
  for (const jid of targets) {
    if (await requestOnDemandHistoryForChat(jid, count)) requested++;
    await new Promise(r => setTimeout(r, 150));
  }
  res.json({ ok: true, totalAnchors: targets.size, requested });
});

app.get('/api/scan-individual-results', (req, res) => {
  const nonSupplierJids = new Set();
  const nameMatches = [];
  for (const jid of [...contactNameCache.keys(), ...realMessageAnchorsMap.keys(), ...chatLastMsgMap.keys()]) {
    if (!jid || jid === 'status@broadcast' || jid.endsWith('@g.us') || jid.endsWith('@broadcast')) continue;
    const resolved = resolveLidJid(jid);
    if (isKnownSupplierJid(jid) || isKnownSupplierJid(resolved)) continue;
    nonSupplierJids.add(resolved || jid);
  }
  for (const [jid, name] of contactNameCache.entries()) {
    if (!jid || jid === 'status@broadcast' || jid.endsWith('@g.us') || jid.endsWith('@broadcast')) continue;
    const resolved = resolveLidJid(jid);
    if (isKnownSupplierJid(jid) || isKnownSupplierJid(resolved)) continue;
    const normName = String(name || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    if (/lacrad|fornecedor|comprovante/.test(normName)) {
      nameMatches.push({
        jid: resolved || jid,
        phone: (resolved || jid).replace(/@.*$/, '').replace(/\D/g, ''),
        name
      });
    }
  }
  const matches = Array.from(individualScanState.matchesByJid.values()).map(entry => ({
    jid: entry.jid,
    phone: entry.phone,
    name: contactNameCache.get(entry.jid) || entry.name,
    categories: Array.from(entry.categories),
    snippets: entry.snippets
  }));
  res.json({
    ok: true,
    scannedMessagesCount: individualScanState.scannedMessagesCount,
    totalNonSupplierContacts: nonSupplierJids.size,
    nameMatches,
    matches
  });
});

app.post('/api/scan-supplier-invites', async (req, res) => {
  if (!waSocket || connectionStatus !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'WhatsApp não conectado' });
  }
  const count = Math.min(100, Math.max(10, Number(req.body?.count || 45)));
  const candidates = [];
  const seenKeys = new Set();

  const resolveLidFileForPhone = (phoneDigits) => {
    const clean = String(phoneDigits || '').replace(/\D/g, '');
    if (!clean) return '';
    const variants = [clean];
    if (clean.startsWith('55') && clean.length === 13) variants.push(clean.slice(0, 4) + clean.slice(5));
    if (clean.startsWith('55') && clean.length === 12) variants.push(clean.slice(0, 4) + '9' + clean.slice(4));
    for (const v of variants) {
      const fp = path.join(authFolder, `lid-mapping-${v}.json`);
      if (fs.existsSync(fp)) {
        try {
          const raw = fs.readFileSync(fp, 'utf8').trim().replace(/^"|"$/g, '');
          if (raw) return `${raw}@lid`;
        } catch {}
      }
    }
    return '';
  };

  const addCandidate = (keyObj, ts) => {
    if (!keyObj?.remoteJid || !keyObj?.id) return;
    const dedupe = `${keyObj.remoteJid}:${keyObj.id}`;
    if (seenKeys.has(dedupe)) return;
    seenKeys.add(dedupe);
    candidates.push({ key: keyObj, timestamp: Number(ts || Math.floor(Date.now() / 1000)) });
  };

  for (const [jid, anchor] of [...realMessageAnchorsMap.entries(), ...chatLastMsgMap.entries()]) {
    if (!jid || jid.endsWith('@g.us') || jid.endsWith('@broadcast') || jid === 'status@broadcast') continue;
    const resolved = resolveLidJid(jid);
    if (!isKnownSupplierJid(jid) && !isKnownSupplierJid(resolved)) continue;
    if (anchor?.key?.id && !String(anchor.key.id).startsWith('ARCHIVE_')) {
      addCandidate(anchor.key, anchor.messageTimestamp);
    }
  }

  const responsesFile = path.join(__dirname, 'respostas_fornecedores.json');
  const replies = fs.existsSync(responsesFile) ? readJsonSafe(responsesFile) : {};
  if (replies && typeof replies === 'object') {
    for (const [keyPhone, info] of Object.entries(replies)) {
      if (!info || typeof info !== 'object') continue;
      const phone = String(info.telefone || keyPhone || '').replace(/\D/g, '');
      const msgId = String(info.message_id || '').trim();
      const ts = Number(info.timestamp || 0);
      if (!phone || !msgId || !ts) continue;
      const lidJid = resolveLidFileForPhone(phone);
      if (lidJid) addCandidate({ remoteJid: lidJid, id: msgId, fromMe: false }, ts);
      addCandidate({ remoteJid: `${phone}@s.whatsapp.net`, id: msgId, fromMe: false }, ts);
    }
  }

  let requested = 0;
  let errors = 0;
  let firstError = '';
  for (const anchor of candidates) {
    try {
      const tsMs = anchor.timestamp < 20000000000 ? anchor.timestamp * 1000 : anchor.timestamp;
      await waSocket.fetchMessageHistory(count, anchor.key, tsMs);
      requested++;
    } catch (err) {
      errors++;
      if (!firstError) firstError = err?.message || String(err);
    }
    await new Promise(r => setTimeout(r, 120));
  }
  addLog(`[CONVITE-SCAN] Varredura de histórico: ${requested} pedidos OK, ${errors} erros (total=${candidates.length})${firstError ? ` | ${firstError}` : ''}.`);
  res.json({ ok: true, candidates: candidates.length, requested, errors, firstError });
});

app.post('/api/join-pending-invites', async (req, res) => {
  if (!waSocket || connectionStatus !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'WhatsApp não conectado' });
  }
  const queueFile = path.join(__dirname, 'fila_convites.json');
  const queueItems = fs.existsSync(queueFile) ? readJsonSafe(queueFile) : [];
  const results = [];
  if (Array.isArray(queueItems)) {
    for (const item of queueItems) {
      if (!item || !item.code) continue;
      const existing = joinedCommunitiesMap.get(item.code);
      if (existing && ['ENTROU', 'ENTROU_E_ARQUIVOU', 'AGUARDANDO_APROVACAO', 'FORA_ESCOPO', 'SAIU_FORA_ESCOPO'].includes(existing.status)) {
        continue;
      }
      const record = await processInviteCode(item.code, item.senderJid || 'Desconhecido', item.senderName || '');
      if (record) results.push(record);
      await new Promise(r => setTimeout(r, 1200));
    }
  }
  res.json({ ok: true, processed: results.length, results });
});

app.post('/api/logout', async (req, res) => {
  ++socketGeneration;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  try {
    if (waSocket) {
      waSocket.ev.removeAllListeners();
      await waSocket.logout();
      waSocket.end(undefined);
      waSocket = null;
    }
    await saveCredentials;
    if (fs.existsSync(authFolder)) fs.renameSync(authFolder, `${authFolder}.manual-${Date.now()}`);
    setConnectionStatus('DISCONNECTED');
    currentQRDataUrl = null;
    connectedUser = null;
    reconnectAttempt = 0;
    scheduleReconnect(1000);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: 'Não foi possível encerrar a sessão.' });
  }
});

const lockFile = path.join(__dirname, 'auth_session.pid');

function acquireSingleInstanceLock() {
  try {
    if (fs.existsSync(lockFile)) {
      const oldPid = Number(fs.readFileSync(lockFile, 'utf-8').trim());
      if (oldPid && oldPid !== process.pid) {
        try {
          process.kill(oldPid, 0);
          addLog(`[TRAVA] Outra instância do motor WhatsApp já está ativa (PID ${oldPid}). Encerrando duplicata para evitar conflito de sessão.`);
          return false;
        } catch {
          // PID antigo não existe mais; segue normalmente
        }
      }
    }
    fs.writeFileSync(lockFile, String(process.pid), 'utf-8');
    return true;
  } catch {
    return true;
  }
}

function releaseSingleInstanceLock() {
  try {
    if (fs.existsSync(lockFile)) {
      const pidInFile = Number(fs.readFileSync(lockFile, 'utf-8').trim());
      if (pidInFile === process.pid) fs.unlinkSync(lockFile);
    }
  } catch {}
}

// Verifica a cada 30s se a conexão travou ou ficou desconectada sem reconexão agendada.
async function checkConnectionHealth() {
  if (stopping || AUTOMATION_PAUSED || watchdogBusy) return;
  if (connectionStatus === 'QR_READY' || connectionStatus === 'PAUSADO') return;
  if (lastDisconnectReason?.code === DisconnectReason.connectionReplaced) return;

  if ((connectionStatus === 'DISCONNECTED' || connectionStatus === 'RECONNECTING') && !connecting && !reconnectTimer) {
    addLog('[WATCHDOG] Conexão parada sem agendamento ativo. Reativando WhatsApp...');
    scheduleReconnect(2000);
    return;
  }

  if ((connectionStatus === 'CONNECTING' || connectionStatus === 'RECONNECTING') && !connecting && Date.now() - connectionChangedAt > 120000) {
    addLog('[WATCHDOG] A conexão não terminou em dois minutos. Agendando nova tentativa.');
    setConnectionStatus('DISCONNECTED');
    scheduleReconnect(3000);
    return;
  }

  if (connectionStatus === 'CONNECTED' && waSocket) {
    // Evita enviar sendPresenceUpdate a cada 30s quando há tráfego recente ou o último heartbeat foi há menos de 3 min.
    if (typeof lastReceivedAt === 'number' && lastReceivedAt > 0 && Date.now() - lastReceivedAt < 120000) {
      return;
    }
    if (typeof lastHeartbeatAt === 'number' && lastHeartbeatAt > 0 && Date.now() - lastHeartbeatAt < 180000) {
      return;
    }
    const generation = socketGeneration;
    const socket = waSocket;
    let timer;
    watchdogBusy = true;
    try {
      await Promise.race([
        socket.sendPresenceUpdate('available'),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout_heartbeat')), 15000); })
      ]);
      if (typeof lastHeartbeatAt === 'number') lastHeartbeatAt = Date.now();
    } catch (err) {
      if (generation !== socketGeneration || stopping || connectionStatus !== 'CONNECTED') return;
      addLog(`[WATCHDOG] Socket sem resposta (${err.message}). Reiniciando conexão limpa preservando sessão...`);
      setConnectionStatus('RECONNECTING');
      scheduleReconnect(3000);
    } finally { clearTimeout(timer); watchdogBusy = false; }
  }
}
const connectionWatchdog = setInterval(() => void checkConnectionHealth().catch(error => addLog(`[WATCHDOG] ${error.message}`)), 30000);
if (typeof connectionWatchdog.unref === 'function') connectionWatchdog.unref();

let lastWindowWakeDay = '';
let lastNewsletterSyncAt = 0;
let windowMaintenanceBusy = false;

// Mantém a janela diária 06:00-18:00 BRT ativa mesmo após a virada da noite sem depender de reconexão.
async function runWindowMaintenance() {
  if (stopping || AUTOMATION_PAUSED || windowMaintenanceBusy) return;
  if (connectionStatus !== 'CONNECTED' || !waSocket) return;
  const { dayKey, hour, minute } = getBrasiliaTimeParts(new Date());
  if (hour < 6 || hour >= 18) return;

  windowMaintenanceBusy = true;
  try {
    if (lastWindowWakeDay !== dayKey) {
      lastWindowWakeDay = dayKey;
      lastNewsletterSyncAt = Date.now();
      historyRequestedJids.clear();
      archivedMessageIds.clear();
      addLog(`[JANELA 06:00-18:00] Varredura automática ativada para ${dayKey} (${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} BRT).`);
      await deepScanAllArchivedAndCommunities();
      return;
    }
    if (captureQueue.status().pending > 0) captureQueue.wake();
    if (Date.now() - lastNewsletterSyncAt >= 30 * 60 * 1000) {
      lastNewsletterSyncAt = Date.now();
      for (const [jid] of monitoredArchivedMap.entries()) {
        if (stopping || connectionStatus !== 'CONNECTED' || !waSocket) break;
        if (!jid.endsWith('@newsletter')) continue;
        if (!subscribedNewsletters.has(jid) && typeof waSocket.subscribeNewsletterUpdates === 'function') {
          await waSocket.subscribeNewsletterUpdates(jid).catch(() => {});
          subscribedNewsletters.add(jid);
        }
        if (typeof waSocket.newsletterFetchMessages === 'function') {
          await waSocket.newsletterFetchMessages(jid, 20).catch(() => {});
        }
      }
    }
  } finally {
    windowMaintenanceBusy = false;
  }
}
const windowMaintenanceTimer = setInterval(() => void runWindowMaintenance().catch(error => addLog(`[JANELA] ${error.message}`)), 60000);
if (typeof windowMaintenanceTimer.unref === 'function') windowMaintenanceTimer.unref();

async function shutdown() {
  stopping = true;
  saveRealMessageAnchors(true);
  clearInterval(connectionWatchdog);
  clearInterval(windowMaintenanceTimer);
  inviteQueue.stop();
  captureQueue.stop();
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (waSocket) {
    waSocket.ev.removeAllListeners();
    waSocket.end(undefined);
  }
  await saveCredentials;
  releaseSingleInstanceLock();
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

if (acquireSingleInstanceLock()) {
  app.listen(PORT, '127.0.0.1', () => {
    addLog(`Motor interno WhatsApp + Monitor de Contatos Arquivados rodando em 127.0.0.1:${PORT}`);
    startWhatsApp();
    if (!AUTOMATION_PAUSED) captureQueue.wake();
  });
} else {
  process.exit(0);
}
