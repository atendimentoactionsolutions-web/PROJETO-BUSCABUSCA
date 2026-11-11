import json
import os
import random
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORNECEDORES_FILE = os.path.join(BASE_DIR, "fornecedores.json")
HISTORICO_FILE = os.path.join(BASE_DIR, "historico_envios.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

PANEL_PORT = int(os.environ.get("PORT", "3050"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3051"))
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"

DEFAULT_NODE_WIN = r"C:\Users\Mundo Apple\.gemini\antigravity\scratch\node-env\node-v20.17.0-win-x64\node.exe"
NODE_EXE = DEFAULT_NODE_WIN if os.path.exists(DEFAULT_NODE_WIN) else (shutil.which("node") or "node")
BRIDGE_SCRIPT = os.path.join(BASE_DIR, "whatsapp_bridge.js")

DEFAULT_CONFIG = {
    "mensagem": (
        "Bom dia, tudo bem? Somos loja física aqui na Santa Efigênia. "
        "Gostaria de entrar na sua lista de transmissão."
    ),
    "delay_min": 8,
    "delay_max": 15,
    "auto_start_on_connect": False,
}

state_lock = threading.Lock()
campaign_state = {
    "running": False,
    "stop_requested": False,
    "current_supplier": None,
    "sent_session": 0,
    "errors_session": 0,
    "logs": [],
}


def add_panel_log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with state_lock:
        campaign_state["logs"].insert(0, line)
        if len(campaign_state["logs"]) > 200:
            campaign_state["logs"].pop()


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(data)
                return cfg
        except Exception:
            pass
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_fornecedores():
    if not os.path.exists(FORNECEDORES_FILE):
        return []
    with open(FORNECEDORES_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_fornecedores(lista):
    with open(FORNECEDORES_FILE, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def load_historico():
    if os.path.exists(HISTORICO_FILE):
        try:
            with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_historico(hist):
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def bridge_get(path):
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bridge_post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_bridge_running():
    try:
        bridge_get("/api/status")
        return
    except Exception:
        add_panel_log("Iniciando motor WhatsApp interno (porta 3051)...")
        env = dict(os.environ)
        env["BRIDGE_PORT"] = str(BRIDGE_PORT)
        subprocess.Popen(
            [NODE_EXE, BRIDGE_SCRIPT],
            cwd=BASE_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(15):
            time.sleep(1)
            try:
                bridge_get("/api/status")
                add_panel_log("Motor WhatsApp online! Nenhum envio será feito sem sua autorização.")
                return
            except Exception:
                pass


def render_message(template, supplier):
    msg = str(template or "").strip()
    msg = msg.replace("{nome}", str(supplier.get("nome", "")).strip())
    msg = msg.replace("{telefone}", str(supplier.get("telefone", "")).strip())
    msg = msg.replace("{localizacao}", str(supplier.get("localizacao", "")).strip())
    return msg


def campaign_worker():
    add_panel_log("🚀 Disparo AUTORIZADO pelo usuário! Iniciando fila de envios...")
    try:
        while True:
            with state_lock:
                if campaign_state["stop_requested"]:
                    campaign_state["running"] = False
                    campaign_state["current_supplier"] = None
                    break

            try:
                st = bridge_get("/api/status")
                if st.get("status") != "CONNECTED":
                    add_panel_log("⚠️ WhatsApp desconectado! Disparo pausado automaticamente.")
                    with state_lock:
                        campaign_state["running"] = False
                        campaign_state["current_supplier"] = None
                    return
            except Exception:
                time.sleep(2)
                continue

            fornecedores = load_fornecedores()
            historico = load_historico()
            cfg = load_config()

            proximo = None
            for f in fornecedores:
                tel = str(f.get("telefone", "")).strip()
                if not tel:
                    continue
                if f.get("ignorar"):
                    continue
                info = historico.get(tel, {})
                if info.get("status") != "ENVIADO":
                    proximo = f
                    break

            if not proximo:
                add_panel_log("🎉 Todos os fornecedores pendentes já receberam a mensagem!")
                with state_lock:
                    campaign_state["running"] = False
                    campaign_state["current_supplier"] = None
                return

            nome = proximo.get("nome", "Fornecedor")
            tel = str(proximo.get("telefone", "")).strip()
            msg_final = render_message(cfg.get("mensagem", ""), proximo)

            with state_lock:
                campaign_state["current_supplier"] = f"{nome} ({tel})"

            try:
                resp = bridge_post("/api/send", {"phone": tel, "name": nome, "message": msg_final})
                if resp.get("ok"):
                    historico[tel] = {
                        "nome": nome,
                        "telefone": tel,
                        "jid": resp.get("jid"),
                        "status": "ENVIADO",
                        "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    save_historico(historico)
                    with state_lock:
                        campaign_state["sent_session"] += 1
                    add_panel_log(f"✅ Enviado para {nome} ({tel})")
                else:
                    historico[tel] = {
                        "nome": nome,
                        "telefone": tel,
                        "status": "ERRO",
                        "erro": resp.get("error"),
                        "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    save_historico(historico)
                    with state_lock:
                        campaign_state["errors_session"] += 1
                    add_panel_log(f"❌ Erro em {nome} ({tel}): {resp.get('error')}")
            except urllib.error.HTTPError as he:
                err_txt = he.read().decode("utf-8", errors="ignore")
                historico[tel] = {
                    "nome": nome,
                    "telefone": tel,
                    "status": "ERRO",
                    "erro": err_txt,
                    "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                save_historico(historico)
                with state_lock:
                    campaign_state["errors_session"] += 1
                add_panel_log(f"❌ Falha HTTP em {nome} ({tel}): {err_txt}")
            except Exception as e:
                with state_lock:
                    campaign_state["errors_session"] += 1
                add_panel_log(f"❌ Exceção em {nome} ({tel}): {e}")

            d_min = max(2, int(cfg.get("delay_min", 8)))
            d_max = max(d_min, int(cfg.get("delay_max", 15)))
            wait_sec = round(random.uniform(d_min, d_max), 1)

            # Aguardar respeitando eventual pedido de pausa imediata
            steps = int(wait_sec * 2)
            for _ in range(steps):
                with state_lock:
                    if campaign_state["stop_requested"]:
                        break
                time.sleep(0.5)

        add_panel_log("⏸️ Disparo pausado pelo usuário.")
    finally:
        with state_lock:
            campaign_state["running"] = False
            campaign_state["current_supplier"] = None


HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Painel Python VPS — Automação WhatsApp & Fornecedores</title>
  <style>
    :root {
      --bg: #0b1120;
      --card: #111827;
      --card-border: #1f2937;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #10b981;
      --accent-hover: #059669;
      --warn: #f59e0b;
      --danger: #ef4444;
      --blue: #3b82f6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 20px;
    }
    .container {
      max-width: 1280px;
      margin: 0 auto;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 20px;
    }
    header h1 {
      margin: 0;
      font-size: 22px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .badge {
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }
    .badge.CONNECTED { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #059669; }
    .badge.QR_READY { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #d97706; }
    .badge.DISCONNECTED, .badge.CONNECTING { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid #475569; }

    .grid-top {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }
    @media (max-width: 920px) {
      .grid-top { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 20px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }
    .card h2 {
      margin: 0 0 14px 0;
      font-size: 16px;
      color: #e2e8f0;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .qr-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 260px;
      background: #0f172a;
      border-radius: 12px;
      padding: 16px;
      text-align: center;
    }
    .qr-wrapper img {
      width: 240px;
      height: 240px;
      border-radius: 10px;
      background: #fff;
      padding: 8px;
    }
    textarea {
      width: 100%;
      min-height: 95px;
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 10px;
      color: #f8fafc;
      padding: 12px;
      font-size: 14px;
      line-height: 1.45;
      resize: vertical;
    }
    input[type="number"], input[type="text"] {
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      color: #f8fafc;
      padding: 8px 12px;
      font-size: 14px;
    }
    .whatsapp-preview {
      background: #064e3b;
      color: #ecfdf5;
      padding: 12px 16px;
      border-radius: 12px 12px 12px 2px;
      font-size: 14px;
      line-height: 1.45;
      margin-top: 10px;
      border: 1px solid #047857;
    }
    .btn-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    button {
      cursor: pointer;
      border: none;
      border-radius: 9px;
      padding: 10px 16px;
      font-size: 14px;
      font-weight: 600;
      transition: all 0.15s;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn-green { background: var(--accent); color: #052e16; }
    .btn-green:hover:not(:disabled) { background: var(--accent-hover); color: #fff; }
    .btn-warn { background: var(--warn); color: #1e1b4b; }
    .btn-danger { background: var(--danger); color: #fff; }
    .btn-blue { background: var(--blue); color: #fff; }
    .btn-ghost { background: #1e293b; color: #cbd5e1; border: 1px solid #334155; }
    .btn-ghost:hover:not(:disabled) { background: #334155; }

    .stats-bar {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 20px;
    }
    .stat-box {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 14px 18px;
    }
    .stat-box .label { font-size: 12px; color: var(--muted); text-transform: uppercase; }
    .stat-box .val { font-size: 26px; font-weight: 700; margin-top: 4px; }

    .grid-bottom {
      display: grid;
      grid-template-columns: 1.5fr 1fr;
      gap: 20px;
    }
    @media (max-width: 980px) {
      .grid-bottom { grid-template-columns: 1fr; }
    }
    .table-wrap {
      max-height: 440px;
      overflow-y: auto;
      border: 1px solid #1e293b;
      border-radius: 10px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 9px 12px;
      text-align: left;
      border-bottom: 1px solid #1e293b;
    }
    th {
      background: #0f172a;
      color: #94a3b8;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .pill {
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
    }
    .pill.PENDENTE { background: rgba(148,163,184,0.15); color: #cbd5e1; }
    .pill.ENVIADO { background: rgba(16,185,129,0.2); color: #34d399; }
    .pill.ERRO { background: rgba(239,68,68,0.2); color: #f87171; }
    .logs-box {
      background: #070b14;
      border: 1px solid #1e293b;
      border-radius: 10px;
      height: 440px;
      overflow-y: auto;
      padding: 12px;
      font-family: Consolas, monospace;
      font-size: 12.5px;
      color: #94a3b8;
    }
    .logs-box div { margin-bottom: 5px; border-bottom: 1px dashed rgba(255,255,255,0.04); padding-bottom: 3px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>🐍 Painel Python VPS — Central de Disparos WhatsApp</h1>
        <div style="font-size:13px;color:var(--muted);margin-top:4px;">
          Modo Seguro Ativo: <strong>Nenhum envio ocorre sem você clicar em "Autorizar e Iniciar Disparo"</strong>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;">
        <span id="waStatusBadge" class="badge CONNECTING">Verificando...</span>
        <span id="campaignBadge" class="badge DISCONNECTED">DISPARO PARADO</span>
      </div>
    </header>

    <div class="stats-bar">
      <div class="stat-box">
        <div class="label">Total na Lista</div>
        <div class="val" id="stTotal">204</div>
      </div>
      <div class="stat-box">
        <div class="label">Pendentes</div>
        <div class="val" id="stPending" style="color:#fbbf24;">204</div>
      </div>
      <div class="stat-box">
        <div class="label">Enviados com Sucesso</div>
        <div class="val" id="stSent" style="color:#34d399;">0</div>
      </div>
      <div class="stat-box">
        <div class="label">Erros / Falhas</div>
        <div class="val" id="stErrors" style="color:#f87171;">0</div>
      </div>
    </div>

    <div class="grid-top">
      <!-- Card 1: Conexão WhatsApp -->
      <div class="card">
        <h2>
          <span>1. Conexão do WhatsApp</span>
          <button class="btn-ghost" onclick="logoutWhatsApp()" style="padding:5px 10px;font-size:12px;">Trocar / Desconectar</button>
        </h2>
        <div class="qr-wrapper" id="qrContainer">
          <div style="color:var(--muted);">Carregando QR Code...</div>
        </div>
        <div id="connectedInfo" style="margin-top:12px;font-size:13px;color:#cbd5e1;text-align:center;"></div>
      </div>

      <!-- Card 2: Mensagem & Controle Manual -->
      <div class="card">
        <h2>
          <span>2. Mensagem Exata & Autorização de Disparo</span>
          <span style="font-size:12px;color:var(--muted);font-weight:normal;">Variáveis opcionais: <code>{nome}</code>, <code>{localizacao}</code></span>
        </h2>

        <label style="font-size:13px;color:var(--muted);display:block;margin-bottom:6px;">Texto que será enviado para cada fornecedor:</label>
        <textarea id="msgInput" oninput="updatePreview()"></textarea>

        <div style="font-size:12px;color:var(--muted);margin-top:8px;">Pré-visualização exata no WhatsApp:</div>
        <div class="whatsapp-preview" id="msgPreview"></div>

        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;margin-top:14px;">
          <div>
            <label style="font-size:12px;color:var(--muted);">Intervalo Mínimo (seg):</label><br/>
            <input type="number" id="delayMin" value="8" min="2" max="120" style="width:90px;margin-top:4px;" />
          </div>
          <div>
            <label style="font-size:12px;color:var(--muted);">Intervalo Máximo (seg):</label><br/>
            <input type="number" id="delayMax" value="15" min="3" max="300" style="width:90px;margin-top:4px;" />
          </div>
          <div style="flex:1;min-width:220px;">
            <label style="font-size:12px;color:var(--muted);">Enviar teste avulso para um número:</label>
            <div style="display:flex;gap:6px;margin-top:4px;">
              <input type="text" id="testPhone" placeholder="Ex: 5511999999999" style="flex:1;" />
              <button class="btn-ghost" onclick="sendTest()">🧪 Testar</button>
            </div>
          </div>
        </div>

        <div class="btn-row">
          <button class="btn-blue" onclick="saveConfig()">💾 Salvar Mensagem</button>
          <button class="btn-green" id="btnStart" onclick="startCampaign()">🚀 Autorizar e Iniciar Disparo</button>
          <button class="btn-warn" id="btnStop" onclick="stopCampaign()" disabled>⏸️ Pausar Disparo</button>
          <button class="btn-ghost" onclick="resetHistory()">🔄 Zerar Histórico de Enviados</button>
        </div>
        <div id="currentSending" style="margin-top:12px;font-size:13px;color:#38bdf8;font-weight:600;"></div>
      </div>
    </div>

    <div class="grid-bottom">
      <!-- Lista de Fornecedores -->
      <div class="card">
        <h2>
          <span>3. Lista de Contatos (204 Fornecedores)</span>
          <input type="text" id="searchInput" placeholder="Buscar fornecedor ou telefone..." oninput="renderTable()" style="width:240px;font-size:12px;padding:6px 10px;" />
        </h2>
        <div style="display:flex;gap:8px;margin-bottom:10px;">
          <input type="text" id="newSupName" placeholder="Nome do contato" style="flex:1;font-size:12px;" />
          <input type="text" id="newSupPhone" placeholder="Telefone (ex: 5511999999999)" style="flex:1;font-size:12px;" />
          <button class="btn-ghost" onclick="addSupplier()" style="padding:6px 12px;font-size:12px;">+ Adicionar Contato</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Fornecedor</th>
                <th>Telefone</th>
                <th>Dias c/ Lista</th>
                <th>Status</th>
                <th>Ação</th>
              </tr>
            </thead>
            <tbody id="supTableBody"></tbody>
          </table>
        </div>
      </div>

      <!-- Logs em tempo real -->
      <div class="card">
        <h2>
          <span>4. Logs de Execução em Tempo Real</span>
        </h2>
        <div class="logs-box" id="logsContainer"></div>
      </div>
    </div>
  </div>

  <script>
    let allSuppliers = [];
    let historyMap = {};
    let configLoaded = false;

    function updatePreview() {
      const raw = document.getElementById('msgInput').value || '';
      const sample = raw
        .replace(/\\{nome\\}/g, 'ADELS MUNDO CEL')
        .replace(/\\{telefone\\}/g, '5511962254545')
        .replace(/\\{localizacao\\}/g, 'SHOPPING ORIENTAL');
      document.getElementById('msgPreview').textContent = sample;
    }

    async function loadState() {
      try {
        const res = await fetch('/api/panel-state');
        const data = await res.json();

        if (!configLoaded && data.config) {
          document.getElementById('msgInput').value = data.config.mensagem || '';
          document.getElementById('delayMin').value = data.config.delay_min || 8;
          document.getElementById('delayMax').value = data.config.delay_max || 15;
          updatePreview();
          configLoaded = true;
        }

        allSuppliers = data.fornecedores || [];
        historyMap = data.historico || {};

        document.getElementById('stTotal').textContent = data.stats.total;
        document.getElementById('stPending').textContent = data.stats.pending;
        document.getElementById('stSent').textContent = data.stats.sent;
        document.getElementById('stErrors').textContent = data.stats.errors;

        // Status WA
        const wa = data.whatsapp || {};
        const waBadge = document.getElementById('waStatusBadge');
        waBadge.className = 'badge ' + (wa.status || 'CONNECTING');
        waBadge.textContent = wa.status === 'CONNECTED'
          ? 'WHATSAPP CONECTADO'
          : (wa.status === 'QR_READY' ? 'AGUARDANDO QR CODE' : (wa.status || 'CONECTANDO'));

        const qrBox = document.getElementById('qrContainer');
        const connInfo = document.getElementById('connectedInfo');
        if (wa.status === 'CONNECTED') {
          qrBox.innerHTML = '<div style="font-size:48px;">✅</div><div style="font-weight:700;color:#34d399;margin-top:8px;">Aparelho Conectado!</div><div style="font-size:12px;color:#94a3b8;margin-top:4px;">Pronto. O disparo só iniciará quando você clicar no botão verde ao lado.</div>';
          connInfo.textContent = wa.user ? ('Conectado como: ' + wa.user.name + ' (' + wa.user.id + ')') : '';
        } else if (wa.status === 'QR_READY' && wa.qrDataUrl) {
          qrBox.innerHTML = '<img src="' + wa.qrDataUrl + '" alt="QR Code WhatsApp" /><div style="font-size:12px;color:#fbbf24;margin-top:8px;">Escaneie no WhatsApp > Aparelhos conectados</div>';
          connInfo.textContent = '';
        } else {
          qrBox.innerHTML = '<div style="color:#94a3b8;">Gerando sessão / QR Code...</div>';
          connInfo.textContent = '';
        }

        // Status Campanha
        const camp = data.campaign || {};
        const cBadge = document.getElementById('campaignBadge');
        if (camp.running) {
          cBadge.className = 'badge CONNECTED';
          cBadge.textContent = 'DISPARANDO EM FILA...';
          document.getElementById('btnStart').disabled = true;
          document.getElementById('btnStop').disabled = false;
          document.getElementById('currentSending').textContent = camp.current_supplier
            ? ('📤 Processando agora: ' + camp.current_supplier)
            : '⏳ Aguardando intervalo seguro entre mensagens...';
        } else {
          cBadge.className = 'badge DISCONNECTED';
          cBadge.textContent = 'AGUARDANDO AUTORIZAÇÃO';
          document.getElementById('btnStart').disabled = false;
          document.getElementById('btnStop').disabled = true;
          document.getElementById('currentSending').textContent = '';
        }

        renderTable();

        const logs = data.logs || [];
        document.getElementById('logsContainer').innerHTML = logs.map(l => '<div>' + l + '</div>').join('');
      } catch (e) {}
    }

    function renderTable() {
      const q = (document.getElementById('searchInput').value || '').toLowerCase().trim();
      const tbody = document.getElementById('supTableBody');
      let html = '';
      for (let i = 0; i < allSuppliers.length; i++) {
        const s = allSuppliers[i];
        const nome = s.nome || '';
        const tel = String(s.telefone || '');
        if (q && !nome.toLowerCase().includes(q) && !tel.includes(q)) continue;
        const h = historyMap[tel] || {};
        const st = h.status || 'PENDENTE';
        html += '<tr>' +
          '<td>' + (i + 1) + '</td>' +
          '<td><strong>' + nome + '</strong></td>' +
          '<td><code>' + tel + '</code></td>' +
          '<td style="color:#94a3b8;font-size:12px;">' + (s.datas || '-') + '</td>' +
          '<td><span class="pill ' + st + '">' + st + '</span></td>' +
          '<td><button class="btn-ghost" style="padding:4px 8px;font-size:11px;" onclick="sendSingle(\\'' + tel + '\\', \\'' + nome.replace(/'/g, '') + '\\')">Enviar 1x</button></td>' +
        '</tr>';
      }
      tbody.innerHTML = html;
    }

    async function saveConfig() {
      const payload = {
        mensagem: document.getElementById('msgInput').value,
        delay_min: Number(document.getElementById('delayMin').value || 8),
        delay_max: Number(document.getElementById('delayMax').value || 15)
      };
      await fetch('/api/save-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await loadState();
    }

    async function startCampaign() {
      await saveConfig();
      const res = await fetch('/api/start-campaign', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) alert(data.error || 'Não foi possível iniciar.');
      await loadState();
    }

    async function stopCampaign() {
      await fetch('/api/stop-campaign', { method: 'POST' });
      await loadState();
    }

    async function resetHistory() {
      if (!confirm('Deseja zerar o histórico de enviados para poder disparar para todos novamente?')) return;
      await fetch('/api/reset-history', { method: 'POST' });
      await loadState();
    }

    async function logoutWhatsApp() {
      if (!confirm('Deseja desconectar a sessão atual e gerar um novo QR Code?')) return;
      await fetch('/api/logout-wa', { method: 'POST' });
      await loadState();
    }

    async function sendTest() {
      const phone = document.getElementById('testPhone').value.trim();
      if (!phone) return alert('Digite um número para testar.');
      await saveConfig();
      const res = await fetch('/api/send-single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, name: 'Teste Manual' })
      });
      const d = await res.json();
      if (d.ok) alert('Mensagem de teste enviada com sucesso!');
      else alert('Erro: ' + (d.error || 'Falha ao enviar'));
      await loadState();
    }

    async function sendSingle(phone, name) {
      await saveConfig();
      const res = await fetch('/api/send-single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, name })
      });
      const d = await res.json();
      if (!d.ok) alert('Erro: ' + (d.error || 'Falha ao enviar'));
      await loadState();
    }

    async function addSupplier() {
      const nome = document.getElementById('newSupName').value.trim();
      const telefone = document.getElementById('newSupPhone').value.trim();
      if (!nome || !telefone) return alert('Preencha nome e telefone.');
      await fetch('/api/add-supplier', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nome, telefone })
      });
      document.getElementById('newSupName').value = '';
      document.getElementById('newSupPhone').value = '';
      await loadState();
    }

    setInterval(loadState, 2000);
    loadState();
  </script>
</body>
</html>
"""


class PanelHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        return json.loads(raw) if raw else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/panel-state":
            cfg = load_config()
            fornecedores = load_fornecedores()
            historico = load_historico()
            try:
                wa = bridge_get("/api/status")
            except Exception:
                wa = {"status": "CONNECTING", "logs": []}

            sent = sum(1 for f in fornecedores if historico.get(str(f.get("telefone", "")).strip(), {}).get("status") == "ENVIADO")
            errors = sum(1 for f in fornecedores if historico.get(str(f.get("telefone", "")).strip(), {}).get("status") == "ERRO")
            total = len(fornecedores)
            pending = max(0, total - sent)

            with state_lock:
                camp_copy = {
                    "running": campaign_state["running"],
                    "current_supplier": campaign_state["current_supplier"],
                    "sent_session": campaign_state["sent_session"],
                    "errors_session": campaign_state["errors_session"],
                }
                combined_logs = list(campaign_state["logs"][:60]) + list(wa.get("logs", [])[:30])

            self._send_json({
                "config": cfg,
                "fornecedores": fornecedores,
                "historico": historico,
                "whatsapp": wa,
                "campaign": camp_copy,
                "stats": {
                    "total": total,
                    "pending": pending,
                    "sent": sent,
                    "errors": errors,
                },
                "logs": combined_logs[:80],
            })
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/save-config":
            body = self._read_body_json()
            cfg = load_config()
            if "mensagem" in body:
                cfg["mensagem"] = str(body["mensagem"])
            if "delay_min" in body:
                cfg["delay_min"] = max(2, int(body["delay_min"]))
            if "delay_max" in body:
                cfg["delay_max"] = max(cfg["delay_min"], int(body["delay_max"]))
            save_config(cfg)
            add_panel_log("💾 Configurações e mensagem atualizadas no painel.")
            self._send_json({"ok": True, "config": cfg})
            return

        if parsed.path == "/api/start-campaign":
            try:
                wa = bridge_get("/api/status")
                if wa.get("status") != "CONNECTED":
                    self._send_json({"ok": False, "error": "Conecte o WhatsApp pelo QR Code antes de iniciar o disparo!"})
                    return
            except Exception:
                self._send_json({"ok": False, "error": "Motor WhatsApp ainda está iniciando."})
                return

            with state_lock:
                if campaign_state["running"]:
                    self._send_json({"ok": True, "already_running": True})
                    return
                campaign_state["running"] = True
                campaign_state["stop_requested"] = False

            t = threading.Thread(target=campaign_worker, daemon=True)
            t.start()
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/stop-campaign":
            with state_lock:
                campaign_state["stop_requested"] = True
            add_panel_log("⏸️ Solicitação de pausa recebida. Parando fila...")
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/reset-history":
            save_historico({})
            add_panel_log("🔄 Histórico de envios zerado. Todos os contatos voltaram para PENDENTE.")
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/logout-wa":
            with state_lock:
                campaign_state["stop_requested"] = True
            try:
                bridge_post("/api/logout", {})
            except Exception:
                pass
            add_panel_log("🔌 Sessão do WhatsApp desconectada. Aguarde o novo QR Code.")
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/send-single":
            body = self._read_body_json()
            phone = str(body.get("phone", "")).strip()
            name = str(body.get("name", "Contato")).strip()
            cfg = load_config()
            msg = render_message(cfg.get("mensagem", ""), {"nome": name, "telefone": phone, "localizacao": ""})
            try:
                resp = bridge_post("/api/send", {"phone": phone, "name": name, "message": msg})
                if resp.get("ok"):
                    hist = load_historico()
                    hist[phone] = {
                        "nome": name,
                        "telefone": phone,
                        "jid": resp.get("jid"),
                        "status": "ENVIADO",
                        "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    save_historico(hist)
                    add_panel_log(f"✅ Envio individual concluído para {name} ({phone})")
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "error": resp.get("error")})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/add-supplier":
            body = self._read_body_json()
            nome = str(body.get("nome", "")).strip()
            telefone = "".join(ch for ch in str(body.get("telefone", "")) if ch.isdigit())
            if not nome or not telefone:
                self._send_json({"ok": False, "error": "Nome e telefone obrigatórios."})
                return
            lista = load_fornecedores()
            lista.insert(0, {
                "id": len(lista) + 1,
                "nome": nome,
                "telefone": telefone,
                "localizacao": "Manual",
                "datas": "Manual",
            })
            save_fornecedores(lista)
            add_panel_log(f"➕ Contato adicionado à lista: {nome} ({telefone})")
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not found"}, status=404)


def main():
    load_config()
    ensure_bridge_running()
    add_panel_log(f"Painel Web Python rodando em http://0.0.0.0:{PANEL_PORT}")
    server = ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), PanelHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
