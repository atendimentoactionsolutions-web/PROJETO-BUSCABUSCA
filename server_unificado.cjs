/**
 * server_unificado.cjs - Servidor Principal do Buscador Mundo Apple (Porta 3050)
 * 
 * Integração Unificada:
 * 1. Serve a Vitrine Oficial Apple (index.html) para o telão com a mesma paleta e tipografia.
 * 2. Serve o Painel Oficial com acesso pela Maçã -> Login -> Admin (admin.html).
 * 3. Modal de engrenagem para controle nativo do WhatsApp (QR code, status, fornecedores).
 * 4. Repasse de WebSockets e APIs oficiais (3333) via http-proxy.
 * 5. Ingestão estrita de mensagens WhatsApp com validação pelo Guardião Anti-Erros (price_guardian.cjs).
 * 6. Regra de Horário: Preços aceitos apenas no dia vigente a partir das 06:00 AM.
 * 7. Reset diário à meia-noite (00:00): Zera completamente o catálogo de preços.
 */

const express = require('express');
const http = require('http');
const path = require('path');
const fs = require('fs');
const httpProxy = require('http-proxy');
const axios = require('axios');
const { spawn, execSync } = require('child_process');
const { validateProduct } = require('./price_guardian.cjs');

const PORT = process.env.PORT || 3050;
const API_BASE = 'http://127.0.0.1:3333';
const BRIDGE_PORT = 3051;
const BRIDGE_BASE = `http://127.0.0.1:${BRIDGE_PORT}`;
const PRODUTOS_DIA_FILE = path.join(__dirname, 'produtos_dia.json');
const PUBLIC_DIR = path.join(__dirname, 'public_ref');

const app = express();
const server = http.createServer(app);

// 1. Criar Proxy reverso para APIs e WebSockets da aplicação oficial (porta 3333)
const proxy = httpProxy.createProxyServer({
  target: API_BASE,
  ws: true,
  changeOrigin: true
});

proxy.on('error', (err, req, res) => {
  if (res && res.writeHead) {
    res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Falha na comunicação com o backend', message: err.message }));
  }
});

// Suporte a WebSockets (Socket.io) transparente
server.on('upgrade', (req, socket, head) => {
  proxy.ws(req, socket, head);
});

// Middleware Express
app.use(express.json({ limit: '20mb' }));
app.use(express.urlencoded({ extended: true, limit: '20mb' }));

// Desativar cache para garantir que todas as telas atualizem imediatamente
app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  next();
});

// 2. Iniciar a Bridge de WhatsApp (Baileys) em processo filho
let waProcess = null;
function startWhatsAppBridge() {
  const bridgeScript = path.join(__dirname, 'whatsapp_bridge.js');
  if (!fs.existsSync(bridgeScript)) return;
  
  waProcess = spawn('node', [bridgeScript], {
    cwd: __dirname,
    stdio: 'inherit',
    env: { ...process.env, BRIDGE_PORT: String(BRIDGE_PORT), PORT: String(PORT) }
  });

  waProcess.on('exit', (code) => {
    console.log(`[Bridge WhatsApp] Processo finalizou com código ${code}. Reiniciando em 5s...`);
    setTimeout(startWhatsAppBridge, 5000);
  });
}
startWhatsAppBridge();

// 3. Helpers de Horário e Reset Diário
function getBrazilDateInfo(date = new Date()) {
  const fmt = new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
  const parts = fmt.formatToParts(date);
  const day = parts.find(p => p.type === 'day')?.value || '01';
  const month = parts.find(p => p.type === 'month')?.value || '01';
  const year = parts.find(p => p.type === 'year')?.value || '2026';
  const hour = parseInt(parts.find(p => p.type === 'hour')?.value || '0', 10);
  const minute = parseInt(parts.find(p => p.type === 'minute')?.value || '0', 10);
  const second = parseInt(parts.find(p => p.type === 'second')?.value || '0', 10);
  const dateStr = `${day}/${month}/${year}`;
  const timeStr = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`;
  return { day, month, year, hour, minute, second, dateStr, timeStr };
}

function loadProdutosDia() {
  try {
    if (fs.existsSync(PRODUTOS_DIA_FILE)) {
      const data = JSON.parse(fs.readFileSync(PRODUTOS_DIA_FILE, 'utf8'));
      if (Array.isArray(data)) return data;
    }
  } catch {}
  return [];
}

function saveProdutosDia(prods) {
  try {
    fs.writeFileSync(PRODUTOS_DIA_FILE, JSON.stringify(prods, null, 2), 'utf8');
  } catch (err) {
    console.error('[Storage] Erro ao salvar produtos_dia.json:', err.message);
  }
}

// Limpeza de produtos antigos na inicialização (somente produtos de hoje a partir das 06:00)
function cleanupStaleProducts() {
  const current = getBrazilDateInfo();
  const prods = loadProdutosDia();
  const valid = prods.filter(p => {
    if (!p || !p.data) return false;
    if (p.data !== current.dateStr) return false;
    const h = parseInt(String(p.hora || '00').split(':')[0], 10);
    return h >= 6;
  });
  if (valid.length !== prods.length) {
    console.log(`[Reset Diário] 🧹 Purga executada: ${prods.length - valid.length} produtos de dias/horários anteriores removidos.`);
    saveProdutosDia(valid);
  }
}
cleanupStaleProducts();

// Cron: Reset Diário Exatamente à Meia-Noite
let midnightResetExecuted = false;
setInterval(() => {
  const info = getBrazilDateInfo();
  if (info.hour === 0 && info.minute === 0 && !midnightResetExecuted) {
    console.log('[Reset Diário] 🕛 MEIA-NOITE! Zerando todos os preços do dia anterior.');
    saveProdutosDia([]);
    midnightResetExecuted = true;
  }
  if (info.hour === 0 && info.minute > 5) {
    midnightResetExecuted = false;
  }
}, 10000);

// 4. Servir Arquivos Estáticos Oficiais da Vitrine e Painel
app.use(express.static(PUBLIC_DIR, { index: false }));

// Rota Principal: Vitrine Oficial Apple para o Telão
app.get(['/', '/index.html', '/vitrine', '/catalogo'], (req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, 'index.html'));
});

// Rota de Login: tela de login com a maçã
app.get(['/login', '/login.html'], (req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, 'login.html'));
});

// Rota do Painel do Administrador
app.get(['/admin', '/admin.html', '/painel', '/painel.html'], (req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, 'admin.html'));
});

// 5. Rotas de Gerenciamento do WhatsApp (Modal da Engrenagem)
app.get('/api/panel-state', async (req, res) => {
  try {
    const bridgeRes = await axios.get(`${BRIDGE_BASE}/api/status`, { timeout: 3500 }).catch(() => ({ data: {} }));
    const prods = loadProdutosDia();
    return res.json({
      whatsapp: bridgeRes.data,
      campaign: { running: false },
      capturedProductsCount: prods.length,
      currentDay: getBrazilDateInfo().dateStr
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

app.post('/api/start-campaign', (req, res) => res.json({ ok: true, status: 'started' }));
app.post('/api/stop-campaign', (req, res) => res.json({ ok: true, status: 'stopped' }));
app.post('/api/save-config', (req, res) => res.json({ ok: true }));
app.post('/api/logout-wa', async (req, res) => {
  try {
    await axios.post(`${BRIDGE_BASE}/api/logout`, {}, { timeout: 4000 }).catch(() => {});
    return res.json({ ok: true });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// 6. Endpoint de Ingestão de Mensagens WhatsApp
app.post('/api/internal/ingest-message', async (req, res) => {
  const { text, imageBase64, remoteJid, participantJid, pushName, groupName, timestamp } = req.body || {};
  
  // Regra Estrita de Horário: Apenas mensagens de HOJE a partir das 06:00 AM
  const msgDateInfo = getBrazilDateInfo(new Date((Number(timestamp) || Math.floor(Date.now() / 1000)) * 1000));
  const todayInfo = getBrazilDateInfo();

  if (msgDateInfo.dateStr !== todayInfo.dateStr || msgDateInfo.hour < 6) {
    return res.json({ ok: false, reason: 'MENSAGEM_FORA_DO_HORARIO_PERMITIDO' });
  }

  // Executar parser_listas.py para extrair os produtos brutos
  try {
    const inputPayload = JSON.stringify({
      text: text || '',
      supplier_name: pushName || groupName || remoteJid.split('@')[0],
      supplier_phone: remoteJid.split('@')[0],
      group_name: groupName || '',
      date_str: msgDateInfo.dateStr,
      msg_timestamp: Number(timestamp) || Math.floor(Date.now() / 1000)
    });

    const pythonScript = path.join(__dirname, 'parser_listas.py');
    const pyProcess = spawn('python3', [
      '-c',
      `
import sys, json
from parser_listas import parse_supplier_message
raw = sys.stdin.read()
data = json.loads(raw)
products = parse_supplier_message(
    data.get('text', ''),
    data.get('supplier_name', ''),
    data.get('supplier_phone', ''),
    group_name=data.get('group_name', ''),
    date_str=data.get('date_str', ''),
    msg_timestamp=data.get('msg_timestamp', 0)
)
print(json.dumps(products))
`
    ]);

    let output = '';
    pyProcess.stdout.on('data', chunk => { output += chunk; });
    
    pyProcess.on('close', code => {
      let rawList = [];
      try {
        rawList = JSON.parse(output.trim() || '[]');
      } catch {
        rawList = [];
      }

      if (!Array.isArray(rawList) || rawList.length === 0) {
        return res.json({ ok: true, parsedCount: 0 });
      }

      // Filtragem com o Guardião Anti-Erros (Sanity Bounds + Outlier Rejection)
      const existingProds = loadProdutosDia();
      const approvedProducts = [];

      for (const item of rawList) {
        const check = validateProduct(item, existingProds);
        if (check.valid && check.sanitizedProduct) {
          approvedProducts.push({
            ...check.sanitizedProduct,
            id: `${Date.now()}_${Math.floor(Math.random() * 1000)}_${approvedProducts.length}`
          });
        }
      }

      if (approvedProducts.length > 0) {
        // Atualizar lista de hoje: substituir ofertas mais antigas do mesmo modelo e fornecedor
        const updatedList = [...existingProds];
        for (const appItem of approvedProducts) {
          const idx = updatedList.findIndex(e => 
            e.modelo === appItem.modelo && 
            e.capacidade === appItem.capacidade && 
            e.fornecedor === appItem.fornecedor
          );
          if (idx >= 0) {
            updatedList[idx] = appItem;
          } else {
            updatedList.push(appItem);
          }
        }
        saveProdutosDia(updatedList);
        console.log(`[INGESTÃO] ✅ ${approvedProducts.length} produtos Apple novos validados e integrados ao catálogo de ${pushName || groupName}!`);
      }

      return res.json({
        ok: true,
        parsedCount: approvedProducts.length,
        supplierName: pushName || groupName || 'Fornecedor'
      });
    });

    pyProcess.stdin.write(inputPayload);
    pyProcess.stdin.end();

  } catch (err) {
    console.error('[INGESTÃO] Erro ao processar lista:', err.message);
    return res.status(500).json({ error: err.message });
  }
});

// 7. Rota de Produtos (/api/products)
// Combina os dados base com o menor custo de fornecedor validado do WhatsApp
app.get('/api/products', async (req, res) => {
  try {
    const upstreamRes = await axios.get(`${API_BASE}/api/products`, {
      headers: { ...req.headers, host: '127.0.0.1:3333' },
      timeout: 8000
    }).catch(() => null);

    const prodsDia = loadProdutosDia();
    const current = getBrazilDateInfo();

    // Se upstream respondeu, enriquecer com os menores custos reais de hoje
    if (upstreamRes && upstreamRes.data && Array.isArray(upstreamRes.data.data)) {
      const upstreamList = upstreamRes.data.data;
      
      // Mapear menor custo por modelo e capacidade a partir de produtos_dia
      const bestCostMap = new Map();
      for (const p of prodsDia) {
        if (!p.modelo || !p.preco) continue;
        const key = `${p.modelo.trim().toUpperCase()}_${String(p.capacidade || '-').trim().toUpperCase()}`;
        if (!bestCostMap.has(key) || p.preco < bestCostMap.get(key).preco) {
          bestCostMap.set(key, p);
        }
      }

      // Enriquecer produtos da vitrine
      const enriched = upstreamList.map(item => {
        const itemKey = `${String(item.name || '').trim().toUpperCase()}_${String(item.storage || item.capacity || '-').trim().toUpperCase()}`;
        const bestQuote = bestCostMap.get(itemKey);
        if (bestQuote) {
          return {
            ...item,
            fornecedorCost: bestQuote.preco,
            fornecedorName: bestQuote.fornecedor,
            fornecedorOrigem: bestQuote.origem,
            fornecedorHora: bestQuote.hora,
            fornecedorData: bestQuote.data
          };
        }
        return item;
      });

      return res.json({
        ...upstreamRes.data,
        latestDate: `${current.day}-${current.month}`,
        data: enriched
      });
    }

    // Se upstream falhar, servir produtos_dia formatados
    return res.json({
      success: true,
      total: prodsDia.length,
      latestDate: `${current.day}-${current.month}`,
      data: prodsDia
    });

  } catch (err) {
    return res.status(500).json({ error: 'Erro ao buscar catálogo', details: err.message });
  }
});

// 8. Demais Rotas da Aplicação Oficial -> Repasse transparente para a porta 3333
app.all(['/socket.io/*', '/api/*'], (req, res) => {
  proxy.web(req, res);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`=======================================================`);
  console.log(`🍎 BUSCADOR MUNDO APPLE OFICIAL (PORTA ${PORT})`);
  console.log(`👉 Vitrine Oficial do Telão: http://179.236.75.100:${PORT}/`);
  console.log(`👉 Painel Administrativo:     http://179.236.75.100:${PORT}/admin.html`);
  console.log(`🛡️ Guardião Anti-Erros:      ATIVO`);
  console.log(`🕛 Reset Diário Meia-Noite:   ATIVO`);
  console.log(`⏰ Captura a partir de:       06:00 AM`);
  console.log(`=======================================================`);
});
