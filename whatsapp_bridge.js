import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import QRCode from 'qrcode';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Patch Baileys chat-utils.js before importing Baileys so chats.update always emits archiveMessageRange & markReadMessageRange without buffering drops
try {
  const chatUtilsPath = path.join(__dirname, 'node_modules', '@whiskeysockets', 'baileys', 'lib', 'Utils', 'chat-utils.js');
  if (fs.existsSync(chatUtilsPath)) {
    let cu = fs.readFileSync(chatUtilsPath, 'utf-8');
    let modified = false;
    if (!cu.includes('archiveMessageRange:')) {
      cu = cu.replace(
        /archived:\s*isArchived,\s*conditional:\s*getChatUpdateConditional\(id,\s*msgRange\)/,
        'archived: isArchived, archiveMessageRange: archiveAction?.messageRange, conditional: undefined'
      );
      modified = true;
    }
    if (!cu.includes('markReadMessageRange:')) {
      cu = cu.replace(
        /unreadCount:\s*isNullUpdate\s*\?\s*null\s*:\s*!!markReadAction\?\.read\s*\?\s*0\s*:\s*-1,\s*conditional:\s*getChatUpdateConditional\(id,\s*markReadAction\?\.messageRange\)/,
        'unreadCount: isNullUpdate ? null : !!markReadAction?.read ? 0 : -1, markReadMessageRange: markReadAction?.messageRange, conditional: undefined'
      );
      modified = true;
    }
    if (modified) {
      fs.writeFileSync(chatUtilsPath, cu, 'utf-8');
      console.log('[Patch] Baileys chat-utils.js atualizado para expor archiveMessageRange e markReadMessageRange.');
    }
  }
} catch (e) {
  console.error('Aviso ao aplicar patch em chat-utils.js:', e.message);
}

const baileysModule = await import('@whiskeysockets/baileys');
const makeWASocket = baileysModule.makeWASocket || baileysModule.default;
const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = baileysModule;

const app = express();
app.use(express.json({ limit: '10mb' }));

const PORT = Number(process.env.BRIDGE_PORT || 3051);
const PANEL_PORT = Number(process.env.PORT || 3050);
const PANEL_INGEST_URL = `http://127.0.0.1:${PANEL_PORT}/api/internal/ingest-message`;

const authFolder = path.join(__dirname, 'auth_session');
const qrFileLocal = path.join(__dirname, 'qr_whatsapp.png');
const communitiesFile = path.join(__dirname, 'comunidades_entradas.json');
const archivedMonitoredFile = path.join(__dirname, 'arquivados_monitorados.json');
const suppliersFile = path.join(__dirname, 'fornecedores.json');

let waSocket = null;
let connectionStatus = 'CONNECTING'; // CONNECTING, QR_READY, CONNECTED, DISCONNECTED
let currentQRDataUrl = null;
let connectedUser = null;
let recentLogs = [];

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
const historyRequestedJids = new Set();

function addLog(msg) {
  const line = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  console.log(line);
  recentLogs.unshift(line);
  if (recentLogs.length > 250) recentLogs.pop();
}

function readJsonSafe(filePath) {
  const raw = fs.readFileSync(filePath, 'utf-8').replace(/^\uFEFF/, '');
  return JSON.parse(raw);
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

function saveRealAnchorFromRange(jid, rangeObj) {
  if (!jid || !rangeObj) return;
  const msgs = rangeObj.messages || [];
  for (const item of msgs) {
    const k = item?.key || item?.message?.key;
    const ts = Number(item?.messageTimestamp || item?.message?.messageTimestamp || rangeObj.lastMessageTimestamp || 0);
    if (k && k.id && !String(k.id).startsWith('ARCHIVE_')) {
      realMessageAnchorsMap.set(jid, {
        key: {
          remoteJid: jid,
          id: k.id,
          fromMe: !!k.fromMe,
          participant: k.participant || undefined
        },
        messageTimestamp: ts || Math.floor(Date.now() / 1000)
      });
      return;
    }
  }
}

function registerMonitoredArchivedChat(jid, info = {}) {
  if (!jid || jid === 'status@broadcast') return;
  autoArchiveJids.add(jid);
  const isGroup = jid.endsWith('@g.us');
  const existing = monitoredArchivedMap.get(jid) || {};
  const name =
    info.name ||
    existing.name ||
    groupSubjectCache.get(jid) ||
    contactNameCache.get(jid) ||
    jid.split('@')[0];

  const record = {
    jid,
    name,
    type: info.type || existing.type || (isGroup ? 'Grupo / Comunidade Arquivada' : 'Contato Arquivado no WhatsApp'),
    lastActivity: info.lastActivity || existing.lastActivity || new Date().toLocaleString('pt-BR'),
    productsCaptured: (existing.productsCaptured || 0) + (info.addProducts || 0),
    status: 'MONITORANDO ARQUIVADO'
  };
  monitoredArchivedMap.set(jid, record);
}

function saveMonitoredArchived() {
  try {
    const arr = Array.from(monitoredArchivedMap.values());
    fs.writeFileSync(archivedMonitoredFile, JSON.stringify(arr, null, 2), 'utf-8');
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
      const arr = readJsonSafe(suppliersFile);
      if (Array.isArray(arr)) {
        for (const s of arr) {
          const sName = String(s.nome || '').trim();
          for (const v of normalizePhoneDigits(s.telefone)) {
            knownSupplierDigits.add(v);
            const sJid = `${v}@s.whatsapp.net`;
            autoArchiveJids.add(sJid);
            if (sName) contactNameCache.set(sJid, sName);
          }
        }
        addLog(`📋 ${arr.length} fornecedores carregados na memória para identificação e auto-arquivamento.`);
      }
    }
  } catch (err) {
    addLog(`Erro ao carregar fornecedores: ${err.message}`);
  }
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
          if (item.groupJid) {
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

function saveJoinedCommunities() {
  try {
    const arr = Array.from(joinedCommunitiesMap.values());
    fs.writeFileSync(communitiesFile, JSON.stringify(arr, null, 2), 'utf-8');
  } catch (err) {
    addLog(`Erro ao salvar comunidades: ${err.message}`);
  }
}

loadKnownSuppliers();
loadMonitoredArchived();
loadJoinedCommunities();

// Arquiva qualquer chat (Comunidade, Grupo, Lista de Transmissão ou Contato Arquivado) e o registra no Monitor
async function archiveChatJid(chatJid, reason = '', customName = '', customType = '', forceFakeIfNoMsg = false) {
  if (!waSocket || !chatJid || chatJid === 'status@broadcast') return false;
  registerMonitoredArchivedChat(chatJid, {
    name: customName,
    type: customType
  });
  saveMonitoredArchived();

  const nowSec = Math.floor(Date.now() / 1000);
  const lastMsg = chatLastMsgMap.get(chatJid) || realMessageAnchorsMap.get(chatJid);

  if (lastMsg && lastMsg.key && lastMsg.key.id) {
    try {
      const safeMsg = {
        key: {
          remoteJid: chatJid,
          id: lastMsg.key.id,
          fromMe: !!lastMsg.key.fromMe,
          participant: lastMsg.key.participant || (lastMsg.key.fromMe ? undefined : chatJid)
        },
        messageTimestamp: Number(lastMsg.messageTimestamp || nowSec)
      };
      await waSocket.chatModify({ archive: true, lastMessages: [safeMsg] }, chatJid);
      if (reason) addLog(`📦 Arquivado e monitorado (${chatJid}) ${reason}`);
      return true;
    } catch {}
  }

  if (!forceFakeIfNoMsg) {
    return true;
  }

  try {
    await waSocket.chatModify(
      {
        archive: true,
        lastMessages: {
          lastMessageTimestamp: nowSec,
          messages: [
            {
              key: {
                remoteJid: chatJid,
                id: 'ARCHIVE_' + nowSec,
                fromMe: true
              },
              messageTimestamp: nowSec
            }
          ]
        }
      },
      chatJid
    );
    if (reason) addLog(`📦 Arquivado e monitorado (${chatJid}) ${reason}`);
    return true;
  } catch {
    return false;
  }
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
    return true;
  } catch {
    return false;
  }
}

// Vistoria completa: sincroniza todos os contatos/grupos arquivados no aparelho e puxa o histórico de mensagens deles
async function deepScanAllArchivedAndCommunities() {
  if (!waSocket || connectionStatus !== 'CONNECTED') return { ok: false, error: 'WhatsApp não conectado' };
  addLog('🔎 Iniciando VISTORIA COMPLETA nos Contatos Arquivados, Comunidades e Grupos...');

  // 1. Sincronizar metadados de todos os grupos/comunidades participantes
  await syncAndArchiveAllParticipatingGroups();

  // 2. Forçar re-leitura dos snapshots de App State (regular_low = arquivados + messageRange, regular_high = lidos + messageRange, critical_unblock_low = contatos)
  try {
    for (const col of ['regular_low', 'regular_high', 'critical_unblock_low']) {
      const f = path.join(authFolder, `app-state-sync-version-${col}.json`);
      if (fs.existsSync(f)) fs.unlinkSync(f);
    }
    await waSocket.resyncAppState(['regular_low', 'regular_high', 'critical_unblock_low'], false);
  } catch (err) {
    addLog(`ℹ️ AppState sync aviso: ${err.message}`);
  }

  // 3. Solicitar histórico sob demanda (fetchMessageHistory) de todos os arquivados e comunidades com âncora de mensagem
  let requestedCount = 0;
  for (const [jid] of monitoredArchivedMap.entries()) {
    if (realMessageAnchorsMap.has(jid)) {
      const ok = await requestOnDemandHistoryForChat(jid, 50);
      if (ok) {
        requestedCount++;
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  }
  for (const [jid] of realMessageAnchorsMap.entries()) {
    if (!monitoredArchivedMap.has(jid) && (jid.endsWith('@g.us') || isKnownOrArchivedJid(jid))) {
      const ok = await requestOnDemandHistoryForChat(jid, 50);
      if (ok) {
        requestedCount++;
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  }

  addLog(`📡 Vistoria: ${monitoredArchivedMap.size} arquivados/comunidades mapeados e ${requestedCount} pedidos de histórico enviados ao aparelho.`);
  return {
    ok: true,
    monitoredCount: monitoredArchivedMap.size,
    anchorsCount: realMessageAnchorsMap.size,
    historyRequestsSent: requestedCount
  };
}

// Sincroniza e arquiva todos os grupos e comunidades em que a conta participa
async function syncAndArchiveAllParticipatingGroups() {
  if (!waSocket || connectionStatus !== 'CONNECTED') return 0;
  try {
    const groupsObj = await waSocket.groupFetchAllParticipating();
    const groups = Object.values(groupsObj || {});
    let count = 0;
    for (const g of groups) {
      if (!g || !g.id) continue;
      groupMetaCache.set(g.id, g);
      if (g.subject) groupSubjectCache.set(g.id, g.subject);
      const isComm = !!(g.isCommunity || g.isCommunityAnnounce || g.linkedParent);
      await archiveChatJid(g.id, '', g.subject || 'Grupo / Comunidade', isComm ? 'Comunidade' : 'Grupo', false);
      if (g.linkedParent) {
        await archiveChatJid(g.linkedParent, '', `${g.subject || 'Comunidade'} (Mãe)`, 'Comunidade-Mãe', false);
      }
      if (!joinedCommunitiesMap.has(g.id) && !Array.from(joinedCommunitiesMap.values()).some((x) => x.groupJid === g.id)) {
        joinedCommunitiesMap.set(g.id, {
          code: g.id,
          link: isComm ? 'Comunidade Participante' : 'Grupo Participante',
          groupJid: g.id,
          linkedParent: g.linkedParent || '',
          groupName: g.subject || 'Grupo / Comunidade',
          senderJid: g.owner || 'Sincronizado',
          senderName: isComm ? 'Comunidade' : 'Grupo',
          status: 'ENTROU_E_ARQUIVOU',
          joinedAt: new Date().toLocaleString('pt-BR')
        });
      }
      count++;
    }
    if (count > 0) {
      saveJoinedCommunities();
      saveMonitoredArchived();
      addLog(`📦 Sincronização completa: ${count} grupos/comunidades arquivados e em monitoramento 24h.`);
    }
    return count;
  } catch {
    return 0;
  }
}

function extractInviteCodesFromMessage(msgObj) {
  const codes = new Set();
  if (!msgObj) return [];

  try {
    const rawJson = JSON.stringify(msgObj);
    const regex = /(?:https?:\/\/)?chat\.whatsapp\.com\/(?:invite\/)?([A-Za-z0-9]{20,26})/gi;
    let match;
    while ((match = regex.exec(rawJson)) !== null) {
      if (match[1]) {
        codes.add(match[1].trim());
      }
    }
  } catch {}

  return Array.from(codes);
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
  if (m.ephemeralMessage?.message) return extractPlainTextFromMessage(m.ephemeralMessage.message);
  if (m.viewOnceMessage?.message) return extractPlainTextFromMessage(m.viewOnceMessage.message);
  if (m.viewOnceMessageV2?.message) return extractPlainTextFromMessage(m.viewOnceMessageV2.message);
  if (m.documentWithCaptionMessage?.message) return extractPlainTextFromMessage(m.documentWithCaptionMessage.message);

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
    const meta = await waSocket.groupMetadata(groupJid);
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

async function forwardMessageToProductParser(msg) {
  try {
    if (!msg || !msg.message) return false;
    const text = extractPlainTextFromMessage(msg.message);
    if (!text || text.trim().length < 8) return false;

    const remoteJid = msg.key?.remoteJid || '';
    if (remoteJid === 'status@broadcast') return false;

    const isGroup = remoteJid.endsWith('@g.us');
    const isBroadcast = !!(msg.broadcast || msg.key?.remoteJid?.endsWith('@broadcast'));
    const participantJid = isGroup
      ? (msg.key?.participant || msg.key?.participantAlt || '')
      : (msg.key?.senderPn || msg.key?.remoteJidAlt || remoteJid);

    const savedContactName = contactNameCache.get(remoteJid) || contactNameCache.get(participantJid) || '';
    const pushName = msg.pushName || savedContactName || '';
    if (pushName && !isGroup) {
      contactNameCache.set(remoteJid, pushName);
    }

    let groupName = '';
    let channelType = '';
    if (isGroup) {
      const details = await getGroupDetails(remoteJid);
      channelType = details.isCommunity ? 'Comunidade Arquivada' : 'Grupo Arquivado';
      groupName = details.subject || groupSubjectCache.get(remoteJid) || channelType;
    } else if (isBroadcast) {
      channelType = 'Lista de Transmissão Arquivada';
      groupName = pushName || savedContactName || '';
    } else {
      channelType = 'Contato Arquivado';
      groupName = pushName || savedContactName || '';
    }

    const payload = {
      text,
      remoteJid,
      participantJid,
      pushName: pushName || savedContactName,
      groupName,
      channelType,
      isGroup,
      timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000))
    };

    const res = await fetch(PANEL_INGEST_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.parsedCount > 0) {
        addLog(`🏷️ Buscador: ${data.parsedCount} produtos capturados de "${data.supplierName}" (${channelType})`);
        registerMonitoredArchivedChat(remoteJid, {
          name: isGroup ? (groupName || data.supplierName) : (data.supplierName || pushName),
          type: channelType,
          lastActivity: new Date().toLocaleString('pt-BR'),
          addProducts: data.parsedCount
        });
        await archiveChatJid(remoteJid, `[Lista recebida de ${data.supplierName}]`, data.supplierName, channelType, false);
        return true;
      }
    }
  } catch {}
  return false;
}

async function processInviteCode(code, senderJid = 'Desconhecido', senderName = '') {
  if (!code || !waSocket) return null;

  const existing = joinedCommunitiesMap.get(code);
  if (existing && existing.status === 'ENTROU_E_ARQUIVOU') {
    if (existing.groupJid) {
      await archiveChatJid(existing.groupJid, '(re-verificação)', existing.groupName, 'Comunidade / Grupo', true);
    }
    return existing;
  }

  if (pendingInviteCodes.has(code)) return null;
  pendingInviteCodes.add(code);

  try {
    addLog(`🔗 Link de Comunidade/Grupo detectado de ${senderName || senderJid}: https://chat.whatsapp.com/${code}`);

    let groupName = 'Comunidade / Grupo Fornecedor';
    let groupJid = null;
    let linkedParent = null;

    try {
      const info = await waSocket.groupGetInviteInfo(code);
      if (info) {
        groupName = info.subject || groupName;
        groupJid = info.id || null;
        linkedParent = info.linkedParent || null;
        addLog(`ℹ️ Identificado: "${groupName}" (${groupJid || 'JID pendente'})`);
      }
    } catch {}

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const joinedJid = await waSocket.groupAcceptInvite(code);
        if (joinedJid) groupJid = joinedJid;
        addLog(`✅ ENTROU AUTOMATICAMENTE em "${groupName}" (${groupJid})`);
        break;
      } catch (joinErr) {
        const msg = String(joinErr?.message || '');
        if (msg.includes('conflict') || msg.includes('already') || msg.includes('409')) {
          addLog(`ℹ️ Já era participante de "${groupName}" (${groupJid}). Garantindo arquivamento...`);
          break;
        }
        if (attempt < 3) {
          addLog(`⏳ Tentativa ${attempt}/3 para entrar no link ${code} falhou (${msg}). Retentando em 2s...`);
          await new Promise((r) => setTimeout(r, 2000));
        } else {
          throw joinErr;
        }
      }
    }

    if (groupJid) {
      try {
        const meta = await waSocket.groupMetadata(groupJid);
        if (meta) {
          groupMetaCache.set(groupJid, meta);
          groupName = meta.subject || groupName;
          groupSubjectCache.set(groupJid, groupName);
          if (meta.linkedParent) linkedParent = meta.linkedParent;
        }
      } catch {}
    }

    if (groupJid) {
      await archiveChatJid(groupJid, `["${groupName}"]`, groupName, 'Comunidade / Grupo', true);
      setTimeout(() => archiveChatJid(groupJid, `[confirmação 3s "${groupName}"]`, groupName, 'Comunidade / Grupo', true), 3000);
      setTimeout(() => archiveChatJid(groupJid, `[confirmação 8s "${groupName}"]`, groupName, 'Comunidade / Grupo', true), 8000);
    }
    if (linkedParent) {
      await archiveChatJid(linkedParent, `[Comunidade-Mãe de "${groupName}"]`, `${groupName} (Comunidade)`, 'Comunidade-Mãe', true);
      setTimeout(() => archiveChatJid(linkedParent, `[confirmação Comunidade-Mãe]`, `${groupName} (Comunidade)`, 'Comunidade-Mãe', true), 3500);
    }
    if (senderJid && senderJid !== 'Manual' && senderJid !== 'Desconhecido') {
      await archiveChatJid(senderJid, `[Fornecedor que enviou comunidade]`, senderName, 'Contato Arquivado', false);
    }

    setTimeout(() => syncAndArchiveAllParticipatingGroups(), 5000);

    const record = {
      code,
      link: `https://chat.whatsapp.com/${code}`,
      groupJid: groupJid || '',
      linkedParent: linkedParent || '',
      groupName,
      senderJid,
      senderName: senderName || senderJid.split('@')[0],
      status: 'ENTROU_E_ARQUIVOU',
      joinedAt: new Date().toLocaleString('pt-BR')
    };

    joinedCommunitiesMap.set(code, record);
    saveJoinedCommunities();
    return record;
  } catch (err) {
    addLog(`❌ Erro ao entrar no link https://chat.whatsapp.com/${code}: ${err.message}`);
    const errRecord = {
      code,
      link: `https://chat.whatsapp.com/${code}`,
      groupJid: '',
      groupName: 'Erro ao entrar',
      senderJid,
      senderName: senderName || senderJid.split('@')[0],
      status: 'ERRO',
      error: err.message,
      joinedAt: new Date().toLocaleString('pt-BR')
    };
    joinedCommunitiesMap.set(code, errRecord);
    saveJoinedCommunities();
    return errRecord;
  } finally {
    pendingInviteCodes.delete(code);
  }
}

async function inspectIncomingMessage(msg) {
  if (!msg || !msg.message) return;
  const remoteJid = msg.key?.remoteJid || '';
  if (!remoteJid || remoteJid === 'status@broadcast') return;
  const pushName = msg.pushName || contactNameCache.get(remoteJid) || '';
  if (pushName && !remoteJid.endsWith('@g.us')) {
    contactNameCache.set(remoteJid, pushName);
  }

  chatLastMsgMap.set(remoteJid, msg);
  if (msg.key?.id && !String(msg.key.id).startsWith('ARCHIVE_')) {
    realMessageAnchorsMap.set(remoteJid, {
      key: msg.key,
      messageTimestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000))
    });
  }

  // Se o contato/grupo está arquivado ou é fornecedor monitorado, mantém arquivado e atualiza atividade
  if (remoteJid.endsWith('@g.us') || isKnownOrArchivedJid(remoteJid)) {
    registerMonitoredArchivedChat(remoteJid, {
      name: remoteJid.endsWith('@g.us') ? groupSubjectCache.get(remoteJid) : pushName,
      lastActivity: new Date().toLocaleString('pt-BR')
    });
    await archiveChatJid(remoteJid, '', '', '', false);
  }

  // 1. Extrair e entrar em links de comunidades/grupos (inclusive em saudações automáticas)
  const codes = extractInviteCodesFromMessage(msg.message);
  for (const code of codes) {
    await processInviteCode(code, remoteJid, pushName);
  }

  // 2. Verificar convites nativos V4
  const nativeInvite = findNativeGroupInvite(msg.message);
  if (nativeInvite && nativeInvite.inviteCode) {
    try {
      addLog(`📩 Convite nativo de Grupo/Comunidade recebido de ${pushName || remoteJid}! Aceitando...`);
      const gJid = await waSocket.groupAcceptInviteV4(msg.key, nativeInvite);
      const targetJid = gJid || nativeInvite.groupJid;
      if (targetJid) {
        await archiveChatJid(targetJid, '[Convite Nativo V4]', nativeInvite.groupName || 'Grupo Convidado', 'Comunidade / Grupo', true);
        setTimeout(() => archiveChatJid(targetJid, '[Confirmação V4]', '', '', true), 3000);
      }
      await archiveChatJid(remoteJid, '[Fornecedor Convite V4]', pushName, 'Contato Arquivado', false);
      joinedCommunitiesMap.set(nativeInvite.inviteCode, {
        code: nativeInvite.inviteCode,
        link: `Convite Nativo (${nativeInvite.groupName || targetJid})`,
        groupJid: targetJid || '',
        groupName: nativeInvite.groupName || 'Grupo Convidado',
        senderJid: remoteJid,
        senderName: pushName || remoteJid.split('@')[0],
        status: 'ENTROU_E_ARQUIVOU',
        joinedAt: new Date().toLocaleString('pt-BR')
      });
      saveJoinedCommunities();
    } catch {
      await processInviteCode(nativeInvite.inviteCode, remoteJid, pushName);
    }
  }

  // 3. Enviar texto da mensagem para o Buscador Inteligente de Preços
  if (!msg.key?.fromMe) {
    await forwardMessageToProductParser(msg);
  }
}

async function startWhatsApp() {
  try {
    connectionStatus = 'CONNECTING';
    const { state, saveCreds } = await useMultiFileAuthState(authFolder);

    let version = [2, 3000, 1015901307];
    try {
      const v = await fetchLatestBaileysVersion();
      if (v && v.version) version = v.version;
    } catch {}

    waSocket = makeWASocket({
      version,
      auth: state,
      printQRInTerminal: false,
      browser: ['Buscador PXT & Painel VPS', 'Chrome', '1.0.0'],
      syncFullHistory: true,
      shouldSyncHistoryMessage: () => true
    });

    waSocket.ev.on('creds.update', saveCreds);

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
            if (ch.archived || ch.archive || ch.id.endsWith('@g.us')) {
              registerMonitoredArchivedChat(ch.id, {
                name: chName || ch.id.split('@')[0],
                type: ch.id.endsWith('@g.us') ? 'Grupo / Comunidade Arquivada' : 'Contato Arquivado no WhatsApp'
              });
              archivedDetected++;
            }
          }
        }
        if (archivedDetected > 0) {
          saveMonitoredArchived();
          addLog(`📂 Sincronização de Arquivados: ${archivedDetected} contatos/grupos arquivados detectados!`);
        }
        if (Array.isArray(messages) && messages.length > 0) {
          addLog(`📥 Processando ${messages.length} mensagens do histórico sincronizado (tipo ${syncType ?? 'histórico'})...`);
          for (const m of messages) {
            if (m && m.key) {
              await inspectIncomingMessage(m);
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
        if (ch && ch.id && (ch.archived || ch.archive)) {
          const chName = ch.name || ch.subject || contactNameCache.get(ch.id) || ch.id.split('@')[0];
          registerMonitoredArchivedChat(ch.id, {
            name: chName,
            type: ch.id.endsWith('@g.us') ? 'Grupo / Comunidade Arquivada' : 'Contato Arquivado no WhatsApp'
          });
          added++;
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
          const isNew = !monitoredArchivedMap.has(u.id);
          registerMonitoredArchivedChat(u.id, {
            name: chName,
            type: u.id.endsWith('@g.us') ? 'Grupo / Comunidade Arquivada' : 'Contato Arquivado no WhatsApp'
          });
          changed = true;
          if (isNew) {
            addLog(`👁️ Contato/Chat arquivado adicionado ao monitoramento: ${chName} (${u.id})`);
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
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        try {
          currentQRDataUrl = await QRCode.toDataURL(qr, { width: 360, margin: 2 });
          connectionStatus = 'QR_READY';
          const base64Data = currentQRDataUrl.replace(/^data:image\/png;base64,/, '');
          const buf = Buffer.from(base64Data, 'base64');
          fs.writeFileSync(qrFileLocal, buf);
          addLog('QR Code atualizado. Aguardando leitura no celular...');
        } catch (err) {
          addLog(`Erro ao gerar QR Code: ${err.message}`);
        }
      }

      if (connection === 'open') {
        connectionStatus = 'CONNECTED';
        currentQRDataUrl = null;
        connectedUser = {
          id: waSocket.user?.id || '',
          name: waSocket.user?.name || waSocket.user?.notify || 'Conectado'
        };
        addLog(`WhatsApp conectado: ${connectedUser.name} (${connectedUser.id}). Iniciando vistoria automática de Arquivados & Comunidades...`);
        setTimeout(() => deepScanAllArchivedAndCommunities(), 3500);
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        addLog(`Conexão fechada (código ${statusCode}).`);
        connectionStatus = 'DISCONNECTED';
        currentQRDataUrl = null;
        connectedUser = null;
        if (loggedOut) {
          try {
            fs.rmSync(authFolder, { recursive: true, force: true });
          } catch {}
        }
        setTimeout(startWhatsApp, 3000);
      }
    });

    waSocket.ev.on('messages.upsert', async ({ messages }) => {
      if (!Array.isArray(messages)) return;
      for (const msg of messages) {
        await inspectIncomingMessage(msg);
      }
    });

    waSocket.ev.on('groups.upsert', async (groups) => {
      if (!Array.isArray(groups)) return;
      for (const g of groups) {
        if (g.id) {
          groupMetaCache.set(g.id, g);
          if (g.subject) groupSubjectCache.set(g.id, g.subject);
          const isComm = !!(g.isCommunity || g.isCommunityAnnounce || g.linkedParent);
          addLog(`📥 Novo grupo/comunidade detectado: "${g.subject || g.id}". Arquivando e monitorando automaticamente...`);
          await archiveChatJid(g.id, `["${g.subject || g.id}"]`, g.subject || 'Grupo', isComm ? 'Comunidade' : 'Grupo', true);
          if (g.linkedParent) {
            await archiveChatJid(g.linkedParent, `[Comunidade-Mãe]`, `${g.subject || 'Comunidade'} (Mãe)`, 'Comunidade-Mãe', true);
          }
          if (!joinedCommunitiesMap.has(g.id)) {
            joinedCommunitiesMap.set(g.id, {
              code: g.id,
              link: isComm ? 'Adicionado à Comunidade' : 'Adicionado ao Grupo',
              groupJid: g.id,
              linkedParent: g.linkedParent || '',
              groupName: g.subject || 'Grupo / Comunidade',
              senderJid: g.owner || 'Automático',
              senderName: isComm ? 'Comunidade' : 'Grupo',
              status: 'ENTROU_E_ARQUIVOU',
              joinedAt: new Date().toLocaleString('pt-BR')
            });
            saveJoinedCommunities();
          }
        }
      }
    });
  } catch (err) {
    addLog(`Erro ao iniciar sessão WhatsApp: ${err.message}`);
    setTimeout(startWhatsApp, 5000);
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
  res.json({
    status: connectionStatus,
    user: connectedUser,
    hasQr: !!currentQRDataUrl,
    qrDataUrl: currentQRDataUrl,
    logs: recentLogs.slice(0, 60),
    communities: Array.from(joinedCommunitiesMap.values()),
    archivedMonitored: Array.from(monitoredArchivedMap.values()),
    archivedMonitoredCount: monitoredArchivedMap.size
  });
});

app.post('/api/send', async (req, res) => {
  const { phone, name, message } = req.body || {};
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
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

    const sentMsg = await waSocket.sendMessage(jid, { text: message });
    if (sentMsg) {
      chatLastMsgMap.set(jid, sentMsg);
    }
    addLog(`✅ Mensagem enviada para ${name || phone} (${jid})`);

    await archiveChatJid(jid, `[Fornecedor arquivado e monitorado: ${name || phone}]`, name || phone, 'Fornecedor Arquivado', true);
    setTimeout(() => archiveChatJid(jid, '', name || phone, 'Fornecedor Arquivado', true), 2500);

    return res.json({ ok: true, jid });
  } catch (err) {
    addLog(`❌ Erro ao enviar para ${name || phone}: ${err.message}`);
    return res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/api/join-community', async (req, res) => {
  const { linkOrCode } = req.body || {};
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
    return res.status(400).json({ ok: false, error: 'WhatsApp não está conectado.' });
  }
  const codes = extractInviteCodesFromMessage({ text: String(linkOrCode || '') });
  const code = codes[0] || String(linkOrCode || '').trim();
  if (!code) {
    return res.status(400).json({ ok: false, error: 'Link ou código inválido.' });
  }
  const result = await processInviteCode(code, 'Manual', 'Painel');
  res.json({ ok: true, result });
});

app.post('/api/archive-all', async (req, res) => {
  if (connectionStatus !== 'CONNECTED' || !waSocket) {
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

app.post('/api/logout', async (req, res) => {
  try {
    if (waSocket) await waSocket.logout();
  } catch {}
  try {
    fs.rmSync(authFolder, { recursive: true, force: true });
  } catch {}
  connectionStatus = 'DISCONNECTED';
  currentQRDataUrl = null;
  connectedUser = null;
  addLog('Sessão desconectada manualmente. Gerando novo QR Code...');
  setTimeout(startWhatsApp, 1000);
  res.json({ ok: true });
});

app.listen(PORT, '127.0.0.1', () => {
  addLog(`Motor interno WhatsApp + Monitor de Contatos Arquivados rodando em 127.0.0.1:${PORT}`);
  startWhatsApp();
});
