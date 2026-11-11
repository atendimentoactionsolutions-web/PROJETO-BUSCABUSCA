import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from parser_listas import parse_supplier_message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORNECEDORES_FILE = os.path.join(BASE_DIR, "fornecedores.json")
HISTORICO_FILE = os.path.join(BASE_DIR, "historico_envios.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PRODUTOS_FILE = os.path.join(BASE_DIR, "produtos_dia.json")

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
products_lock = threading.Lock()

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
        if len(campaign_state["logs"]) > 250:
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


def load_produtos():
    with products_lock:
        if os.path.exists(PRODUTOS_FILE):
            try:
                with open(PRODUTOS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []


def save_produtos(lista):
    with products_lock:
        with open(PRODUTOS_FILE, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)


def identify_supplier(participant_jid, remote_jid, push_name, group_name, msg_text=""):
    """
    Cruza o número do remetente ou nome do grupo/comunidade com a base dos 204 fornecedores
    para identificar automaticamente qual fornecedor enviou a lista.
    """
    fornecedores = load_fornecedores()
    raw_sender = re.sub(r"\D", "", (participant_jid or remote_jid or "").split("@")[0])

    # 1. Tentar casar pelo número de telefone (últimos 8 ou 9 dígitos)
    if len(raw_sender) >= 8:
        tail8 = raw_sender[-8:]
        for f in fornecedores:
            ftel = re.sub(r"\D", "", str(f.get("telefone", "")))
            if len(ftel) >= 8 and ftel[-8:] == tail8:
                return f.get("nome"), ftel

    # 2. Tentar casar pelo nome do grupo/comunidade, pushName ou cabeçalho da mensagem
    head_text = " ".join((msg_text or "").splitlines()[:4])
    combined_title = f"{group_name} {push_name} {head_text}".upper()
    for f in fornecedores:
        fnome = str(f.get("nome", "")).upper().strip()
        if not fnome:
            continue
        base_name = re.sub(r"\s+\d{3,4}$", "", fnome).strip()
        if len(base_name) >= 4 and base_name in combined_title:
            return f.get("nome"), re.sub(r"\D", "", str(f.get("telefone", "")))

    # 3. Fallback: priorizar nome do grupo/comunidade ou pushName (evitando IDs numéricos puros)
    clean_group = group_name if (group_name and not group_name.isdigit() and "Arquivado" not in group_name) else ""
    clean_push = push_name if (push_name and not push_name.isdigit()) else ""
    clean_fallback = (clean_group or clean_push or group_name or raw_sender or "FORNECEDOR ARQUIVADO").upper().strip()
    return clean_fallback, raw_sender


def upsert_parsed_products(new_items):
    if not new_items:
        return 0
    atuais = load_produtos()
    # Chave única por Data + Fornecedor + Modelo + Capacidade + Cor para atualizar preço e condição no mesmo dia
    index_map = {}
    for i, item in enumerate(atuais):
        k = (
            f"{item.get('data')}|{item.get('fornecedor')}|{item.get('modelo')}|"
            f"{item.get('capacidade')}|{item.get('cor')}"
        )
        index_map[k] = i

    count = 0
    for item in new_items:
        k = (
            f"{item.get('data')}|{item.get('fornecedor')}|{item.get('modelo')}|"
            f"{item.get('capacidade')}|{item.get('cor')}"
        )
        item["id"] = f"{int(time.time() * 1000)}_{random.randint(100, 999)}_{count}"
        if k in index_map:
            atuais[index_map[k]] = item
        else:
            index_map[k] = len(atuais)
            atuais.append(item)
        count += 1

    # Manter no máximo 25.000 ofertas recentes para performance
    if len(atuais) > 25000:
        atuais = atuais[-25000:]

    save_produtos(atuais)
    return count


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
        add_panel_log("Iniciando motor WhatsApp + Auto-Comunidades + Captura de Listas (porta 3051)...")
        env = dict(os.environ)
        env["BRIDGE_PORT"] = str(BRIDGE_PORT)
        env["PORT"] = str(PANEL_PORT)
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
                add_panel_log("Motor WhatsApp online! Auto-Join, Auto-Arquivamento e Captura de Preços ATIVOS.")
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
    add_panel_log("[INICIO] Disparo AUTORIZADO pelo usuário! Iniciando fila de envios...")
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
                    add_panel_log("[AVISO] WhatsApp desconectado! Disparo pausado automaticamente.")
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
                add_panel_log("[CONCLUIDO] Todos os fornecedores pendentes já receberam a mensagem!")
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
                    add_panel_log(f"[OK] Enviado para {nome} ({tel})")
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
                    add_panel_log(f"[ERRO] Erro em {nome} ({tel}): {resp.get('error')}")
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
                add_panel_log(f"[ERRO] Falha HTTP em {nome} ({tel}): {err_txt}")
            except Exception as e:
                with state_lock:
                    campaign_state["errors_session"] += 1
                add_panel_log(f"[ERRO] Exceção em {nome} ({tel}): {e}")

            d_min = max(2, int(cfg.get("delay_min", 8)))
            d_max = max(d_min, int(cfg.get("delay_max", 15)))
            wait_sec = round(random.uniform(d_min, d_max), 1)

            steps = int(wait_sec * 2)
            for _ in range(steps):
                with state_lock:
                    if campaign_state["stop_requested"]:
                        break
                time.sleep(0.5)

        add_panel_log("[PAUSA] Disparo pausado pelo usuário.")
    finally:
        with state_lock:
            campaign_state["running"] = False
            campaign_state["current_supplier"] = None


# ==============================================================================
# PÁGINA 1 (ABERTA / PÚBLICA): BUSCADOR DOS MELHORES PREÇOS DO DIA (/)
# ==============================================================================
BUSCADOR_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Buscador de Preços — Melhores Custos do Dia</title>
  <style>
    :root {
      --bg: #090d16;
      --card: #111827;
      --border: #1e293b;
      --text: #f8fafc;
      --muted: #94a3b8;
      --green: #10b981;
      --green-dark: #059669;
      --blue: #3b82f6;
      --purple: #8b5cf6;
      --amber: #f59e0b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .navbar {
      background: #0f172a;
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 20px;
      font-weight: 800;
      color: #fff;
    }
    .brand span.tag {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
      font-size: 11px;
      padding: 4px 10px;
      border-radius: 999px;
      font-weight: 700;
    }
    .nav-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .btn-admin {
      background: #1e293b;
      color: #cbd5e1;
      border: 1px solid #334155;
      padding: 8px 14px;
      border-radius: 9px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      transition: all 0.15s;
    }
    .btn-admin:hover { background: #334155; color: #fff; }

    .container {
      max-width: 1360px;
      margin: 0 auto;
      padding: 20px;
    }
    .stats-row {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 18px;
    }
    @media (max-width: 800px) {
      .stats-row { grid-template-columns: repeat(2, 1fr); }
    }
    .stat-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 18px;
    }
    .stat-card .lbl { font-size: 12px; color: var(--muted); text-transform: uppercase; }
    .stat-card .val { font-size: 24px; font-weight: 800; margin-top: 4px; }

    .cat-tabs {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 8px;
      margin-bottom: 16px;
    }
    .cat-btn {
      background: #111827;
      color: #cbd5e1;
      border: 1px solid #1e293b;
      padding: 10px 16px;
      border-radius: 999px;
      font-size: 13.5px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s;
    }
    .cat-btn:hover { border-color: #3b82f6; color: #fff; }
    .cat-btn.active {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      border-color: #3b82f6;
      box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
    }
    .cat-count {
      background: rgba(255,255,255,0.12);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
    }

    .filters-bar {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      margin-bottom: 20px;
    }
    .search-box {
      flex: 2;
      min-width: 240px;
      background: #090d16;
      border: 1px solid #334155;
      color: #fff;
      padding: 10px 14px;
      border-radius: 10px;
      font-size: 14px;
    }
    select {
      background: #090d16;
      border: 1px solid #334155;
      color: #fff;
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 13px;
    }
    .mode-toggle {
      display: inline-flex;
      background: #090d16;
      border: 1px solid #334155;
      border-radius: 10px;
      overflow: hidden;
    }
    .mode-toggle button {
      background: transparent;
      color: #94a3b8;
      border: none;
      padding: 9px 14px;
      font-size: 12.5px;
      font-weight: 700;
      cursor: pointer;
    }
    .mode-toggle button.active {
      background: var(--green);
      color: #052e16;
    }

    .products-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 14px;
    }
    .prod-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.12s, border-color 0.12s;
    }
    .prod-card:hover {
      border-color: #334155;
      transform: translateY(-2px);
    }
    .prod-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
    }
    .prod-cat {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      padding: 3px 8px;
      border-radius: 6px;
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
    }
    .prod-cat.seminovo {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
    }
    .prod-title {
      font-size: 16px;
      font-weight: 800;
      margin: 10px 0 6px 0;
      color: #f8fafc;
    }
    .prod-specs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
    }
    .spec-pill {
      background: #1e293b;
      color: #cbd5e1;
      font-size: 12px;
      padding: 3px 9px;
      border-radius: 6px;
      font-weight: 600;
    }
    .price-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      padding-top: 10px;
      border-top: 1px solid #1e293b;
      margin-bottom: 12px;
    }
    .price-lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; }
    .price-val { font-size: 23px; font-weight: 900; color: #34d399; }

    .supp-box {
      background: #090d16;
      border: 1px solid #1e293b;
      border-radius: 10px;
      padding: 10px 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }
    .supp-name { font-size: 13px; font-weight: 700; color: #e2e8f0; }
    .supp-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .btn-wa {
      background: #16a34a;
      color: #fff;
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 12.5px;
      font-weight: 700;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }
    .btn-wa:hover { background: #15803d; }
    .empty-state {
      background: var(--card);
      border: 1px dashed #334155;
      border-radius: 14px;
      padding: 48px 20px;
      text-align: center;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <nav class="navbar">
    <div class="brand">
      <span>Buscador de Preços</span>
      <span class="tag">MELHORES CUSTOS DO DIA</span>
    </div>
    <div class="nav-actions">
      <span id="lastUpdateText" style="font-size:12px;color:var(--muted);">Atualizando...</span>
      <a href="/admin" class="btn-admin">Painel Administrativo (WhatsApp)</a>
    </div>
  </nav>

  <div class="container">
    <div class="stats-row">
      <div class="stat-card">
        <div class="lbl">Ofertas Capturadas</div>
        <div class="val" id="totalProducts">0</div>
      </div>
      <div class="stat-card">
        <div class="lbl">Modelos c/ Menor Preço</div>
        <div class="val" id="bestDealsCount" style="color:#34d399;">0</div>
      </div>
      <div class="stat-card">
        <div class="lbl">Fornecedores c/ Lista</div>
        <div class="val" id="activeSuppliersCount" style="color:#60a5fa;">0</div>
      </div>
      <div class="stat-card">
        <div class="lbl">Comunidades Monitoradas</div>
        <div class="val" id="monitoredCommunities" style="color:#c084fc;">0</div>
      </div>
    </div>

    <!-- Abas de Categorias -->
    <div class="cat-tabs" id="catTabs"></div>

    <!-- Barra de Filtros -->
    <div class="filters-bar">
      <input type="text" id="qInput" class="search-box" placeholder="Buscar modelo: iPhone 16 Pro Max 256GB, MacBook M3 16GB, iPad 10, AirPods..." oninput="renderProducts()" />

      <select id="dateFilter" onchange="renderProducts()">
        <option value="ALL">Todas as datas</option>
      </select>

      <select id="capFilter" onchange="renderProducts()">
        <option value="ALL">[SALVO] Todas as capacidades</option>
      </select>

      <select id="colorFilter" onchange="renderProducts()">
        <option value="ALL">Todas as cores</option>
      </select>

      <select id="suppFilter" onchange="renderProducts()">
        <option value="ALL">Todos os fornecedores</option>
      </select>

      <div class="mode-toggle">
        <button id="btnBestMode" class="active" onclick="setMode('BEST')">Menor Preço do Dia</button>
        <button id="btnAllMode" onclick="setMode('ALL')">Todas as Ofertas</button>
      </div>
    </div>

    <div id="productsContainer"></div>
  </div>

  <script>
    const CATEGORIES = [
      { key: 'ALL', label: 'Todos' },
      { key: 'iPhones', label: 'iPhones' },
      { key: 'iPhones Seminovos', label: 'iPhones Seminovos' },
      { key: 'MacBook', label: 'MacBook' },
      { key: 'iPad', label: 'iPad' },
      { key: 'Apple Watch', label: 'Apple Watch' },
      { key: 'AirPods', label: 'AirPods' },
      { key: 'Androids', label: 'Androids' },
      { key: 'Acessórios & Outros', label: '[CONEXAO] Acessórios & Outros' }
    ];

    let allProducts = [];
    let selectedCategory = 'ALL';
    let viewMode = 'BEST'; // 'BEST' = apenas o menor valor de custo de cada produto/capacidade/cor | 'ALL' = todos

    function setMode(m) {
      viewMode = m;
      document.getElementById('btnBestMode').className = m === 'BEST' ? 'active' : '';
      document.getElementById('btnAllMode').className = m === 'ALL' ? 'active' : '';
      renderProducts();
    }

    function selectCategory(catKey) {
      selectedCategory = catKey;
      renderCategoryTabs();
      renderProducts();
    }

    function formatBRL(val) {
      return Number(val || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    function renderCategoryTabs() {
      const counts = { ALL: allProducts.length };
      for (const p of allProducts) {
        counts[p.categoria] = (counts[p.categoria] || 0) + 1;
      }
      const wrap = document.getElementById('catTabs');
      wrap.innerHTML = CATEGORIES.map(c => {
        const cnt = counts[c.key] || 0;
        const cls = selectedCategory === c.key ? 'cat-btn active' : 'cat-btn';
        return '<button class="' + cls + '" onclick="selectCategory(\\'' + c.key + '\\')">' +
          '<span>' + c.label + '</span>' +
          '<span class="cat-count">' + cnt + '</span>' +
        '</button>';
      }).join('');
    }

    function populateDropdown(id, values, defaultLabel) {
      const sel = document.getElementById(id);
      const current = sel.value || 'ALL';
      let html = '<option value="ALL">' + defaultLabel + '</option>';
      for (const v of values) {
        if (!v || v === '-') continue;
        html += '<option value="' + v + '"' + (v === current ? ' selected' : '') + '>' + v + '</option>';
      }
      sel.innerHTML = html;
    }

    async function fetchProducts() {
      try {
        const res = await fetch('/api/products');
        const data = await res.json();
        allProducts = data.products || [];

        document.getElementById('totalProducts').textContent = allProducts.length;
        document.getElementById('monitoredCommunities').textContent = data.communitiesCount || 0;

        const supSet = new Set(allProducts.map(p => p.fornecedor).filter(Boolean));
        document.getElementById('activeSuppliersCount').textContent = supSet.size;

        const dates = Array.from(new Set(allProducts.map(p => p.data).filter(Boolean)));
        const caps = Array.from(new Set(allProducts.map(p => p.capacidade).filter(Boolean))).sort();
        const colors = Array.from(new Set(allProducts.map(p => p.cor).filter(Boolean))).sort();
        const supps = Array.from(supSet).sort();

        populateDropdown('dateFilter', dates, 'Todas as datas');
        populateDropdown('capFilter', caps, '[SALVO] Todas as capacidades');
        populateDropdown('colorFilter', colors, 'Todas as cores');
        populateDropdown('suppFilter', supps, 'Todos os fornecedores');

        renderCategoryTabs();
        renderProducts();
        document.getElementById('lastUpdateText').textContent = 'Atualizado às ' + new Date().toLocaleTimeString('pt-BR');
      } catch (e) {}
    }

    function renderProducts() {
      const q = (document.getElementById('qInput').value || '').toLowerCase().trim();
      const dFilter = document.getElementById('dateFilter').value;
      const capFilter = document.getElementById('capFilter').value;
      const colorFilter = document.getElementById('colorFilter').value;
      const suppFilter = document.getElementById('suppFilter').value;

      let filtered = allProducts.filter(p => {
        if (selectedCategory !== 'ALL' && p.categoria !== selectedCategory) return false;
        if (dFilter !== 'ALL' && p.data !== dFilter) return false;
        if (capFilter !== 'ALL' && p.capacidade !== capFilter) return false;
        if (colorFilter !== 'ALL' && p.cor !== colorFilter) return false;
        if (suppFilter !== 'ALL' && p.fornecedor !== suppFilter) return false;
        if (q) {
          const hay = (p.modelo + ' ' + p.capacidade + ' ' + p.cor + ' ' + p.fornecedor + ' ' + p.categoria).toLowerCase();
          const words = q.split(/\\s+/);
          for (const w of words) {
            if (!hay.includes(w)) return false;
          }
        }
        return true;
      });

      // Agrupar por Modelo + Capacidade + Cor + Condição para encontrar o menor custo
      const bestMap = new Map();
      const offersCountMap = new Map();
      for (const p of filtered) {
        const key = (p.categoria + '|' + p.modelo + '|' + p.capacidade + '|' + p.cor + '|' + p.condicao).toUpperCase();
        offersCountMap.set(key, (offersCountMap.get(key) || 0) + 1);
        const cur = bestMap.get(key);
        if (!cur || Number(p.preco) < Number(cur.preco)) {
          bestMap.set(key, p);
        }
      }

      document.getElementById('bestDealsCount').textContent = bestMap.size;

      let displayList = viewMode === 'BEST'
        ? Array.from(bestMap.values())
        : filtered.slice();

      displayList.sort((a, b) => {
        if (a.modelo !== b.modelo) return a.modelo.localeCompare(b.modelo);
        return Number(a.preco) - Number(b.preco);
      });

      const container = document.getElementById('productsContainer');
      if (displayList.length === 0) {
        container.innerHTML = '<div class="empty-state">' +
          '<div style="font-size:16px;font-weight:700;margin-bottom:10px;color:var(--muted);">[NENHUM ITEM]</div>' +
          '<h3 style="margin:0 0 8px 0;color:#e2e8f0;">Nenhum produto encontrado para este filtro</h3>' +
          '<p style="margin:0;font-size:14px;">Assim que os fornecedores enviarem as listas nos grupos/comunidades do WhatsApp (ou você importar uma lista no <a href="/admin" style="color:#38bdf8;">Painel Administrativo</a>), os melhores preços aparecerão aqui automaticamente separados por categoria!</p>' +
        '</div>';
        return;
      }

      container.innerHTML = '<div class="products-grid">' + displayList.map(p => {
        const key = (p.categoria + '|' + p.modelo + '|' + p.capacidade + '|' + p.cor + '|' + p.condicao).toUpperCase();
        const totalOffers = offersCountMap.get(key) || 1;
        const isSem = p.categoria === 'iPhones Seminovos' || p.condicao === 'Seminovo';
        const telClean = String(p.telefone || '').replace(/\\D/g, '');
        const waText = encodeURIComponent('Olá! Vi na lista do dia o produto ' + p.modelo + ' ' + (p.capacidade !== '-' ? p.capacidade : '') + ' ' + (p.cor !== '-' ? p.cor : '') + ' por ' + formatBRL(p.preco) + '. Ainda está disponível?');
        const waHref = telClean ? ('https://wa.me/' + telClean + '?text=' + waText) : '#';

        return '<div class="prod-card">' +
          '<div>' +
            '<div class="prod-top">' +
              '<span class="prod-cat ' + (isSem ? 'seminovo' : '') + '">' + p.categoria + '</span>' +
              '<span style="font-size:11px;color:#94a3b8;">Data: ' + (p.data || '') + ' ' + (p.hora || '') + '</span>' +
            '</div>' +
            '<div class="prod-title">' + p.modelo + '</div>' +
            '<div class="prod-specs">' +
              (p.capacidade && p.capacidade !== '-' ? '<span class="spec-pill">[SALVO] ' + p.capacidade + '</span>' : '') +
              (p.cor && p.cor !== '-' ? '<span class="spec-pill">Cor: ' + p.cor + '</span>' : '') +
              '<span class="spec-pill">' + (p.condicao || 'Novo') + '</span>' +
              (viewMode === 'BEST' && totalOffers > 1 ? '<span class="spec-pill" style="background:rgba(16,185,129,0.15);color:#34d399;">Melhor Preço entre ' + totalOffers + ' listas</span>' : '') +
            '</div>' +
          '</div>' +
          '<div>' +
            '<div class="price-row">' +
              '<span class="price-lbl">Menor Custo Fornecedor</span>' +
              '<span class="price-val">' + formatBRL(p.preco) + '</span>' +
            '</div>' +
            '<div class="supp-box">' +
              '<div>' +
                '<div class="supp-name">Fornecedor: ' + (p.fornecedor || 'Fornecedor') + '</div>' +
                '<div class="supp-meta">Origem: ' + (p.origem || 'Grupo WhatsApp') + '</div>' +
              '</div>' +
              (telClean ? '<a class="btn-wa" href="' + waHref + '" target="_blank" rel="noopener">WhatsApp</a>' : '') +
            '</div>' +
          '</div>' +
        '</div>';
      }).join('') + '</div>';
    }

    setInterval(fetchProducts, 5000);
    fetchProducts();
  </script>
</body>
</html>
"""


# ==============================================================================
# PÁGINA 2: PAINEL ADMINISTRATIVO WHATSAPP & COMUNIDADES (/admin)
# ==============================================================================
ADMIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Painel Administrativo — WhatsApp, Comunidades & Listas</title>
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
      --purple: #a855f7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 20px;
    }
    .container { max-width: 1320px; margin: 0 auto; }
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
    header h1 { margin: 0; font-size: 22px; display: flex; align-items: center; gap: 10px; }
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
    .badge.AUTOJOIN { background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid #9333ea; }

    .grid-top {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }
    @media (max-width: 920px) { .grid-top { grid-template-columns: 1fr; } }
    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 20px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.3);
      margin-bottom: 20px;
    }
    .card h2 {
      margin: 0 0 14px 0;
      font-size: 16px;
      color: #e2e8f0;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
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
    .btn-row { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    button, .btn-link {
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
      text-decoration: none;
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
      grid-template-columns: repeat(6, 1fr);
      gap: 12px;
      margin-bottom: 20px;
    }
    @media (max-width: 960px) { .stats-bar { grid-template-columns: repeat(2, 1fr); } }
    .stat-box {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 14px 18px;
    }
    .stat-box .label { font-size: 12px; color: var(--muted); text-transform: uppercase; }
    .stat-box .val { font-size: 24px; font-weight: 700; margin-top: 4px; }

    .grid-bottom {
      display: grid;
      grid-template-columns: 1.45fr 1fr;
      gap: 20px;
    }
    @media (max-width: 980px) { .grid-bottom { grid-template-columns: 1fr; } }
    .table-wrap {
      max-height: 400px;
      overflow-y: auto;
      border: 1px solid #1e293b;
      border-radius: 10px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid #1e293b; }
    th { background: #0f172a; color: #94a3b8; position: sticky; top: 0; z-index: 1; }
    .pill { padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    .pill.PENDENTE { background: rgba(148,163,184,0.15); color: #cbd5e1; }
    .pill.ENVIADO, .pill.ENTROU_E_ARQUIVOU { background: rgba(16,185,129,0.2); color: #34d399; }
    .pill.ERRO { background: rgba(239,68,68,0.2); color: #f87171; }
    .logs-box {
      background: #070b14;
      border: 1px solid #1e293b;
      border-radius: 10px;
      height: 400px;
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
        <h1>Painel Administrativo — WhatsApp, Comunidades & Captura de Preços</h1>
        <div style="font-size:13px;color:var(--muted);margin-top:4px;">
          <strong>Trava Manual Ativa:</strong> Nenhum disparo é feito sem sua autorização. Comunidades e Listas de Preços são capturadas 24h!
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <a href="/" class="btn-link btn-blue">Abrir Buscador Público de Preços</a>
        <span class="badge AUTOJOIN">SISTEMA DE COMUNIDADES & LISTAS ATIVO</span>
        <span id="waStatusBadge" class="badge CONNECTING">Verificando...</span>
        <span id="campaignBadge" class="badge DISCONNECTED">DISPARO PARADO</span>
      </div>
    </header>

    <div class="stats-bar">
      <div class="stat-box">
        <div class="label">Total Fornecedores</div>
        <div class="val" id="stTotal">204</div>
      </div>
      <div class="stat-box">
        <div class="label">Pendentes Envio</div>
        <div class="val" id="stPending" style="color:#fbbf24;">204</div>
      </div>
      <div class="stat-box">
        <div class="label">Mensagens Enviadas</div>
        <div class="val" id="stSent" style="color:#34d399;">0</div>
      </div>
      <div class="stat-box">
        <div class="label">Comunidades Arquivadas</div>
        <div class="val" id="stCommunities" style="color:#c084fc;">0</div>
      </div>
      <div class="stat-box">
        <div class="label">Produtos no Buscador</div>
        <div class="val" id="stProducts" style="color:#38bdf8;">0</div>
      </div>
      <div class="stat-box">
        <div class="label">Erros / Falhas</div>
        <div class="val" id="stErrors" style="color:#f87171;">0</div>
      </div>
    </div>

    <div class="grid-top">
      <!-- Card 1: Conexão WhatsApp -->
      <div class="card" style="margin-bottom:0;">
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
      <div class="card" style="margin-bottom:0;">
        <h2>
          <span>2. Mensagem Exata & Autorização Manual de Disparo</span>
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
              <button class="btn-ghost" onclick="sendTest()">Testar Envio</button>
            </div>
          </div>
        </div>

        <div class="btn-row">
          <button class="btn-blue" onclick="saveConfig()">[SALVO] Salvar Mensagem</button>
          <button class="btn-green" id="btnStart" onclick="startCampaign()">Autorizar e Iniciar Disparo</button>
          <button class="btn-warn" id="btnStop" onclick="stopCampaign()" disabled>Pausar Disparo</button>
          <button class="btn-ghost" onclick="resetHistory()">[RESET] Zerar Histórico de Enviados</button>
        </div>
        <div id="currentSending" style="margin-top:12px;font-size:13px;color:#38bdf8;font-weight:600;"></div>
      </div>
    </div>

    <!-- Card 3: Simulador / Importador Manual de Lista para o Buscador -->
    <div class="card">
      <h2>
        <span>[PRODUTO] Testar / Cadastrar Lista de Preços Manualmente no Buscador</span>
        <div style="display:flex;gap:8px;">
          <button class="btn-ghost" onclick="loadSampleList()" style="padding:6px 12px;font-size:12px;">Preencher Lista de Exemplo</button>
          <button class="btn-ghost" onclick="clearProducts()" style="padding:6px 12px;font-size:12px;color:#f87171;">[LIMPEZA] Limpar Produtos do Buscador</button>
        </div>
      </h2>
      <p style="margin:0 0 10px 0;font-size:13px;color:var(--muted);">
        O sistema já captura automaticamente todas as listas que os fornecedores mandam nos grupos/comunidades. Se quiser testar ou colar uma lista manualmente agora, preencha abaixo:
      </p>
      <div style="display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
        <input type="text" id="manualListSupplier" placeholder="Nome do Fornecedor (ex: BOB CELL 0277)" style="flex:1;min-width:220px;" />
        <input type="text" id="manualListPhone" placeholder="WhatsApp do Fornecedor (ex: 5511977770277)" style="flex:1;min-width:220px;" />
      </div>
      <textarea id="manualListText" placeholder="Cole aqui a lista de produtos e preços do fornecedor..." style="min-height:110px;"></textarea>
      <div class="btn-row">
        <button class="btn-green" onclick="submitManualList()">[NOVO] Processar Lista e Publicar no Buscador</button>
        <a href="/" class="btn-link btn-ghost">Ver no Buscador Aberto</a>
      </div>
    </div>

    <!-- Card 4: Comunidades Entradas e Contatos Arquivados Monitorados -->
    <div class="card">
      <h2>
        <span>[ARQUIVO] Todos os Contatos, Comunidades, Grupos & Listas Arquivadas Monitorados 24h</span>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <input type="text" id="manualInviteLink" placeholder="Colar link chat.whatsapp.com/... para entrar e arquivar" style="width:300px;font-size:12px;" />
          <button class="btn-ghost" onclick="joinManualCommunity()" style="padding:6px 12px;font-size:12px;">Entrar e Arquivar Agora</button>
          <button class="btn-blue" onclick="archiveAllNow()" style="padding:6px 12px;font-size:12px;">[ARQUIVO] Arquivar Tudo (Comunidades, Grupos e Listas)</button>
        </div>
      </h2>
      <p style="margin:0 0 10px 0;font-size:12px;color:#34d399;">
        <strong>Monitoramento Total dos Arquivados Ativo:</strong> Todo contato, lista de transmissão, grupo ou comunidade que estiver <strong>arquivado no seu WhatsApp</strong> é monitorado automaticamente 24h por dia para capturar tabelas de preços e links de comunidades!
      </p>
      <div class="table-wrap" style="max-height:240px;">
        <table>
          <thead>
            <tr>
              <th>Última Atividade</th>
              <th>Contato / Comunidade / Grupo Arquivado</th>
              <th>Tipo de Canal</th>
              <th>Identificador / Link</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="commTableBody"></tbody>
        </table>
      </div>
    </div>

    <div class="grid-bottom">
      <!-- Lista de Fornecedores -->
      <div class="card">
        <h2>
          <span>5. Lista de Contatos (204 Fornecedores)</span>
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
          <span>6. Logs em Tempo Real (Disparos, Comunidades & Listas Capturadas)</span>
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

        const wa = data.whatsapp || {};
        const comms = wa.communities || [];
        const archivedList = wa.archivedMonitored || [];

        document.getElementById('stTotal').textContent = data.stats.total;
        document.getElementById('stPending').textContent = data.stats.pending;
        document.getElementById('stSent').textContent = data.stats.sent;
        document.getElementById('stCommunities').textContent = Math.max(
          comms.filter(c => c.status === 'ENTROU_E_ARQUIVOU').length,
          archivedList.length
        );
        document.getElementById('stProducts').textContent = data.stats.productsCount || 0;
        document.getElementById('stErrors').textContent = data.stats.errors;

        const commBody = document.getElementById('commTableBody');
        const combinedRows = [];
        for (const c of comms.slice().reverse()) {
          combinedRows.push({
            time: c.joinedAt || '-',
            name: c.groupName || 'Comunidade',
            type: c.senderName || 'Comunidade / Grupo',
            id: c.link || c.code || '-',
            status: c.status === 'ENTROU_E_ARQUIVOU' ? 'ENTROU E ARQUIVOU' : (c.status || 'ATIVO')
          });
        }
        for (const a of archivedList.slice().reverse()) {
          if (!combinedRows.some(r => r.name === a.name || r.id === a.jid)) {
            combinedRows.push({
              time: a.lastActivity || '-',
              name: a.name || a.jid,
              type: a.type || 'Contato Arquivado',
              id: a.jid || '-',
              status: 'MONITORANDO ARQUIVADO'
            });
          }
        }

        if (combinedRows.length === 0) {
          commBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;">Conecte o WhatsApp para sincronizar todos os seus contatos, listas e grupos arquivados automaticamente...</td></tr>';
        } else {
          commBody.innerHTML = combinedRows.map(r => (
            '<tr>' +
              '<td style="color:#94a3b8;font-size:12px;">' + r.time + '</td>' +
              '<td><strong>' + r.name + '</strong></td>' +
              '<td>' + r.type + '</td>' +
              '<td><code>' + r.id + '</code></td>' +
              '<td><span class="pill ENTROU_E_ARQUIVOU">' + r.status + '</span></td>' +
            '</tr>'
          )).join('');
        }

        const waBadge = document.getElementById('waStatusBadge');
        waBadge.className = 'badge ' + (wa.status || 'CONNECTING');
        waBadge.textContent = wa.status === 'CONNECTED'
          ? 'WHATSAPP CONECTADO'
          : (wa.status === 'QR_READY' ? 'AGUARDANDO QR CODE' : (wa.status || 'CONECTANDO'));

        const qrBox = document.getElementById('qrContainer');
        const connInfo = document.getElementById('connectedInfo');
        if (wa.status === 'CONNECTED') {
          qrBox.innerHTML = '<div style="font-size:24px;font-weight:800;color:#34d399;margin-bottom:6px;">[CONECTADO]</div><div style="font-weight:700;color:#34d399;margin-top:8px;">Aparelho Conectado!</div><div style="font-size:12px;color:#94a3b8;margin-top:4px;">Monitor de Comunidades & Listas ativo 24h. O disparo só ocorre quando você clicar no botão verde ao lado.</div>';
          connInfo.textContent = wa.user ? ('Conectado como: ' + wa.user.name + ' (' + wa.user.id + ')') : '';
        } else if (wa.status === 'QR_READY' && wa.qrDataUrl) {
          qrBox.innerHTML = '<img src="' + wa.qrDataUrl + '" alt="QR Code WhatsApp" /><div style="font-size:12px;color:#fbbf24;margin-top:8px;">Escaneie no WhatsApp > Aparelhos conectados</div>';
          connInfo.textContent = '';
        } else {
          qrBox.innerHTML = '<div style="color:#94a3b8;">Gerando sessão / QR Code...</div>';
          connInfo.textContent = '';
        }

        const camp = data.campaign || {};
        const cBadge = document.getElementById('campaignBadge');
        if (camp.running) {
          cBadge.className = 'badge CONNECTED';
          cBadge.textContent = 'DISPARANDO EM FILA...';
          document.getElementById('btnStart').disabled = true;
          document.getElementById('btnStop').disabled = false;
          document.getElementById('currentSending').textContent = camp.current_supplier
            ? ('Processando agora: ' + camp.current_supplier)
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

    async function joinManualCommunity() {
      const link = document.getElementById('manualInviteLink').value.trim();
      if (!link) return alert('Cole um link chat.whatsapp.com/...');
      const res = await fetch('/api/join-community', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ linkOrCode: link })
      });
      const d = await res.json();
      if (d.ok) {
        document.getElementById('manualInviteLink').value = '';
      } else {
        alert('Erro: ' + (d.error || 'Falha ao entrar na comunidade'));
      }
      await loadState();
    }

    function loadSampleList() {
      document.getElementById('manualListSupplier').value = 'BOB CELL 0277';
      document.getElementById('manualListPhone').value = '5511977770277';
      document.getElementById('manualListText').value =
        "IPHONE 16 PRO MAX 256GB\\n" +
        "PRETO R$ 7.150\\n" +
        "BRANCO R$ 7.200\\n" +
        "DESERT TITANIO R$ 7.180\\n\\n" +
        "IPHONE 15 128GB PRETO R$ 4.320\\n" +
        "IPHONE 13 128GB SWAP AZUL R$ 2.490\\n" +
        "MACBOOK AIR M2 8/256GB SPACE GRAY R$ 5.450\\n" +
        "IPAD 10 64GB SILVER R$ 2.150\\n" +
        "APPLE WATCH S10 46MM PRETO R$ 2.480\\n" +
        "AIRPODS PRO 2 USB-C R$ 1.290\\n" +
        "REDMI NOTE 13 8/256GB PRETO R$ 980";
    }

    async function submitManualList() {
      const supplierName = document.getElementById('manualListSupplier').value.trim() || 'FORNECEDOR MANUAL';
      const supplierPhone = document.getElementById('manualListPhone').value.trim() || '';
      const text = document.getElementById('manualListText').value.trim();
      if (!text) return alert('Cole o texto da lista de preços.');
      const res = await fetch('/api/manual-ingest-list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supplierName, supplierPhone, text })
      });
      const d = await res.json();
      if (d.ok) {
        alert('Sucesso! ' + d.parsedCount + ' produtos foram cadastrados e separados por categoria no Buscador!');
        document.getElementById('manualListText').value = '';
      } else {
        alert('Nenhum produto com preço foi identificado no texto.');
      }
      await loadState();
    }

    async function clearProducts() {
      if (!confirm('Deseja limpar todos os produtos atualmente listados no Buscador?')) return;
      await fetch('/api/clear-products', { method: 'POST' });
      await loadState();
    }

    async function archiveAllNow() {
      const res = await fetch('/api/archive-all', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        alert('[ARQUIVO] Arquivamento completo! ' + (data.groupCount || 0) + ' grupos/comunidades e ' + (data.chatCount || 0) + ' conversas/listas arquivadas.');
      } else {
        alert('Aviso: ' + (data.error || 'Conecte o WhatsApp primeiro.'));
      }
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

    def _send_html(self, html_str):
        body = html_str.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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

        # Rota Aberta / Pública -> Buscador de Preços
        if parsed.path in ("/", "/index.html", "/buscador"):
            self._send_html(BUSCADOR_HTML)
            return

        # Rota Administrativa -> Painel WhatsApp & Comunidades
        if parsed.path in ("/admin", "/painel"):
            self._send_html(ADMIN_HTML)
            return

        if parsed.path == "/api/products":
            produtos = load_produtos()
            try:
                wa = bridge_get("/api/status")
                comms = wa.get("communities", [])
                archived_cnt = int(wa.get("archivedMonitoredCount", 0))
            except Exception:
                comms = []
                archived_cnt = 0
            self._send_json({
                "products": produtos,
                "communitiesCount": max(len([c for c in comms if c.get("status") == "ENTROU_E_ARQUIVOU"]), archived_cnt),
            })
            return

        if parsed.path == "/api/panel-state":
            cfg = load_config()
            fornecedores = load_fornecedores()
            historico = load_historico()
            produtos = load_produtos()
            try:
                wa = bridge_get("/api/status")
            except Exception:
                wa = {"status": "CONNECTING", "logs": [], "communities": []}

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
                combined_logs = list(wa.get("logs", [])[:45]) + list(campaign_state["logs"][:45])

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
                    "productsCount": len(produtos),
                },
                "logs": combined_logs[:90],
            })
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)

        # Endpoint interno chamado automaticamente pelo whatsapp_bridge.js a cada mensagem recebida nos grupos/comunidades
        if parsed.path == "/api/internal/ingest-message":
            body = self._read_body_json()
            text = str(body.get("text", "")).strip()
            remote_jid = str(body.get("remoteJid", ""))
            participant_jid = str(body.get("participantJid", ""))
            push_name = str(body.get("pushName", ""))
            group_name = str(body.get("groupName", ""))
            image_base64 = str(body.get("imageBase64", "")).strip()

            sup_name, sup_phone = identify_supplier(participant_jid, remote_jid, push_name, group_name, text)
            items = parse_supplier_message(text, sup_name, sup_phone, group_name or push_name, image_base64=image_base64)
            if items:
                try:
                    raw_log_path = os.path.join(BASE_DIR, "ultimas_listas_brutas.json")
                    raw_list = []
                    if os.path.exists(raw_log_path):
                        with open(raw_log_path, "r", encoding="utf-8") as rf:
                            raw_list = json.load(rf)
                    raw_list.append({
                        "supplierName": sup_name,
                        "supplierPhone": sup_phone,
                        "remoteJid": remote_jid,
                        "participantJid": participant_jid,
                        "pushName": push_name,
                        "groupName": group_name,
                        "text": text,
                    })
                    with open(raw_log_path, "w", encoding="utf-8") as wf:
                        json.dump(raw_list[-100:], wf, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                saved = upsert_parsed_products(items)
                add_panel_log(f"[PRODUTO] {saved} produtos capturados automaticamente de {sup_name} ({group_name or 'WhatsApp'})")
                self._send_json({"ok": True, "parsedCount": saved, "supplierName": sup_name})
            else:
                self._send_json({"ok": True, "parsedCount": 0})
            return

        # Endpoint para importar/testar lista manualmente pelo /admin
        if parsed.path == "/api/manual-ingest-list":
            body = self._read_body_json()
            sup_name = str(body.get("supplierName", "FORNECEDOR MANUAL")).strip().upper()
            sup_phone = re.sub(r"\D", "", str(body.get("supplierPhone", "")))
            text = str(body.get("text", "")).strip()

            items = parse_supplier_message(text, sup_name, sup_phone, "Importado no Painel")
            if items:
                saved = upsert_parsed_products(items)
                add_panel_log(f"[PRODUTO] {saved} produtos importados manualmente para o fornecedor {sup_name}")
                self._send_json({"ok": True, "parsedCount": saved})
            else:
                self._send_json({"ok": False, "parsedCount": 0})
            return

        if parsed.path == "/api/clear-products":
            save_produtos([])
            add_panel_log("[LIMPEZA] Lista de produtos do Buscador foi limpa pelo administrador.")
            self._send_json({"ok": True})
            return

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
            add_panel_log("[SALVO] Configurações e mensagem atualizadas no painel.")
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
            add_panel_log("[RESET] Histórico de envios zerado. Todos os contatos voltaram para PENDENTE.")
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/logout-wa":
            with state_lock:
                campaign_state["stop_requested"] = True
            try:
                bridge_post("/api/logout", {})
            except Exception:
                pass
            add_panel_log("[CONEXAO] Sessão do WhatsApp desconectada. Aguarde o novo QR Code.")
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
                    add_panel_log(f"[OK] Envio individual concluído para {name} ({phone})")
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "error": resp.get("error")})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/join-community":
            body = self._read_body_json()
            link_or_code = str(body.get("linkOrCode", "")).strip()
            try:
                resp = bridge_post("/api/join-community", {"linkOrCode": link_or_code})
                self._send_json(resp)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/archive-all":
            try:
                resp = bridge_post("/api/archive-all", {})
                add_panel_log(f"[ARQUIVO] Arquivamento geral executado: {resp.get('groupCount', 0)} grupos/comunidades e {resp.get('chatCount', 0)} conversas.")
                self._send_json(resp)
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
            add_panel_log(f"[NOVO] Contato adicionado à lista: {nome} ({telefone})")
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not found"}, status=404)


def main():
    load_config()
    ensure_bridge_running()
    add_panel_log(f"Buscador Público (/) e Painel Admin (/admin) rodando em http://0.0.0.0:{PANEL_PORT}")
    server = ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), PanelHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
