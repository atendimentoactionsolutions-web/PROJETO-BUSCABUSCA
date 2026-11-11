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

let waSocket = null;
let connectionStatus = 'CONNECTING'; // CONNECTING, QR_READY, CONNECTED, DISCONNECTED
let currentQRDataUrl = null;
let connectedUser = null;
let recentLogs = [];

function addLog(msg) {
  const line = `[${new Date().toLocaleTimeString('pt-BR')}] ${msg}`;
  console.log(line);
  recentLogs.unshift(line);
  if (recentLogs.length > 150) recentLogs.pop();
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
        addLog(`WhatsApp conectado: ${connectedUser.name} (${connectedUser.id}). Aguardando sua autorização para disparar.`);
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
    logs: recentLogs.slice(0, 40)
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
  addLog(`Motor interno WhatsApp rodando em 127.0.0.1:${PORT}`);
  startWhatsApp();
});
