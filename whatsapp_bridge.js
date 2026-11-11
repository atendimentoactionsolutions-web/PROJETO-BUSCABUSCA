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
app.use(express.json());

const PORT = Number(process.env.BRIDGE_PORT || 3051);
const authFolder = path.join(__dirname, 'auth_session');
const qrFileLocal = path.join(__dirname, 'qr_whatsapp.png');
const communitiesFile = path.join(__dirname, 'comunidades_entradas.json');

let waSocket = null;
let connectionStatus = 'CONNECTING'; // CONNECTING, QR_READY, CONNECTED, DISCONNECTED
let currentQRDataUrl = null;
let connectedUser = null;
let recentLogs = [];

// Mapa de grupos/comunidades entrados: code ou groupJid -> dados
const joinedCommunitiesMap = new Map();
// Conjunto de JIDs de grupos que devem permanecer sempre arquivados
const autoArchiveGroupJids = new Set();
// Última mensagem real recebida de cada grupo para o chatModify archive
const groupLastMsgMap = new Map();
// Fila para processar códigos sem concorrência excessiva
const pendingInviteCodes = new Set();

function addLog(msg) {
  const line = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  console.log(line);
  recentLogs.unshift(line);
  if (recentLogs.length > 200) recentLogs.pop();
}

function loadJoinedCommunities() {
  try {
    if (fs.existsSync(communitiesFile)) {
      const arr = JSON.parse(fs.readFileSync(communitiesFile, 'utf-8'));
      if (Array.isArray(arr)) {
        for (const item of arr) {
          if (item.code) joinedCommunitiesMap.set(item.code, item);
          if (item.groupJid) autoArchiveGroupJids.add(item.groupJid);
          if (item.linkedParent) autoArchiveGroupJids.add(item.linkedParent);
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

loadJoinedCommunities();

// Arquiva um grupo/comunidade de forma infalível
async function archiveGroupJid(groupJid, reason = '') {
  if (!waSocket || !groupJid) return false;
  autoArchiveGroupJids.add(groupJid);

  const nowSec = Math.floor(Date.now() / 1000);
  const lastMsg = groupLastMsgMap.get(groupJid);

  // Tentativa 1: Usando a última mensagem real se disponível
  if (lastMsg && lastMsg.key && lastMsg.key.id) {
    try {
      const safeMsg = {
        key: {
          remoteJid: groupJid,
          id: lastMsg.key.id,
          fromMe: !!lastMsg.key.fromMe,
          participant: lastMsg.key.participant || (lastMsg.key.fromMe ? undefined : groupJid)
        },
        messageTimestamp: Number(lastMsg.messageTimestamp || nowSec)
      };
      await waSocket.chatModify({ archive: true, lastMessages: [safeMsg] }, groupJid);
      addLog(`📦 Arquivado com sucesso (${groupJid}) ${reason}`);
      return true;
    } catch {}
  }

  // Tentativa 2: Usando objeto messageRange direto (suportado pelo chatModificationToAppPatch do Baileys)
  try {
    await waSocket.chatModify(
      {
        archive: true,
        lastMessages: {
          lastMessageTimestamp: nowSec,
          messages: [
            {
              key: {
                remoteJid: groupJid,
                id: 'ARCHIVE_' + nowSec,
                fromMe: true
              },
              messageTimestamp: nowSec
            }
          ]
        }
      },
      groupJid
    );
    addLog(`📦 Comunidade/Grupo arquivado automaticamente (${groupJid}) ${reason}`);
    return true;
  } catch (err) {
    addLog(`⚠️ Aviso ao arquivar ${groupJid} (será re-arquivado no próximo evento): ${err.message}`);
    return false;
  }
}

// Extrai todos os códigos de convite chat.whatsapp.com de qualquer estrutura de mensagem (incluindo saudações automáticas, botões, templates, legendas)
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

// Busca recursivamente se há groupInviteMessage nativo dentro da mensagem
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

async function processInviteCode(code, senderJid = 'Desconhecido', senderName = '') {
  if (!code || !waSocket) return null;

  const existing = joinedCommunitiesMap.get(code);
  if (existing && existing.status === 'ENTROU_E_ARQUIVOU') {
    if (existing.groupJid) {
      await archiveGroupJid(existing.groupJid, '(re-verificação)');
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

    // 1. Inspecionar metadados do convite antes de entrar
    try {
      const info = await waSocket.groupGetInviteInfo(code);
      if (info) {
        groupName = info.subject || groupName;
        groupJid = info.id || null;
        linkedParent = info.linkedParent || null;
        addLog(`ℹ️ Identificado: "${groupName}" (${groupJid || 'JID pendente'})`);
      }
    } catch (infoErr) {
      addLog(`ℹ️ Prosseguindo com entrada direta para o código ${code}...`);
    }

    // 2. Aceitar convite e entrar automaticamente na comunidade/grupo
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const joinedJid = await waSocket.groupAcceptInvite(code);
        if (joinedJid) groupJid = joinedJid;
        addLog(`✅ ENTROU AUTOMATICAMENTE em "${groupName}" (${groupJid})`);
        break;
      } catch (joinErr) {
        const msg = String(joinErr?.message || '');
        // Se já estiver no grupo (conflict / already participant), continuamos para arquivar
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

    // 3. Consultar metadados do grupo após entrar para descobrir comunidade-mãe (linkedParent)
    if (groupJid) {
      try {
        const meta = await waSocket.groupMetadata(groupJid);
        if (meta) {
          groupName = meta.subject || groupName;
          if (meta.linkedParent) linkedParent = meta.linkedParent;
        }
      } catch {}
    }

    // 4. Arquivar imediatamente o grupo e a comunidade-mãe (se houver)
    if (groupJid) {
      await archiveGroupJid(groupJid, `["${groupName}"]`);
      // Re-arquivar após 3 segundos e 8 segundos para garantir que mensagens de boas-vindas do grupo não o desarquivem
      setTimeout(() => archiveGroupJid(groupJid, `[confirmação 3s "${groupName}"]`), 3000);
      setTimeout(() => archiveGroupJid(groupJid, `[confirmação 8s "${groupName}"]`), 8000);
    }
    if (linkedParent) {
      await archiveGroupJid(linkedParent, `[Comunidade-Mãe de "${groupName}"]`);
      setTimeout(() => archiveGroupJid(linkedParent, `[confirmação Comunidade-Mãe]`), 3500);
    }

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

async function inspectIncomingMessageForInvites(msg) {
  if (!msg || !msg.message) return;
  const remoteJid = msg.key?.remoteJid || '';
  const pushName = msg.pushName || '';

  // Se for mensagem dentro de um grupo que nós entramos e marcamos para auto-arquivar, atualizar última msg e manter arquivado
  if (remoteJid.endsWith('@g.us')) {
    groupLastMsgMap.set(remoteJid, msg);
    if (autoArchiveGroupJids.has(remoteJid)) {
      await archiveGroupJid(remoteJid, '(mantendo arquivado após nova mensagem)');
    }
  }

  // 1. Extrair todos os links chat.whatsapp.com/XXXX (inclusive em mensagens de saudação, legendas, botões, etc.)
  const codes = extractInviteCodesFromMessage(msg.message);
  for (const code of codes) {
    await processInviteCode(code, remoteJid, pushName);
  }

  // 2. Verificar convite nativo (groupInviteMessage V4)
  const nativeInvite = findNativeGroupInvite(msg.message);
  if (nativeInvite && nativeInvite.inviteCode) {
    try {
      addLog(`📩 Convite nativo de Grupo/Comunidade recebido de ${pushName || remoteJid}! Aceitando...`);
      const gJid = await waSocket.groupAcceptInviteV4(msg.key, nativeInvite);
      const targetJid = gJid || nativeInvite.groupJid;
      if (targetJid) {
        await archiveGroupJid(targetJid, '[Convite Nativo V4]');
        setTimeout(() => archiveGroupJid(targetJid, '[Confirmação V4]'), 3000);
      }
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
    } catch (v4Err) {
      // Fallback para groupAcceptInvite padrão
      await processInviteCode(nativeInvite.inviteCode, remoteJid, pushName);
    }
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
      browser: ['Painel Python VPS', 'Chrome', '1.0.0'],
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
        addLog(`WhatsApp conectado: ${connectedUser.name} (${connectedUser.id}). Monitor de Comunidades 100% ATIVO!`);
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

    // Escutar TODAS as mensagens recebidas (inclusive saudações automáticas instantâneas e histórico recente)
    waSocket.ev.on('messages.upsert', async ({ messages }) => {
      if (!Array.isArray(messages)) return;
      for (const msg of messages) {
        await inspectIncomingMessageForInvites(msg);
      }
    });

    // Caso algum grupo novo seja adicionado via evento groups.upsert logo após aceitar convite
    waSocket.ev.on('groups.upsert', async (groups) => {
      if (!Array.isArray(groups)) return;
      for (const g of groups) {
        if (g.id) {
          addLog(`📥 Novo grupo/comunidade sincronizado: "${g.subject || g.id}". Arquivando automaticamente...`);
          await archiveGroupJid(g.id, `["${g.subject || g.id}"]`);
          if (g.linkedParent) {
            await archiveGroupJid(g.linkedParent, `[Comunidade-Mãe]`);
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
    logs: recentLogs.slice(0, 50),
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
    await waSocket.sendMessage(jid, { text: message });
    addLog(`✅ Mensagem enviada para ${name || phone} (${jid})`);
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
  addLog(`Motor interno WhatsApp + Auto-Join de Comunidades rodando em 127.0.0.1:${PORT}`);
  startWhatsApp();
});
