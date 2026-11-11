import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import QRCode from 'qrcode';
import makeWASocketDefault, {
  makeWASocket as makeWASocketNamed,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion
} from '@whiskeysockets/baileys';

const makeWASocket = makeWASocketNamed || makeWASocketDefault;
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
app.use(express.json({ limit: '10mb' }));

const PORT = Number(process.env.BRIDGE_PORT || 3051);
const PANEL_PORT = Number(process.env.PORT || 3050);
const PANEL_INGEST_URL = `http://127.0.0.1:${PANEL_PORT}/api/internal/ingest-message`;

const authFolder = path.join(__dirname, 'auth_session');
const qrFileLocal = path.join(__dirname, 'qr_whatsapp.png');
const communitiesFile = path.join(__dirname, 'comunidades_entradas.json');
const suppliersFile = path.join(__dirname, 'fornecedores.json');

let waSocket = null;
let connectionStatus = 'CONNECTING'; // CONNECTING, QR_READY, CONNECTED, DISCONNECTED
let currentQRDataUrl = null;
let connectedUser = null;
let recentLogs = [];

const joinedCommunitiesMap = new Map();
const autoArchiveJids = new Set();
const chatLastMsgMap = new Map();
const groupSubjectCache = new Map();
const groupMetaCache = new Map();
const pendingInviteCodes = new Set();
const knownSupplierDigits = new Set();

function addLog(msg) {
  const line = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  console.log(line);
  recentLogs.unshift(line);
  if (recentLogs.length > 250) recentLogs.pop();
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

function loadKnownSuppliers() {
  try {
    if (fs.existsSync(suppliersFile)) {
      const arr = JSON.parse(fs.readFileSync(suppliersFile, 'utf-8'));
      if (Array.isArray(arr)) {
        for (const s of arr) {
          for (const v of normalizePhoneDigits(s.telefone)) {
            knownSupplierDigits.add(v);
            autoArchiveJids.add(`${v}@s.whatsapp.net`);
          }
        }
      }
    }
  } catch (err) {
    addLog(`Erro ao carregar fornecedores para auto-arquivamento: ${err.message}`);
  }
}

function isKnownSupplierJid(jid) {
  if (!jid) return false;
  if (autoArchiveJids.has(jid)) return true;
  const userPart = jid.split('@')[0].split(':')[0];
  for (const v of normalizePhoneDigits(userPart)) {
    if (knownSupplierDigits.has(v)) return true;
  }
  return false;
}

function loadJoinedCommunities() {
  try {
    if (fs.existsSync(communitiesFile)) {
      const arr = JSON.parse(fs.readFileSync(communitiesFile, 'utf-8'));
      if (Array.isArray(arr)) {
        for (const item of arr) {
          const key = item.code || item.groupJid;
          if (key) joinedCommunitiesMap.set(key, item);
          if (item.groupJid) {
            autoArchiveJids.add(item.groupJid);
            if (item.groupName) groupSubjectCache.set(item.groupJid, item.groupName);
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
loadJoinedCommunities();

// Arquiva qualquer chat (Comunidade, Grupo, Lista de Transmissão ou Conversa de Fornecedor)
async function archiveChatJid(chatJid, reason = '') {
  if (!waSocket || !chatJid || chatJid === 'status@broadcast') return false;
  autoArchiveJids.add(chatJid);

  const nowSec = Math.floor(Date.now() / 1000);
  const lastMsg = chatLastMsgMap.get(chatJid);

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
      if (reason) addLog(`📦 Arquivado automaticamente (${chatJid}) ${reason}`);
      return true;
    } catch {}
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
    if (reason) addLog(`📦 Arquivado automaticamente (${chatJid}) ${reason}`);
    return true;
  } catch {
    return false;
  }
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
      await archiveChatJid(g.id, '');
      if (g.linkedParent) {
        await archiveChatJid(g.linkedParent, '');
      }
      const isComm = !!(g.isCommunity || g.isCommunityAnnounce || g.linkedParent);
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
      addLog(`📦 Sincronização completa: ${count} grupos/comunidades verificados e arquivados.`);
    }
    return count;
  } catch (err) {
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

// Extrai o texto completo de qualquer tipo de mensagem (conversa, texto estendido, legenda de imagem/vídeo/documento, mensagem efêmera)
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
    const pushName = msg.pushName || '';

    let groupName = '';
    if (isGroup) {
      const details = await getGroupDetails(remoteJid);
      const prefix = details.isCommunity ? 'Comunidade' : 'Grupo';
      groupName = details.subject ? `${prefix}: ${details.subject}` : prefix;
    } else if (isBroadcast) {
      groupName = `Lista de Transmissão (${pushName || 'Fornecedor'})`;
    } else {
      groupName = `Lista / Chat Direto (${pushName || 'Fornecedor'})`;
    }

    const payload = {
      text,
      remoteJid,
      participantJid,
      pushName,
      groupName,
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
        addLog(`🏷️ Buscador: ${data.parsedCount} produtos capturados de "${data.supplierName}" via [${groupName}]`);
        // Se for fornecedor em Lista de Transmissão ou Chat Direto que mandou tabela, arquiva o chat dele também!
        await archiveChatJid(remoteJid, `[Tabela recebida de ${data.supplierName}]`);
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
      await archiveChatJid(existing.groupJid, '(re-verificação)');
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

    // Arquivar imediatamente o Grupo/Comunidade, a Comunidade-Mãe e também a conversa do fornecedor que enviou o link!
    if (groupJid) {
      await archiveChatJid(groupJid, `["${groupName}"]`);
      setTimeout(() => archiveChatJid(groupJid, `[confirmação 3s "${groupName}"]`), 3000);
      setTimeout(() => archiveChatJid(groupJid, `[confirmação 8s "${groupName}"]`), 8000);
    }
    if (linkedParent) {
      await archiveChatJid(linkedParent, `[Comunidade-Mãe de "${groupName}"]`);
      setTimeout(() => archiveChatJid(linkedParent, `[confirmação Comunidade-Mãe]`), 3500);
    }
    if (senderJid && senderJid !== 'Manual' && senderJid !== 'Desconhecido') {
      await archiveChatJid(senderJid, `[Fornecedor que enviou comunidade]`);
    }

    // Sincronizar subgrupos (Avisos da Comunidade) criados ao entrar na comunidade
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
  const pushName = msg.pushName || '';

  // Salvar referência da última mensagem para poder arquivar qualquer chat (Comunidade, Grupo ou Fornecedor)
  chatLastMsgMap.set(remoteJid, msg);

  // Todo grupo/comunidade ou fornecedor cadastrado é mantido 100% arquivado
  if (remoteJid.endsWith('@g.us') || isKnownSupplierJid(remoteJid)) {
    await archiveChatJid(remoteJid, '');
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
        await archiveChatJid(targetJid, '[Convite Nativo V4]');
        setTimeout(() => archiveChatJid(targetJid, '[Confirmação V4]'), 3000);
      }
      await archiveChatJid(remoteJid, '[Fornecedor Convite V4]');
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

  // 3. Enviar texto da mensagem para o Buscador Inteligente de Preços (Comunidades, Grupos, Listas de Transmissão e Chat Direto)
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
      syncFullHistory: false
    });

    waSocket.ev.on('creds.update', saveCreds);

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
        addLog(`WhatsApp conectado: ${connectedUser.name} (${connectedUser.id}). Monitor de Comunidades, Grupos e Listas de Transmissão ATIVO!`);
        setTimeout(() => syncAndArchiveAllParticipatingGroups(), 4000);
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
          addLog(`📥 Novo grupo/comunidade detectado: "${g.subject || g.id}". Arquivando automaticamente...`);
          await archiveChatJid(g.id, `["${g.subject || g.id}"]`);
          if (g.linkedParent) {
            await archiveChatJid(g.linkedParent, `[Comunidade-Mãe]`);
          }
          const isComm = !!(g.isCommunity || g.isCommunityAnnounce || g.linkedParent);
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
    communities: Array.from(joinedCommunitiesMap.values())
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
    autoArchiveJids.add(jid);

    const sentMsg = await waSocket.sendMessage(jid, { text: message });
    if (sentMsg) {
      chatLastMsgMap.set(jid, sentMsg);
    }
    addLog(`✅ Mensagem enviada para ${name || phone} (${jid})`);

    // Arquivar automaticamente a conversa do fornecedor após o envio (para manter tudo limpo e arquivado)
    await archiveChatJid(jid, `[Fornecedor contatado: ${name || phone}]`);
    setTimeout(() => archiveChatJid(jid, ''), 2500);

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
  const groupCount = await syncAndArchiveAllParticipatingGroups();
  let chatCount = 0;
  for (const jid of autoArchiveJids) {
    const ok = await archiveChatJid(jid, '');
    if (ok) chatCount++;
  }
  addLog(`📦 Arquivamento geral concluído (${groupCount} grupos/comunidades + ${chatCount} conversas/listas).`);
  res.json({ ok: true, groupCount, chatCount });
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
  addLog(`Motor interno WhatsApp + Auto-Comunidades + Captura de Listas rodando em 127.0.0.1:${PORT}`);
  startWhatsApp();
});
