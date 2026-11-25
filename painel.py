import hmac
import json
import datetime
import os
import random
import re
import secrets
import shutil
import subprocess
import threading
import tempfile
from pathlib import Path
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):
    time.tzset()

import daily_review
import apple_reference
import color_guardian
from parser_listas import parse_supplier_message
from price_guardian import (
    offer_color, public_price_review, review_quoted_price, validate_product,
    log_anomaly, equalize_catalog_prices, load_anomalies, run_guardian_sweep,
    compute_dfu_delta, classify_vitrine_category, _is_seminovo_condition,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORNECEDORES_FILE = os.path.join(BASE_DIR, "fornecedores.json")
RESPOSTAS_FILE = os.path.join(BASE_DIR, "respostas_fornecedores.json")
HISTORY_EVENTS_FILE = os.path.join(BASE_DIR, "historico_eventos.jsonl")
HISTORICO_FILE = os.path.join(BASE_DIR, "historico_envios.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CATALOG_ARCHIVE_DIR = os.path.join(BASE_DIR, "catalogo_arquivo")
PRODUTOS_FILE = os.path.join(BASE_DIR, "produtos_dia.json")

PANEL_PORT = int(os.environ.get("PORT", "3050"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3051"))
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"

DEFAULT_NODE_WIN = r"C:\Users\Mundo Apple\.gemini\antigravity\scratch\node-env\node-v20.17.0-win-x64\node.exe"
NODE_EXE = DEFAULT_NODE_WIN if os.path.exists(DEFAULT_NODE_WIN) else (shutil.which("node") or "node")
BRIDGE_SCRIPT = os.path.join(BASE_DIR, "whatsapp_bridge.js")

DEFAULT_MESSAGES = [
    "Olá, tudo bem? Me manda o link da sua comunidade ou grupo para eu acompanhar as listas, por favor.",
    "Boa tarde, tudo certo? Consegue me mandar o link da comunidade de vocês para eu entrar e acompanhar as atualizações?",
    "Opa, beleza? Consegue me mandar o link da sua comunidade ou grupo para eu entrar?",
    "Oi, tudo bem? Me manda o link da comunidade de vocês para eu acompanhar as tabelas do dia, por favor.",
    "Olá, tudo bem por aí? Quando puder, me manda o link do seu grupo ou comunidade para eu entrar. Obrigado!",
]
DEFAULT_CONFIG = {
    "mensagem": DEFAULT_MESSAGES[0],
    "mensagens": list(DEFAULT_MESSAGES),
    "delay_min": 45,
    "delay_max": 70,
    "limite_rodada": 20,
    "auto_start_on_connect": False,
}
BLOCKED_DELIVERY_STATES = {"ENVIADO", "ENVIANDO", "INCERTO", "AGUARDANDO_CONFIRMACAO"}


def automation_paused():
    return os.environ.get("BUSCABUSCA_PAUSED", "0") == "1"


def normalized_messaging_config(cfg):
    value = dict(DEFAULT_CONFIG)
    value.update(cfg)
    messages = value.get("mensagens")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 5 or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in messages):
        raise ValueError("Defina de um a cinco modelos de mensagem válidos.")
    value["mensagens"] = [item.strip() for item in messages]
    value["mensagem"] = value["mensagens"][0]
    raw_min = int(value.get("delay_min", 45))
    raw_max = int(value.get("delay_max", 70))
    if raw_min < 30:
        raw_min = 300
    if raw_max < 30:
        raw_max = 600
    value["delay_min"] = max(45, min(86400, raw_min))
    value["delay_max"] = max(value["delay_min"], min(86400, raw_max))
    value["limite_rodada"] = max(1, min(20, int(value.get("limite_rodada", 20))))
    value["auto_start_on_connect"] = False
    return value


def supplier_message(cfg, supplier, round_index=0):
    messages = cfg["mensagens"]
    raw_index = supplier.get("modelo_mensagem")
    index = int(raw_index) if raw_index is not None else (int(round_index) % len(messages))
    return render_message(messages[index if 0 <= index < len(messages) else 0], supplier)


state_lock = threading.Lock()
products_lock = threading.RLock()
data_lock = threading.RLock()
sending_lock = threading.Lock()

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
                if "mensagens" not in data:
                    cfg["mensagens"] = [str(data.get("mensagem") or DEFAULT_MESSAGES[0])] + DEFAULT_MESSAGES[1:]
                return normalized_messaging_config(cfg)
        except Exception:
            pass
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def atomic_json_write(file_path, value):
    with data_lock:
        fd, temporary = tempfile.mkstemp(prefix=".data-", suffix=".tmp", dir=os.path.dirname(file_path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def save_config(cfg):
    atomic_json_write(CONFIG_FILE, normalized_messaging_config(cfg))


def load_fornecedores():
    if not os.path.exists(FORNECEDORES_FILE):
        return []
    with open(FORNECEDORES_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_fornecedores(lista):
    atomic_json_write(FORNECEDORES_FILE, lista)


def load_historico():
    if not os.path.exists(HISTORICO_FILE):
        if os.path.exists(HISTORY_EVENTS_FILE) or os.path.exists(HISTORICO_FILE + ".bak"):
            raise ValueError("Histórico ausente: restaurar os registros antes de enviar.")
        return {}
    with open(HISTORICO_FILE, "r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Histórico inválido: envios bloqueados para proteger os registros.")
    return value


def save_historico(hist):
    # Merge under one lock. A stale request must never erase a previous delivery.
    with data_lock:
        previous = load_historico()
        merged = dict(previous)
        for phone, incoming in hist.items():
            old = previous.get(phone, {})
            entry = {**old, **incoming}
            if old.get("status") == "ENVIADO":
                entry["status"] = "ENVIADO"
                entry["horario"] = max(old.get("horario", ""), incoming.get("horario", ""))
            if int(old.get("ack", 0)) > int(incoming.get("ack", 0)):
                for field in ("ack", "confirmacao", "message_id"):
                    if field in old:
                        entry[field] = old[field]
            merged[phone] = entry
        if previous:
            atomic_json_write(HISTORICO_FILE + ".bak", previous)
        atomic_json_write(HISTORICO_FILE, merged)
        changes = {phone: item for phone, item in merged.items() if item != previous.get(phone)}
        if changes:
            with open(HISTORY_EVENTS_FILE, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"timestamp": time.time(), "changes": changes}, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


def canonical_phone(raw):
    digits = re.sub(r"\D", "", str(raw or "").split("@")[0].split(":")[0])
    if len(digits) in (10, 11):
        digits = "55" + digits
    if len(digits) == 13 and digits.startswith("55") and digits[4] == "9":
        digits = digits[:4] + digits[5:]
    return digits


def history_for_phone(history, phone):
    identity = canonical_phone(phone)
    matches = [entry for key, entry in history.items()
               if identity and (canonical_phone(key) == identity or canonical_phone(entry.get("jid")) == identity)]
    return next((entry for entry in matches if entry.get("status") == "ENVIADO"), matches[0] if matches else {})


def load_respostas():
    if not os.path.exists(RESPOSTAS_FILE):
        return {}
    with open(RESPOSTAS_FILE, encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Registro de respostas inválido")
    return value


def record_supplier_reply(phone, timestamp, message_id):
    identity = canonical_phone(phone)
    supplier = next((item for item in load_fornecedores() if canonical_phone(item.get("telefone")) == identity), None)
    if not identity or not supplier or not isinstance(timestamp, (int, float)) or timestamp <= 0 or timestamp > time.time() + 300:
        return False
    history = history_for_phone(load_historico(), supplier.get("telefone"))
    if history.get("horario"):
        sent_at = time.mktime(time.strptime(history["horario"], "%Y-%m-%d %H:%M:%S"))
        if timestamp < sent_at:
            return False
    with data_lock:
        replies = load_respostas()
        old = replies.get(identity, {})
        if timestamp <= old.get("timestamp", 0):
            return False
        replies[identity] = {"telefone": str(supplier.get("telefone")), "timestamp": timestamp,
                             "horario": datetime.datetime.fromtimestamp(timestamp).isoformat(timespec="seconds"),
                             "message_id": str(message_id or ""), "nome": supplier.get("nome", "")}
        atomic_json_write(RESPOSTAS_FILE, replies)
    return True


def pending_supplier(supplier, history, replies):
    phone = supplier.get("telefone", "")
    return bool(phone) and supplier.get("contato_autorizado") is True and not supplier.get("ignorar") and history_for_phone(history, phone).get("status") not in BLOCKED_DELIVERY_STATES and canonical_phone(phone) not in replies


def deliver_supplier(supplier, message):
    if automation_paused():
        return {"ok": False, "error": "Automações pausadas devido à restrição da conta."}
    if supplier.get("contato_autorizado") is not True:
        return {"ok": False, "error": "Registre a autorização ou expectativa de contato do fornecedor antes de enviar."}
    phone = str(supplier.get("telefone", "")).strip()
    with sending_lock:
        history = load_historico()
        previous = history_for_phone(history, phone)
        if previous.get("status") in BLOCKED_DELIVERY_STATES or canonical_phone(phone) in load_respostas():
            return {"ok": False, "skipped": True, "error": "Contato já enviado, respondido ou aguardando conferência; não será repetido."}
        cfg = load_config()
        latest_attempt = max((float(item.get("tentativa_timestamp", 0)) for item in history.values()), default=0)
        if time.time() - latest_attempt < cfg["delay_min"]:
            return {"ok": False, "error": "Aguarde o intervalo configurado antes do próximo envio."}
        entry = {**previous, "nome": supplier.get("nome", "Fornecedor"), "telefone": phone,
                 "status": "ENVIANDO", "texto_enviado": message, "tentativa_timestamp": time.time(), "horario": time.strftime("%Y-%m-%d %H:%M:%S")}
        save_historico({phone: entry})
        try:
            response = bridge_post("/api/send", {"phone": phone, "name": entry["nome"], "message": message})
        except Exception as exc:
            save_historico({phone: {**entry, "status": "INCERTO", "erro": str(exc)}})
            return {"ok": False, "error": "Envio sem confirmação. Conferir a conversa antes de repetir."}
        if response.get("ok") and response.get("messageId") and response.get("ack") == 0:
            save_historico({phone: {**entry, "status": "INCERTO", "message_id": response["messageId"], "ack": 0, "confirmacao": "FALHA"}})
            return {"ok": False, "error": "O WhatsApp informou falha. Confira a conversa antes de tentar novamente."}
        if response.get("ok") and response.get("messageId"):
            confirmed = int(response.get("ack", 0)) >= 2
            save_historico({phone: {**entry, "jid": response.get("jid"), "message_id": response["messageId"],
                "ack": response.get("ack", 0), "status": "ENVIADO" if confirmed else "AGUARDANDO_CONFIRMACAO",
                "confirmacao": "SERVIDOR" if confirmed else "PENDENTE"}})
            response["confirmed"] = confirmed
        elif response.get("ok"):
            save_historico({phone: {**entry, "status": "INCERTO", "erro": "O motor não retornou o ID da mensagem."}})
            return {"ok": False, "error": "Envio sem ID: conferir a conversa, sem repetição automática."}
        else:
            save_historico({phone: {**entry, "status": "ERRO", "erro": response.get("error")}})
        return response


def record_message_ack(phone, message_id, status):
    if not message_id or not isinstance(status, int) or status not in range(6):
        return False
    with data_lock:
        history = load_historico()
        entry = history_for_phone(history, phone)
        if not entry or (entry.get("message_id") and entry["message_id"] != message_id):
            return False
        if int(entry.get("ack", 0)) > status:
            return False
        if not entry.get("message_id") and entry.get("status") != "ENVIANDO":
            return False
        target_phone = entry.get("telefone") or phone
        confirmation = {0: "FALHA", 1: "PENDENTE", 2: "SERVIDOR", 3: "ENTREGUE", 4: "LIDA", 5: "REPRODUZIDA"}[status]
        save_historico({target_phone: {**entry, "message_id": message_id, "ack": status,
            "confirmacao": confirmation, "status": "INCERTO" if status == 0 else "ENVIADO" if status >= 2 else "AGUARDANDO_CONFIRMACAO"}})
        return True


def get_today_str():
    return time.strftime("%d/%m/%Y")


def load_produtos(only_today=True):
    with products_lock:
        if os.path.exists(PRODUTOS_FILE):
            try:
                with open(PRODUTOS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, list) or not all(isinstance(p, dict) for p in data):
                        raise ValueError("Formato inválido no arquivo de ofertas")
                    if isinstance(data, list):
                        if only_today:
                            hoje = get_today_str()
                            # Filtrar apenas produtos do dia corrente (a partir das 06:00)
                            return [
                                p for p in data
                                if p.get("data") == hoje and str(p.get("hora", "00:00:00")) >= "06:00:00"
                            ]
                        return data
            except (OSError, ValueError, TypeError):
                if not only_today:
                    raise
        return []


def archive_produtos(items):
    if not items:
        return
    with data_lock:
        os.makedirs(CATALOG_ARCHIVE_DIR, exist_ok=True)
        date_key = time.strftime("%Y%m%d")
        destination = os.path.join(CATALOG_ARCHIVE_DIR, date_key + ".json")
        archived = []
        if os.path.exists(destination):
            with open(destination, encoding="utf-8") as stream:
                archived = json.load(stream)
        unique = {json.dumps(item, sort_keys=True, ensure_ascii=False): item for item in archived + items}
        atomic_json_write(destination, list(unique.values()))


def save_produtos(lista):
    with products_lock:
        if not lista:
            archive_produtos(load_produtos(only_today=False))
        atomic_json_write(PRODUTOS_FILE, lista)


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


def offer_key(item):
    phone = re.sub(r"\D", "", str(item.get("telefone") or ""))
    supplier = phone or str(item.get("fornecedor") or item.get("id") or "").strip().casefold()
    model = re.sub(r"\bSE\s*(\d+)\b", r"SE \1", str(item.get("modelo") or "").upper()).strip()
    if model.startswith("IPAD"):
        model = re.sub(r"\b(?:GB|TB)\b", " ", model)
        model = re.sub(r"\bWI[ -]?FI\b", "WI-FI", model)
        model = " ".join(model.split())
    return (item.get("data"), supplier, model,
            re.sub(r"\s+", "", str(item.get("capacidade") or "-")).upper(),
            offer_color(item), str(item.get("condicao") or "").strip().casefold())


reference_wakeup = threading.Event()
REFERENCE_PENDING = os.path.join(BASE_DIR, "modelos_pendentes_apple.json")


def pending_models():
    if not os.path.exists(REFERENCE_PENDING):
        return []
    with open(REFERENCE_PENDING, encoding="utf8") as stream:
        return [r for r in json.load(stream) if r.get("data") == get_today_str()]


def refresh_model_reference():
    last_lookup = float("-inf")
    while True:
        try:
            force = reference_wakeup.is_set() and time.monotonic() - last_lookup >= 900
            reference_wakeup.clear()
            if force:
                last_lookup = time.monotonic()
            apple_reference.refresh(force=force)
            with products_lock:
                pending = pending_models()
                confirmed = [r for r in pending if apple_reference.model_status(r["modelo"]) is True]
                if confirmed:
                    upsert_parsed_products(confirmed)
                if os.path.exists(REFERENCE_PENDING):
                    atomic_json_write(REFERENCE_PENDING, [r for r in pending if r not in confirmed])
        except Exception as exc:
            print(f"[REFERÊNCIA APPLE] {type(exc).__name__}", flush=True)
        reference_wakeup.wait(900)


def upsert_parsed_products(new_items):
    with products_lock:
        if not new_items:
            return 0
        # Carregar apenas os do dia para que ao virar o dia a lista zere automaticamente
        hoje = get_today_str()
        atuais_todos = load_produtos(only_today=False)
        # Manter somente os produtos de hoje após 06:00
        atuais = [
            p for p in atuais_todos
            if p.get("data") == hoje and str(p.get("hora", "00:00:00")) >= "06:00:00"
        ]
        archive_produtos([p for p in atuais_todos if p not in atuais])
        # Chave única por Data + Fornecedor + Modelo + Capacidade + Cor + Condição para atualizar preço no mesmo dia
        index_map = {}
        for i, item in enumerate(atuais):
            k = offer_key(item)
            index_map[k] = i

        count = 0
        pending = None
        for raw_item in new_items:
            review_reason = review_quoted_price(raw_item)
            if review_reason:
                log_anomaly(raw_item, review_reason)
                continue
            # Confere modelo, capacidade, cor e condição antes da atualização.
            valid, reason, sanitized_item = validate_product(raw_item, atuais, allow_seminovo=True)
            if not valid or not sanitized_item:
                continue

            item = sanitized_item
            k = offer_key(item)
            if apple_reference.model_status(item["modelo"]) is False:
                if pending is None:
                    pending = {offer_key(r): r for r in pending_models()}
                if k not in pending or item.get("hora", "") >= pending[k].get("hora", ""):
                    pending[k] = item
                continue
            # History sync and retries can arrive after a newer supplier list.
            if k in index_map and str(item.get("hora", "00:00:00")) < str(atuais[index_map[k]].get("hora", "00:00:00")):
                continue
            item["id"] = f"{int(time.time() * 1000)}_{random.randint(100, 999)}_{count}"
            if k in index_map:
                atuais[index_map[k]] = item
            else:
                index_map[k] = len(atuais)
                atuais.append(item)
            count += 1

        if pending is not None:
            atomic_json_write(REFERENCE_PENDING, list(pending.values()))
            reference_wakeup.set()

        # Equalização contra desvios extremos depois que a mediana de mercado se forma
        atuais, _dropped_outliers = equalize_catalog_prices(atuais)

        # Manter no máximo 25.000 ofertas recentes para performance
        if len(atuais) > 25000:
            atuais = atuais[-25000:]

        save_produtos(atuais)
        return count


def midnight_reset_worker():
    last_reset_day = ""
    last_open_day = ""
    last_close_day = ""
    while True:
        try:
            now_str = time.strftime("%H:%M:%S")
            today_str = get_today_str()
            # Exatamente na virada da meia-noite (00:00:00 até 00:00:59) arquiva o dia anterior e zera o catálogo
            if now_str.startswith("00:00") and last_reset_day != today_str:
                with products_lock:
                    prev_items = load_produtos(only_today=False)
                    if prev_items:
                        archive_produtos(prev_items)
                    save_produtos([])
                add_panel_log("[RESET DIÁRIO] 🕛 MEIA-NOITE! Catálogo do dia anterior arquivado e zerado para o novo dia.")
                last_reset_day = today_str
            elif now_str.startswith("06:00") and last_open_day != today_str:
                add_panel_log("[JANELA 06:00-18:00] ☀️ Janela diária de captura aberta às 06:00 BRT.")
                last_open_day = today_str
            elif now_str.startswith("18:00") and last_close_day != today_str:
                add_panel_log("[JANELA 06:00-18:00] 🌙 Janela encerrada às 18:00 BRT. Catálogo do dia preservado até 00:00.")
                last_close_day = today_str
        except Exception:
            pass
        time.sleep(5)


reset_thread = threading.Thread(target=midnight_reset_worker, daemon=True)



_bridge_lite_cache = {"at": 0.0, "data": {}}


def bridge_get(path):
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bridge_status_lite():
    now = time.time()
    if now - _bridge_lite_cache["at"] < 2.0 and _bridge_lite_cache["data"]:
        return _bridge_lite_cache["data"]
    try:
        data = bridge_get("/api/status?lite=1")
        _bridge_lite_cache["at"] = now
        _bridge_lite_cache["data"] = data
        return data
    except Exception:
        if now - _bridge_lite_cache["at"] < 15.0 and _bridge_lite_cache["data"]:
            return _bridge_lite_cache["data"]
        return {}


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
    if automation_paused():
        return
    try:
        bridge_get("/api/status")
        return
    except Exception:
        add_panel_log("Iniciando conexão WhatsApp e captura de listas...")
        env = dict(os.environ)
        env["BRIDGE_PORT"] = str(BRIDGE_PORT)
        env["PORT"] = str(PANEL_PORT)
        subprocess.Popen(
            [NODE_EXE, BRIDGE_SCRIPT],
            cwd=BASE_DIR,
            env=env,
            stdout=None,
            stderr=None,
        )
        for _ in range(15):
            time.sleep(1)
            try:
                bridge_get("/api/status")
                add_panel_log("Motor WhatsApp disponível. Consulte o painel para verificar a conexão.")
                return
            except Exception:
                pass


def bridge_supervisor():
    while True:
        time.sleep(20)
        try:
            ensure_bridge_running()
        except Exception as exc:
            add_panel_log(f"Falha ao reiniciar motor WhatsApp: {exc}")


def render_message(template, supplier):
    msg = str(template or "").strip()
    msg = msg.replace("{nome}", str(supplier.get("nome", "")).strip())
    msg = msg.replace("{telefone}", str(supplier.get("telefone", "")).strip())
    msg = msg.replace("{localizacao}", str(supplier.get("localizacao", "")).strip())
    return msg


def campaign_worker():
    add_panel_log("[INICIO] Disparo AUTORIZADO pelo usuário! Iniciando fila de envios...")
    attempted = set()
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

            if automation_paused():
                return
            fornecedores = load_fornecedores()
            historico = load_historico()
            cfg = load_config()
            replies = load_respostas()

            proximo = None
            for f in fornecedores:
                tel = str(f.get("telefone", "")).strip()
                if not tel:
                    continue
                if f.get("ignorar"):
                    continue
                if canonical_phone(tel) not in attempted and pending_supplier(f, historico, replies):
                    proximo = f
                    break

            if not proximo:
                add_panel_log("[FILA] Fim desta rodada. Envios com erro ou sem confirmação ficam separados para conferência.")
                with state_lock:
                    campaign_state["running"] = False
                    campaign_state["current_supplier"] = None
                return

            nome = proximo.get("nome", "Fornecedor")
            tel = str(proximo.get("telefone", "")).strip()
            if len(attempted) >= cfg["limite_rodada"]:
                add_panel_log("[PAUSA] Limite da rodada alcançado. Confira as confirmações antes de continuar.")
                return
            msg_final = supplier_message(cfg, proximo, len(attempted))

            with state_lock:
                campaign_state["current_supplier"] = f"{nome} ({tel})"

            attempted.add(canonical_phone(tel))
            resp = deliver_supplier(proximo, msg_final)
            with state_lock:
                campaign_state["sent_session" if resp.get("ok") else "errors_session"] += 1
            add_panel_log(f"[{'OK' if resp.get('ok') else 'CONFERIR'}] {nome}: " + ("confirmado pelo servidor" if resp.get("confirmed") else "aguardando confirmação" if resp.get("ok") else str(resp.get("error", "Falha"))) + f' | "{msg_final[:80]}"')

            if resp.get("ok") and not resp.get("confirmed"):
                add_panel_log("[PAUSA] Sem confirmação do servidor. Confira a mensagem no celular antes de continuar.")
                return
            if not resp.get("ok"):
                add_panel_log("[PAUSA] Falha no envio. Rodada interrompida para conferência.")
                return
            d_min = cfg["delay_min"]
            d_max = max(d_min, int(cfg.get("delay_max", 70)))
            wait_sec = round(random.uniform(d_min, d_max), 1)
            add_panel_log(f"[INTERVALO] Aguardando {wait_sec}s antes da próxima mensagem...")

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
BUSCADOR_HTML = (Path(BASE_DIR) / "web" / "index.html").read_text(encoding="utf-8")


# ==============================================================================
# PÁGINA 2: PAINEL ADMINISTRATIVO WHATSAPP & COMUNIDADES (/admin)
# ==============================================================================
ADMIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BuscaBusca · Central de fornecedores</title>
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

    /* BuscaBusca — central operacional */
    :root { --bg:#f5f7f8; --card:#fff; --card-border:#e3e9ed; --text:#24343d; --muted:#73818b; --accent:#166858; }
    body { font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; padding:30px; }
    header { padding:12px 0 25px; }
    header h1 { font-size:25px; letter-spacing:-.8px; }
    .card { min-width:0; box-shadow:none; border-radius:12px; padding:24px; }
    .card h2 { flex-wrap:wrap; gap:10px; color:#24343d; font-size:15px; }
    .stats-bar { gap:12px; }
    .stat-box { background:#fff; border:1px solid #e3e9ed; box-shadow:none; }
    .stat-box .val { color:#166858!important; }
    .stat-box .label { color:#73818b; }
    input,textarea,select { background:#f8fafb!important; color:#24343d!important; border:1px solid #dce4e8!important; border-radius:7px!important; }
    button,.btn-link { border-radius:7px!important; font-weight:600; }
    .btn-blue { background:#166858; color:white; }
    .btn-green { background:#166858; color:white; }
    .btn-primary,.btn-success { color:white!important; }
    .pill.PENDENTE { color:#687c88; background:#eef2f5; }
    .pill.RESPONDEU { color:#166858; background:#dcefe5; }
    .pill.INCERTO,.pill.ENVIANDO { color:#986713; background:#fff2d8; }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .pill.ENVIADO { color:#166858; background:#eaf6ef; }
    .btn-ghost { background:white; color:#52636d; border:1px solid #dce4e8; }
    .badge.CONNECTED { background:#eaf6ef; color:#166858; border-color:#cee7d9; }
    .badge.CONNECTING,.badge.DISCONNECTED { background:#edf1f4; color:#60717e; border-color:#dce4e8; }
    .badge.AUTOJOIN { background:#eff4f5; color:#617984; border:0; }
    .badge.QR_READY { background:#fff4dc; color:#9a6b17; border-color:#eedbb0; }
    .grid-bottom { grid-template-columns:minmax(0,1.45fr) minmax(0,1fr); }
    .table-wrap { width:100%; max-width:100%; overflow:auto; }
    input,textarea { min-width:0; max-width:100%; }
    table { color:#33444f; }
    th { background:#f5f8f9!important; color:#627680!important; }
    td { border-color:#edf0f2!important; }
    tr:hover { background:#f8fafb!important; }
    .qr-wrapper { background:#f7faf8; border-color:#e0e9e3; }
    #connectedInfo { color:#627680!important; overflow-wrap:anywhere; }
    #logsContainer { background:#1b2b32; border-radius:8px; padding:16px; }
    #msgPreview { color:#52636d!important; background:#f8fafb; }
    a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible { outline:3px solid #76c5af; outline-offset:3px; }
    @media(max-width:980px){ .grid-bottom { grid-template-columns:minmax(0,1fr); } }
    @media(max-width:640px){ #newSupName,#newSupPhone { width:100%; flex:auto!important; } #newSupName { min-width:100%; } body { padding:16px; } header h1 { font-size:21px; } .card { padding:16px; } .stats-bar { grid-template-columns:repeat(2,minmax(0,1fr)); } .grid-top { grid-template-columns:minmax(0,1fr); } }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Central de fornecedores</h1>
        <div style="font-size:13px;color:var(--muted);margin-top:4px;">
          <strong>Trava Manual Ativa:</strong> Nenhum disparo é feito sem sua autorização. Os envios dependem da conexão, da autorização do contato e das travas da conta.
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <a href="/" class="btn-link btn-blue">Voltar ao buscador</a>
        <span class="badge AUTOJOIN">CONTROLES DE AUTOMAÇÃO</span>
        <span id="waStatusBadge" class="badge CONNECTING">Verificando...</span>
        <span id="campaignBadge" class="badge DISCONNECTED">DISPARO PARADO</span>
      </div>
    </header>

    <div id="pauseNotice" hidden role="status" style="background:#fff2d8;color:#815714;padding:16px;border-radius:8px;margin-bottom:20px;">Conta com restrição: envios e entradas automáticas pausados. Configurações e histórico continuam disponíveis.</div>
    <div class="stats-bar">
      <div class="stat-box">
        <div class="label">Total Fornecedores</div>
        <div class="val" id="stTotal">0</div>
      </div>
      <div class="stat-box">
        <div class="label">Pendentes Envio</div>
        <div class="val" id="stPending" style="color:#fbbf24;">0</div>
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

    <p id="replySummary" style="margin:0 0 18px;color:var(--muted);font-size:13px;">Histórico permanente. A fila continua somente pelos contatos pendentes.</p>
    <p style="margin:0 0 18px;color:var(--muted);font-size:12px;">As respostas anteriores serão identificadas conforme o WhatsApp sincronizar. Sem resposta registrada não significa que o fornecedor nunca respondeu.</p>
    <div class="grid-top">
      <!-- Card 1: Conexão WhatsApp -->
      <div class="card" style="margin-bottom:0;">
        <h2>
          <span>Conexão do WhatsApp</span>
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
          <span>Mensagens para fornecedores</span>
          <span style="font-size:12px;color:var(--muted);font-weight:normal;">Variáveis opcionais: <code>{nome}</code>, <code>{localizacao}</code></span>
        </h2>

        <label style="font-size:13px;color:var(--muted);display:block;margin-bottom:6px;">Texto que será enviado para cada fornecedor:</label>
        <label>Modelo 1 · Pedido direto</label><textarea id="msgInput" oninput="updatePreview()" style="min-height:80px;"></textarea>
        <details style="margin:12px 0;"><summary style="cursor:pointer;">Editar os outros quatro modelos</summary>
          <label>Modelo 2 · Ofertas do dia</label><textarea id="msgInput2" style="min-height:80px;"></textarea>
          <label>Modelo 3 · Cotação da loja</label><textarea id="msgInput3" style="min-height:80px;"></textarea>
          <label>Modelo 4 · Condições de compra</label><textarea id="msgInput4" style="min-height:80px;"></textarea>
          <label>Modelo 5 · Catálogo e preferência de contato</label><textarea id="msgInput5" style="min-height:80px;"></textarea>
        </details>
        <p style="font-size:12px;color:var(--muted);">Escolha o modelo adequado por fornecedor. Textos diferentes e intervalos maiores não garantem evitar restrições. Envie apenas para quem autorizou ou espera esse contato.</p>

        <div style="font-size:12px;color:var(--muted);margin-top:8px;">Pré-visualização exata no WhatsApp:</div>
        <div class="whatsapp-preview" id="msgPreview"></div>

        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;margin-top:14px;">
          <div>
            <label style="font-size:12px;color:var(--muted);">Intervalo Mínimo (seg):</label><br/>
            <input type="number" id="delayMin" value="300" min="300" max="86400" style="width:90px;margin-top:4px;" />
          </div>
          <div>
            <label style="font-size:12px;color:var(--muted);">Intervalo Máximo (seg):</label><br/>
            <input type="number" id="delayMax" value="600" min="300" max="86400" style="width:90px;margin-top:4px;" />
          </div>
          <div><label style="font-size:12px;">Limite por rodada:</label><br><input id="roundLimit" type="number" min="1" max="20" value="5" style="width:90px;margin-top:4px;"></div>
          <div style="flex:1;min-width:220px;">
            <label style="font-size:12px;color:var(--muted);">Enviar teste avulso para um número:</label>
            <div style="display:flex;gap:6px;margin-top:4px;">
              <input type="text" id="testPhone" placeholder="Ex: 5511999999999" style="flex:1;" />
              <button class="btn-ghost" onclick="sendTest()">Testar Envio</button>
            </div>
          </div>
        </div>

        <div class="btn-row">
          <button class="btn-blue" onclick="saveConfig()">Salvar modelos e intervalos</button>
          <button class="btn-green" id="btnStart" onclick="startCampaign()">Continuar envios pendentes</button>
          <button class="btn-warn" id="btnStop" onclick="stopCampaign()" disabled>Pausar Disparo</button>
        </div>
        <div id="currentSending" style="margin-top:12px;font-size:13px;color:#38bdf8;font-weight:600;"></div>
      </div>
    </div>

    <!-- Card 3: Simulador / Importador Manual de Lista para o Buscador (oculto por ora) -->
    <div class="card" style="display:none;">
      <h2>
        <span>Importar lista de preços</span>
        <div style="display:flex;gap:8px;">
          <button class="btn-ghost" onclick="loadSampleList()" style="padding:6px 12px;font-size:12px;">Preencher Lista de Exemplo</button>
          <button class="btn-ghost" onclick="clearProducts()" style="padding:6px 12px;font-size:12px;color:#f87171;">Limpar catálogo</button>
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
        <button class="btn-green" onclick="submitManualList()">Processar e publicar ofertas</button>
        <a href="/" class="btn-link btn-ghost">Ver catálogo</a>
      </div>
    </div>

    <!-- Card 4: Comunidades Entradas e Contatos Arquivados Monitorados -->
    <div class="card">
      <h2>
        <span>Conversas e grupos monitorados</span>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <input type="text" id="manualInviteLink" placeholder="Colar link chat.whatsapp.com/... para entrar e arquivar" style="width:300px;font-size:12px;" />
          <button class="btn-ghost" onclick="joinManualCommunity()" style="padding:6px 12px;font-size:12px;">Entrar e Arquivar Agora</button>
          <button class="btn-blue" onclick="archiveAllNow()" style="padding:6px 12px;font-size:12px;">Sincronizar conversas</button>
        </div>
      </h2>
      <p style="margin:0 0 10px 0;font-size:12px;color:#34d399;">
        A conexão permanece ativa. O catálogo aceita listas do dia recebidas entre 06h e 18h, no horário de Brasília.
      </p>
      <p id="captureStatus" role="status" style="font-size:12px;color:var(--muted);">Aguardando estado da captura…</p>
      <p id="inviteQueueStatus" style="font-size:12px;color:var(--muted);margin:10px 0;">Convites recebidos dos fornecedores cadastrados serão processados automaticamente.</p>
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
          <span>Lista de fornecedores</span><select id="supplierStatusFilter" aria-label="Filtrar situação do fornecedor" onchange="renderTable()" style="width:210px;"><option value="ALL">Todas as situações</option><option value="PENDENTE">Pendentes de envio</option><option value="ENVIADO">Enviados</option><option value="RESPONDEU">Responderam</option><option value="SEM_RESPOSTA">Sem resposta registrada</option><option value="CONFERIR">Conferir envio</option></select>
          <input type="text" id="searchInput" placeholder="Buscar fornecedor ou telefone..." oninput="renderTable()" style="width:240px;font-size:12px;padding:6px 10px;" />
        </h2>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
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
                <th>Permissão e modelo</th>
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
          <span>Atividade recente</span>
        </h2>
        <div class="logs-box" id="logsContainer"></div>
      </div>

    </div>
  </div>

  <script>
    function escapeHTML(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    let stateLoading = false;
    let allSuppliers = [];
    let historyMap = {};
    let configLoaded = false;
    let automationPaused = true;

    function updatePreview() {
      const raw = document.getElementById('msgInput').value || '';
      const sample = raw
        .replace(/\\{nome\\}/g, 'ADELS MUNDO CEL')
        .replace(/\\{telefone\\}/g, '5511962254545')
        .replace(/\\{localizacao\\}/g, 'SHOPPING ORIENTAL');
      document.getElementById('msgPreview').textContent = sample;
    }

    async function loadState() {
      if (stateLoading) return;
      stateLoading = true;
      try {
        const res = await fetch('/api/panel-state', { signal: AbortSignal.timeout(15000), cache: 'no-store' });
        if (!res.ok) throw new Error('Falha ao atualizar painel');
        const data = await res.json();

        if (!configLoaded && data.config) {
          const templates = data.config.mensagens || [data.config.mensagem || ''];
          for (let index = 0; index < 5; index++) document.getElementById('msgInput' + (index ? index + 1 : '')).value = templates[index] || '';
          document.getElementById('roundLimit').value = data.config.limite_rodada || 5;
          document.getElementById('delayMin').value = data.config.delay_min || 300;
          document.getElementById('delayMax').value = data.config.delay_max || 600;
          updatePreview();
          configLoaded = true;
        }

        automationPaused = !!data.automationPaused;
        document.getElementById('pauseNotice').hidden = !automationPaused;
        allSuppliers = data.fornecedores || [];
        historyMap = data.historico || {};

        const wa = data.whatsapp || {};
        const capture = wa.capture || {};
        const lastList = capture.lastProcessedAt ? new Date(capture.lastProcessedAt).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' }) : 'nenhuma nesta execução';
        document.getElementById('captureStatus').textContent =
          (capture.pending || 0) + ' listas aguardando processamento · ' + (capture.failed || 0) + ' exigem conferência · Última lista processada: ' + lastList +
          (capture.lastError ? ' · ' + capture.lastError : '');
        const comms = wa.communities || [];
        const archivedList = wa.archivedMonitored || [];
        document.getElementById('inviteQueueStatus').textContent = (wa.pendingInvites || 0) + ' convites na fila automática. Convites que exigem aprovação aguardam o administrador.';

        document.getElementById('stTotal').textContent = data.stats.total;
        document.getElementById('stPending').textContent = data.stats.pending;
        document.getElementById('stSent').textContent = data.stats.sent;
        document.getElementById('stCommunities').textContent = Math.max(
          comms.filter(c => ['ENTROU','ENTROU_E_ARQUIVOU'].includes(c.status)).length,
          archivedList.length
        );
        document.getElementById('stProducts').textContent = data.stats.productsCount || 0;
        document.getElementById('stErrors').textContent = data.stats.errors;
        document.getElementById('replySummary').textContent = data.respostasCount + ' fornecedores com resposta registrada. ' + (data.stats.eligible || 0) + ' contatos aptos para envio. Histórico permanente.';

        const commBody = document.getElementById('commTableBody');
        const combinedRows = [];
        for (const c of comms.slice().reverse()) {
          combinedRows.push({
            time: c.joinedAt || '-',
            name: c.groupName || 'Comunidade',
            type: c.senderName || 'Comunidade / Grupo',
            id: c.link || c.code || '-',
            status: c.status === 'ENTROU_E_ARQUIVOU' ? 'ENTROU E ARQUIVOU' : c.status === 'AGUARDANDO_APROVACAO' ? 'AGUARDANDO APROVAÇÃO' : (c.status || 'ATIVO')
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
          commBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;">Conecte o WhatsApp para sincronizar as conversas. Convites recebidos de fornecedores cadastrados entram automaticamente na fila.</td></tr>';
        } else {
          commBody.innerHTML = combinedRows.map(r => (
            '<tr>' +
              '<td style="color:#94a3b8;font-size:12px;">' + escapeHTML(r.time) + '</td>' +
              '<td><strong>' + escapeHTML(r.name) + '</strong></td>' +
              '<td>' + escapeHTML(r.type) + '</td>' +
              '<td><code>' + escapeHTML(r.id) + '</code></td>' +
              '<td><span class="pill ENTROU_E_ARQUIVOU">' + escapeHTML(r.status) + '</span></td>' +
            '</tr>'
          )).join('');
        }

        const waBadge = document.getElementById('waStatusBadge');
        waBadge.className = 'badge ' + (wa.status || 'CONNECTING');
        waBadge.textContent = wa.status === 'CONNECTED'
          ? 'WHATSAPP CONECTADO'
          : (wa.status === 'RECONNECTING' ? 'RECONECTANDO…' : (wa.status === 'QR_READY' ? 'AGUARDANDO QR CODE' : (wa.status || 'CONECTANDO')));

        const qrBox = document.getElementById('qrContainer');
        const connInfo = document.getElementById('connectedInfo');
        if (wa.status === 'CONNECTED') {
          qrBox.innerHTML = '<div style="font-size:24px;font-weight:800;color:#34d399;margin-bottom:6px;">[CONECTADO]</div><div style="font-weight:700;color:#34d399;margin-top:8px;">Aparelho conectado</div><div style="font-size:12px;color:#94a3b8;margin-top:4px;">Captura de listas das 06h às 18h (Brasília). Envios dependem de comando no painel.</div>';
          connInfo.textContent = wa.user ? ('Conectado como: ' + wa.user.name + ' (' + wa.user.id + ')') : '';
        } else if (wa.status === 'RECONNECTING') {
          qrBox.innerHTML = '<div style="font-size:20px;font-weight:800;color:#fbbf24;margin-bottom:6px;">[RECONECTANDO]</div><div style="font-weight:700;color:#fbbf24;margin-top:6px;">Renovando sessão automaticamente…</div><div style="font-size:12px;color:#94a3b8;margin-top:4px;">Sessão preservada — captura ao vivo retomada em segundos.</div>';
          connInfo.textContent = wa.user ? ('Sessão ativa: ' + wa.user.name + ' (' + wa.user.id + ')') : '';
        } else if (wa.status === 'QR_READY' && wa.qrDataUrl) {
          qrBox.innerHTML = '<img src="' + wa.qrDataUrl + '" alt="QR Code WhatsApp" /><div style="font-size:12px;color:#fbbf24;margin-top:8px;">Escaneie no WhatsApp > Aparelhos conectados</div>';
          connInfo.textContent = '';
        } else {
          const explanation = automationPaused ? 'Conexão automática pausada devido à restrição da conta.'
            : wa.lastDisconnect?.code === 440 ? 'Esta sessão foi aberta em outra instância. Encerre a instância duplicada antes de reconectar.'
            : wa.status === 'DISCONNECTED' ? 'WhatsApp desconectado. Aguardando a reconexão automática.' : 'Gerando sessão / QR Code…';
          qrBox.innerHTML = '<div style="color:#627680;">' + escapeHTML(explanation) + '</div>';
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
          document.getElementById('btnStart').disabled = automationPaused || wa.status !== 'CONNECTED';
          document.getElementById('btnStop').disabled = true;
          document.getElementById('currentSending').textContent = '';
        }

        document.querySelectorAll('[onclick=\"sendTest()\"],[onclick=\"joinManualCommunity()\"],[onclick=\"archiveAllNow()\"]').forEach(button => button.disabled = automationPaused);
        renderTable();

        const logs = data.logs || [];
        document.getElementById('logsContainer').innerHTML = logs.map(l => '<div>' + escapeHTML(l) + '</div>').join('');

      } catch (e) {
        document.getElementById('waStatusBadge').textContent = 'SEM ATUALIZAÇÃO — tentando novamente';
      } finally { stateLoading = false; }
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
        const st = h.respondeu ? 'RESPONDEU' : (h.status || 'PENDENTE');
        const filter = document.getElementById('supplierStatusFilter').value;
        if (filter === 'SEM_RESPOSTA' ? !(h.status === 'ENVIADO' && !h.respondeu) : filter === 'CONFERIR' ? !['INCERTO','ENVIANDO','ERRO','AGUARDANDO_CONFIRMACAO'].includes(st) : filter !== 'ALL' && filter !== st) continue;
        html += '<tr>' +
          '<td>' + (i + 1) + '</td>' +
          '<td><strong>' + escapeHTML(nome) + '</strong></td>' +
          '<td><code>' + escapeHTML(tel) + '</code></td>' +
          '<td style="color:#94a3b8;font-size:12px;">' + escapeHTML((s.datas || '-')) + '</td>' +
          '<td><span class="pill ' + escapeHTML(st) + '">' + escapeHTML(st.replaceAll('_',' ')) + '</span><br><small>' + escapeHTML(h.confirmacao || (h.status === 'ENVIADO' ? 'Registro antigo: sem confirmação detalhada' : '')) + '</small><br><small>' + escapeHTML(h.message_id || '') + '</small></td>' +
          '<td><label style="font-size:11px;"><input type="checkbox" data-authorize="' + i + '"' + (s.contato_autorizado ? ' checked' : '') + ' style="width:auto;"> Contato autorizado/esperado</label><select data-template="' + i + '" aria-label="Modelo da mensagem">' + [0,1,2,3,4].map(n => '<option value="' + n + '"' + (Number(s.modelo_mensagem || 0) === n ? ' selected' : '') + '>Modelo ' + (n+1) + '</option>').join('') + '</select></td>' +
          '<td><button class="btn-ghost" data-supplier="' + i + '"' + (automationPaused || !s.contato_autorizado || ['ENVIADO','RESPONDEU','ENVIANDO','INCERTO','AGUARDANDO_CONFIRMACAO'].includes(st) ? ' disabled' : '') + '>Enviar 1x</button></td>' +
        '</tr>';
      }
      tbody.innerHTML = html;
      tbody.querySelectorAll('[data-authorize],[data-template]').forEach(control => control.addEventListener('change', async () => {
        const index = Number(control.dataset.authorize ?? control.dataset.template);
        const row = control.closest('tr');
        const result = await fetch('/api/supplier-messaging', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone:allSuppliers[index].telefone, authorized:row.querySelector('[data-authorize]').checked, template:Number(row.querySelector('[data-template]').value)})});
        if (!result.ok) alert('Não foi possível salvar as preferências do fornecedor.');
        await loadState();
      }));
      tbody.querySelectorAll('[data-supplier]').forEach(button => button.addEventListener('click', () => {
        const supplier = allSuppliers[Number(button.dataset.supplier)];
        sendSingle(String(supplier.telefone || ''), supplier.nome || '');
      }));
    }

    async function saveConfig() {
      const messages = [1,2,3,4,5].map(n => document.getElementById('msgInput' + (n === 1 ? '' : n)).value);
      while (messages.length && !messages[messages.length - 1].trim()) messages.pop();
      const payload = {
        mensagens: messages,
        limite_rodada: Number(document.getElementById('roundLimit').value || 5),
        delay_min: Number(document.getElementById('delayMin').value || 300),
        delay_max: Number(document.getElementById('delayMax').value || 600)
      };
      const response = await fetch('/api/save-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) { alert('Não foi possível salvar. Confira os modelos e intervalos.'); return false; }
      await loadState();
      return true;
    }

    async function startCampaign() {
      if (!await saveConfig()) return;
      const res = await fetch('/api/start-campaign', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) alert(data.error || 'Não foi possível iniciar.');
      await loadState();
    }

    async function stopCampaign() {
      await fetch('/api/stop-campaign', { method: 'POST' });
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
      if (!await saveConfig()) return;
      const res = await fetch('/api/send-single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, name: 'Teste Manual' })
      });
      const d = await res.json();
      if (d.ok) alert(d.confirmed ? 'Mensagem confirmada pelo servidor. Confira a conversa no celular.' : 'Mensagem criada, aguardando confirmação. A fila ficará pausada para conferência.');
      else alert('Erro: ' + (d.error || 'Falha ao enviar'));
      await loadState();
    }

    async function sendSingle(phone, name) {
      if (!await saveConfig()) return;
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

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Access-Control-Max-Age", "3600")
        self.end_headers()

    def _send_json(self, obj, status=200, cors=False):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _check_api_v1_auth(self):
        expected = get_api_v1_key()
        if not expected:
            return False
        provided = (self.headers.get("X-API-Key") or "").strip()
        if not provided:
            auth_hdr = (self.headers.get("Authorization") or "").strip()
            if auth_hdr.lower().startswith("bearer "):
                provided = auth_hdr[7:].strip()
        return bool(provided) and hmac.compare_digest(provided, expected)

    def _send_html(self, html_str):
        body = html_str.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 15 * 1024 * 1024:
            raise ValueError("Corpo excede o limite")
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("Esperado objeto JSON")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/api/ping", "/healthz"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        assets = {"/catalog.css": "text/css; charset=utf-8", "/catalog.js": "text/javascript; charset=utf-8", "/catalog-ui.js": "text/javascript; charset=utf-8"}
        if parsed.path in assets:
            body = (Path(BASE_DIR) / "web" / parsed.path[1:]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", assets[parsed.path])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return


        # Rota Aberta / Pública -> Buscador de Preços
        if parsed.path in ("/", "/index.html", "/buscador"):
            self._send_html(BUSCADOR_HTML)
            return

        # Rota Administrativa -> Painel WhatsApp & Comunidades
        if parsed.path in ("/admin", "/painel"):
            self._send_html(ADMIN_HTML)
            return

        if parsed.path == "/api/products":
            produtos, _ = public_price_review(load_produtos())
            wa = bridge_status_lite()
            archived_cnt = int(wa.get("archivedMonitoredCount", 0))
            comm_cnt = int(wa.get("communitiesCount", 0))
            self._send_json({
                "products": [{k: v for k, v in p.items() if k not in
                              ("alertaPreco", "alertaFaixaModelo", "observacao", "revisao", "contextoOriginal")}
                             for p in produtos],
                "whatsappStatus": wa.get("status", "CONNECTING"),
                "communitiesCount": max(comm_cnt, archived_cnt),
            })
            return

        if parsed.path in ("/api/v1/feed", "/api/v1/suppliers", "/api/v1/status"):
            if not self._check_api_v1_auth():
                self._send_json({"ok": False, "error": "API key inválida ou ausente."}, status=401, cors=True)
                return
            if parsed.path == "/api/v1/feed":
                qs = parse_qs(parsed.query)
                self._send_json(build_api_v1_feed(qs), cors=True)
                return
            if parsed.path == "/api/v1/suppliers":
                self._send_json(build_api_v1_suppliers(), cors=True)
                return
            if parsed.path == "/api/v1/status":
                wa = bridge_status_lite()
                prods, _ = public_price_review(load_produtos())
                forns = {str(p.get("fornecedor") or "").strip() for p in prods if p.get("fornecedor")}
                self._send_json({
                    "ok": True,
                    "date": get_today_str(),
                    "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "whatsappStatus": wa.get("status", "CONNECTING"),
                    "monitoredChannels": max(int(wa.get("communitiesCount", 0)), int(wa.get("archivedMonitoredCount", 0))),
                    "totalOffers": len(prods),
                    "totalSuppliers": len(forns),
                    "dfuActive": True,
                    "lastSweep": last_guardian_sweep,
                    "lastColorAudit": last_color_audit,
                }, cors=True)
                return

        if parsed.path == "/api/panel-state":
            cfg = load_config()
            fornecedores = load_fornecedores()
            historico = load_historico()
            produtos = load_produtos()
            try:
                wa = bridge_get("/api/status")
            except Exception:
                wa = {"status": "PAUSADO" if automation_paused() else "CONNECTING", "logs": [], "communities": [], "automationPaused": automation_paused()}

            respostas = load_respostas()
            sent = sum(1 for f in fornecedores if history_for_phone(historico, f.get("telefone")).get("status") == "ENVIADO")
            errors = sum(1 for f in fornecedores if historico.get(str(f.get("telefone", "")).strip(), {}).get("status") == "ERRO")
            total = len(fornecedores)
            pending = sum(pending_supplier({**f, "contato_autorizado": True}, historico, respostas) for f in fornecedores)
            eligible = sum(pending_supplier(f, historico, respostas) for f in fornecedores)

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
                "automationPaused": automation_paused(),
                "fornecedores": fornecedores,
                "historico": {str(f.get("telefone", "")): {**history_for_phone(historico, f.get("telefone")), "respondeu": canonical_phone(f.get("telefone")) in respostas} for f in fornecedores},
                "respostasCount": sum(canonical_phone(f.get("telefone")) in respostas for f in fornecedores),
                "whatsapp": wa,
                "campaign": camp_copy,
                "stats": {
                    "total": total,
                    "pending": pending,
                    "eligible": eligible,
                    "sent": sent,
                    "errors": errors,
                    "productsCount": len(produtos),
                },
                "logs": combined_logs[:90],
            })
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        try:
            self._handle_post()
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json({"ok": False, "error": "Dados inválidos na solicitação."}, status=400)
        except (urllib.error.URLError, TimeoutError):
            self._send_json({"ok": False, "error": "WhatsApp indisponível. Verifique a conexão no painel."}, status=503)

    def _handle_post(self):
        origin = self.headers.get("Origin")
        if (origin and urlparse(origin).netloc != self.headers.get("Host")) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send_json({"error": "Origem da solicitação inválida"}, status=403)
            return
        if int(self.headers.get("Content-Length", "0")) > 0 and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._send_json({"error": "Envie application/json"}, status=415)
            return
        parsed = urlparse(self.path)
        if automation_paused() and parsed.path in ("/api/start-campaign", "/api/send-single", "/api/join-community", "/api/archive-all"):
            self._send_json({"ok": False, "error": "Automações pausadas pela restrição da conta. Apenas configurações e consultas estão disponíveis."}, status=423)
            return

        if parsed.path.startswith("/api/internal/") and self.client_address[0] not in ("127.0.0.1", "::1"):
            self._send_json({"error": "Acesso interno"}, status=403)
            return

        if parsed.path == "/api/internal/message-ack":
            body = self._read_body_json()
            self._send_json({"ok": True, "recorded": record_message_ack(body.get("phone"), body.get("messageId"), body.get("status"))})
            return

        if parsed.path == "/api/supplier-messaging":
            body = self._read_body_json()
            phone = canonical_phone(body.get("phone"))
            with data_lock:
                suppliers = load_fornecedores()
                supplier = next((item for item in suppliers if canonical_phone(item.get("telefone")) == phone), None)
                if not supplier:
                    self._send_json({"error": "Fornecedor não encontrado"}, status=404)
                    return
                supplier["contato_autorizado"] = body.get("authorized") is True
                supplier["modelo_mensagem"] = max(0, min(4, int(body.get("template", 0))))
                supplier["autorizacao_registrada_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
                save_fornecedores(suppliers)
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/internal/supplier-reply":
            body = self._read_body_json()
            recorded = record_supplier_reply(body.get("phone"), body.get("timestamp"), body.get("messageId"))
            self._send_json({"ok": True, "recorded": recorded})
            return

        # Endpoint interno chamado automaticamente pelo whatsapp_bridge.js a cada mensagem recebida nos grupos/comunidades
        if parsed.path == "/api/internal/ingest-message":
            body = self._read_body_json()
            text = str(body.get("text", "")).strip()
            remote_jid = str(body.get("remoteJid", ""))
            participant_jid = str(body.get("participantJid", ""))
            push_name = str(body.get("pushName", ""))
            group_name = str(body.get("groupName", ""))
            image_base64 = str(body.get("imageBase64", "")).strip()
            msg_ts = body.get("timestamp")

            # Bloqueio explícito do grupo/comunidade 'iFindz - Produtos Apple'
            combined_origin = f"{group_name} {push_name} {remote_jid}".lower()
            if "ifindz" in combined_origin:
                self._send_json({"ok": True, "parsedCount": 0, "ignoredBlockedGroup": True})
                return

            # Validação estrita: somente processar mensagens recebidas hoje entre 06:00 e 18:00
            if type(msg_ts) not in (int, float) or not 0 < msg_ts < 253402300800:
                self._send_json({"ok": False, "error": "Horário de origem inválido."}, status=422)
                return
            today = datetime.date.today()
            opens = datetime.datetime.combine(today, datetime.time(6)).timestamp()
            closes = datetime.datetime.combine(today, datetime.time(18)).timestamp()
            if not opens <= msg_ts < closes:
                self._send_json({"ok": True, "parsedCount": 0, "ignoredOutsideWindow": True})
                return

            sup_name, sup_phone = identify_supplier(participant_jid, remote_jid, push_name, group_name, text)
            message = {
                "supplierName": sup_name, "supplierPhone": sup_phone, "text": text,
                "groupName": group_name, "pushName": push_name, "timestamp": msg_ts,
                "remoteJid": remote_jid, "participantJid": participant_jid,
                "messageId": body.get("messageId"), "imageBase64": image_base64,
                "hasImage": bool(image_base64 or body.get("hasImage")),
                "mediaIssue": body.get("mediaIssue")
            }
            identity = daily_review.save_message(BASE_DIR, message)
            if message["hasImage"]:
                items = daily_review.media_offers(BASE_DIR, identity, message, offer_key)
            else:
                items = parse_supplier_message(text, sup_name, sup_phone, group_name or push_name,
                                               msg_timestamp=msg_ts, include_non_new=True)
                for item in items:
                    item["fonteCaptura"] = "lista"
            if items:
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

            timestamp = int(time.time())
            daily_review.save_message(BASE_DIR, {"supplierName": sup_name, "supplierPhone": sup_phone,
                                                  "text": text, "groupName": "Importado no Painel",
                                                  "timestamp": timestamp, "includeNonNew": False})
            items = parse_supplier_message(text, sup_name, sup_phone, "Importado no Painel", msg_timestamp=timestamp)
            if items:
                saved = upsert_parsed_products(items)
                add_panel_log(f"[PRODUTO] {saved} produtos importados manualmente para o fornecedor {sup_name}")
                self._send_json({"ok": True, "parsedCount": saved})
            else:
                self._send_json({"ok": False, "parsedCount": 0})
            return

        if parsed.path == "/api/clear-products":
            with products_lock:
                atomic_json_write(os.path.join(BASE_DIR, "ultima_limpeza_catalogo.json"),
                                  {"data": get_today_str(), "hora": time.strftime("%H:%M:%S")})
                save_produtos([])
            add_panel_log("[LIMPEZA] Lista de produtos do Buscador foi limpa pelo administrador.")
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/save-config":
            body = self._read_body_json()
            cfg = load_config()
            if "mensagens" in body:
                cfg["mensagens"] = body["mensagens"]
            elif "mensagem" in body:
                cfg["mensagens"][0] = str(body["mensagem"])
            for key in ("delay_min", "delay_max", "limite_rodada"):
                if key in body:
                    cfg[key] = body[key]
            cfg = normalized_messaging_config(cfg)
            save_config(cfg)
            add_panel_log("Configurações e mensagem atualizadas no painel.")
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
            self._send_json({"ok": False, "error": "Histórico protegido: os envios anteriores não podem ser apagados."}, status=409)
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
            supplier = next((item for item in load_fornecedores() if canonical_phone(item.get("telefone")) == canonical_phone(phone)), {"nome": name, "telefone": phone})
            msg = supplier_message(cfg, supplier)
            if not phone or not msg.strip():
                self._send_json({"ok": False, "error": "Telefone e mensagem são obrigatórios."}, status=400)
                return
            resp = deliver_supplier(supplier, msg)
            self._send_json(resp)
            return

        if parsed.path == "/api/join-community":
            body = self._read_body_json()
            link_or_code = str(body.get("linkOrCode", "")).strip()
            try:
                resp = bridge_post("/api/join-community", {"linkOrCode": link_or_code})
                self._send_json(resp)
            except urllib.error.HTTPError as error:
                try:
                    detail = json.loads(error.read().decode("utf-8"))
                    self._send_json({"ok": False, "error": detail.get("error", "O WhatsApp recusou a entrada.")}, status=error.code)
                except (ValueError, UnicodeError):
                    self._send_json({"ok": False, "error": "O WhatsApp recusou a entrada."}, status=502)
            except (urllib.error.URLError, TimeoutError):
                self._send_json({"ok": False, "error": "Motor WhatsApp indisponível. Verifique a conexão no painel."}, status=503)
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


def cleanup_old_products_on_start():
    try:
        hoje = get_today_str()
        atuais_todos = load_produtos(only_today=False)
        valid = [
            p for p in atuais_todos
            if p.get("data") == hoje and str(p.get("hora", "00:00:00")) >= "06:00:00"
        ]
        expired = len(valid) != len(atuais_todos)
        snapshot = [dict(p) for p in valid]
        latest = {}
        for row in valid:
            ok, _, item = validate_product(dict(row), allow_seminovo=True)
            if ok:
                key = offer_key(item)
                if key not in latest or item.get("hora", "") >= latest[key].get("hora", ""):
                    latest[key] = item
        valid, _ = equalize_catalog_prices(list(latest.values()))
        if expired:
            archive_produtos(atuais_todos)
        if expired or snapshot != valid:
            save_produtos(valid)
            add_panel_log(f"[STARTUP] Limpeza de preços: mantidos {len(valid)} produtos válidos de hoje ({hoje}) após 06:00.")
    except Exception as e:
        add_panel_log(f"[STARTUP] Erro na limpeza inicial: {e}")


catalog_monitor = {"checked": None, "issues": {}, "examples": [], "offers": 0}


def audit_catalog(rows):
    seen = set()
    issues = {}
    examples = []
    for row in rows:
        problems = []
        key = offer_key(row)
        if key in seen:
            problems.append("Oferta duplicada do fornecedor")
        seen.add(key)
        valid, reason, item = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
        if not valid:
            problems.append(reason)
        else:
            if apple_reference.model_status(item["modelo"]) is False:
                problems.append("Modelo pendente de confirmação pela Apple")
            evidence = review_quoted_price(item)
            if evidence:
                problems.append(evidence)
        for reason in problems:
            issues[reason] = issues.get(reason, 0) + 1
            if len(examples) < 12:
                examples.append(f"{row.get('modelo')} · {row.get('fornecedor')} · {reason}")
    visible, held = public_price_review(rows)
    display = subprocess.run([NODE_EXE, os.path.join(BASE_DIR, "scripts", "audit_display.mjs")],
                             input=json.dumps(visible, ensure_ascii=False), text=True,
                             capture_output=True, timeout=40, check=True)
    visibility = json.loads(display.stdout)
    if visibility["missing"]:
        issues["Ofertas sem cartão no painel"] = visibility["missing"]
    if held:
        issues["Ofertas retidas na comparação de preços"] = len(held)
    return {"offers": len(rows), "issues": issues, "examples": examples, "exibicao": visibility}


def apply_reviewed_offers(items):
    with products_lock:
        current = {offer_key(row): row for row in load_produtos()}
        marker = Path(BASE_DIR) / "ultima_limpeza_catalogo.json"
        cleared = json.loads(marker.read_text()) if marker.exists() else {}
        changes = []
        for row in items:
            if cleared.get("data") == row["data"] and row["hora"] <= cleared["hora"]:
                continue
            if row["data"] != get_today_str():
                continue
            previous = current.get(offer_key(row))
            if previous and (previous["hora"] > row["hora"] or
                             (previous["hora"] == row["hora"] and previous["preco"] == row["preco"])):
                continue
            changes.append(row)
        return upsert_parsed_products(changes)


def live_review_worker():
    while True:
        daily_review.live_wakeup.clear()
        try:
            report = daily_review.run(BASE_DIR, offer_key, apply_reviewed_offers, live=True)
            if report:
                atomic_json_write(os.path.join(BASE_DIR, "revisao_listas", "ultima_revisao_ao_vivo.json"), report)
                continue
        except Exception as exc:
            print(f"[RELEITURA AO VIVO] {type(exc).__name__}: {exc}", flush=True)
        daily_review.live_wakeup.wait(10)


def daily_review_worker():
    migrated = False
    while True:
        try:
            if not migrated:
                daily_review.import_previous_log(BASE_DIR)
                migrated = True
            report = daily_review.run(BASE_DIR, offer_key, apply_reviewed_offers, pace=0.03)
            if report is not None:
                atomic_json_write(os.path.join(BASE_DIR, "revisao_listas", "ultima_revisao.json"), report)
        except Exception as exc:
            print(f"[RELEITURA] {type(exc).__name__}: {exc}", flush=True)
        time.sleep(10)


def catalog_monitor_worker():
    global catalog_monitor
    while True:
        try:
            rows = load_produtos(only_today=False)
            today = get_today_str()
            report = audit_catalog([r for r in rows if r.get("data") == today and
                                    str(r.get("hora", "")) >= "06:00:00"])
        except Exception as exc:
            report = {"offers": None, "issues": {"Verificação interrompida": 1},
                      "examples": [f"Falha ao verificar o catálogo ({type(exc).__name__})"]}
        report["checked"] = time.strftime("%d/%m/%Y %H:%M:%S")
        if report["issues"] != catalog_monitor["issues"]:
            print(f"[MONITOR] {report['issues'] or 'Nenhuma pendência detectada'}")
        catalog_monitor = report
        try:
            atomic_json_write(os.path.join(BASE_DIR, "monitor_catalogo_interno.json"), report)
        except OSError as exc:
            print(f"[MONITOR] Falha ao gravar relatório interno: {type(exc).__name__}", flush=True)
        time.sleep(60)


API_V1_KEY_FILE = os.path.join(BASE_DIR, "api_v1_key.txt")


def get_api_v1_key():
    env_key = str(os.environ.get("BUSCABUSCA_API_KEY") or "").strip()
    if env_key:
        return env_key
    key_path = os.path.join(BASE_DIR, "api_v1_key.txt")
    if os.path.exists(key_path):
        try:
            saved = Path(key_path).read_text(encoding="utf-8").strip()
            if saved:
                return saved
        except OSError:
            pass
    generated = secrets.token_hex(24)
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(generated + "\n")
    except OSError:
        pass
    return generated


def _normalize_br_phone(raw_phone):
    digits = re.sub(r"\D", "", str(raw_phone or ""))
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    return digits


def build_api_v1_feed(query_params=None):
    qs = query_params or {}
    cat_filter = (qs.get("category") or qs.get("categoria") or [""])[0].strip().upper()
    cond_filter = (qs.get("condition") or qs.get("condicao") or [""])[0].strip().lower()
    model_filter = (qs.get("model") or qs.get("modelo") or [""])[0].strip().lower()
    sup_filter = (qs.get("supplier") or qs.get("fornecedor") or [""])[0].strip().lower()

    produtos, _ = public_price_review(load_produtos())
    wa = bridge_status_lite()
    offers = []
    suppliers_set = set()

    for p in produtos:
        base_price = float(p.get("preco") or 0)
        if base_price <= 0:
            continue
        vit_cat = classify_vitrine_category(p)
        is_semi = bool(p.get("is_seminovo") or vit_cat == "SEMI" or _is_seminovo_condition(p.get("condicao", ""), p.get("categoria", "")))
        dfu = compute_dfu_delta(p)
        final_price = round(base_price + dfu, 2)
        if cat_filter and vit_cat != cat_filter and cat_filter not in str(p.get("categoria") or "").upper():
            continue
        if cond_filter and cond_filter not in str(p.get("condicao") or "").lower():
            continue
        if model_filter and model_filter not in str(p.get("modelo") or "").lower():
            continue
        sup_name = str(p.get("fornecedor") or "").strip()
        if sup_filter and sup_filter not in sup_name.lower():
            continue
        if sup_name:
            suppliers_set.add(sup_name)
        offers.append({
            "id": str(p.get("id") or ""),
            "modelo": str(p.get("modelo") or "").strip(),
            "capacidade": str(p.get("capacidade") or "-").strip(),
            "cor": str(p.get("cor") or "-").strip(),
            "categoria": str(p.get("categoria") or "").strip(),
            "vitrineCategory": vit_cat,
            "condicao": "Seminovo" if is_semi and str(p.get("condicao") or "").strip().lower() in ("", "condição não informada") else str(p.get("condicao") or "Novo / Lacrado"),
            "isSeminovo": is_semi,
            "preco": final_price,
            "precoBase": round(base_price, 2),
            "dfu": dfu,
            "fornecedor": sup_name,
            "telefone": _normalize_br_phone(p.get("telefone")),
            "origem": str(p.get("origem") or "").strip(),
            "data": str(p.get("data") or ""),
            "hora": str(p.get("hora") or ""),
        })

    return {
        "ok": True,
        "date": get_today_str(),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dfuActive": True,
        "appleOnly": True,
        "whatsappStatus": wa.get("status", "CONNECTING"),
        "totalOffers": len(offers),
        "totalSuppliers": len(suppliers_set),
        "lastSweep": last_guardian_sweep.get("checked"),
        "offers": offers,
    }


def build_api_v1_suppliers():
    produtos, _ = public_price_review(load_produtos())
    registered = load_fornecedores()
    active_map = {}
    for p in produtos:
        name = str(p.get("fornecedor") or "").strip()
        if not name:
            continue
        entry = active_map.setdefault(name, {
            "nome": name,
            "telefone": _normalize_br_phone(p.get("telefone")),
            "ofertasHoje": 0,
            "ultimaAtualizacao": "",
        })
        entry["ofertasHoje"] += 1
        if not entry["telefone"]:
            entry["telefone"] = _normalize_br_phone(p.get("telefone"))
        hora = str(p.get("hora") or "")
        if hora > entry["ultimaAtualizacao"]:
            entry["ultimaAtualizacao"] = hora

    active_list = sorted(active_map.values(), key=lambda x: (-x["ofertasHoje"], x["nome"]))
    return {
        "ok": True,
        "date": get_today_str(),
        "totalRegistered": len(registered),
        "totalActiveToday": len(active_list),
        "suppliers": active_list,
    }


GUARDIAN_SWEEP_INTERVAL_SECONDS = 300
last_guardian_sweep = {"checked": None, "analyzed": 0, "validAfter": 0, "removed": 0, "reclassified": 0, "deduplicated": 0, "actions": []}


def perform_guardian_sweep(log_anomalies=True):
    """Executa vistoria completa e autocorreção em todas as ofertas ativas do dia."""
    global last_guardian_sweep
    with products_lock:
        hoje = get_today_str()
        atuais_todos = load_produtos(only_today=False)
        atuais = [
            p for p in atuais_todos
            if p.get("data") == hoje and str(p.get("hora", "00:00:00")) >= "06:00:00"
        ]
        if not atuais:
            report = {
                "checked": time.strftime("%d/%m/%Y %H:%M:%S"),
                "changed": False,
                "analyzed": 0,
                "validAfter": 0,
                "removed": 0,
                "reclassified": 0,
                "deduplicated": 0,
                "actions": [],
            }
            last_guardian_sweep = report
            return report

        healed, report = run_guardian_sweep(atuais, offer_key_fn=offer_key, log_anomalies=log_anomalies)
        if report["changed"]:
            save_produtos(healed)
            add_panel_log(
                f"[VISTORIA 5MIN] 🔍 {report['analyzed']} ofertas auditadas · "
                f"{report['reclassified']} movidas p/ Seminovos · {report['removed']} removidas · "
                f"{report['deduplicated']} duplicatas limpas -> {report['validAfter']} válidas."
            )
        else:
            add_panel_log(
                f"[VISTORIA 5MIN] ✅ Vistoria limpa: {report['analyzed']} ofertas ativas conferidas, 0 divergências."
            )
        last_guardian_sweep = report
        try:
            atomic_json_write(os.path.join(BASE_DIR, "ultima_vistoria_5min.json"), report)
        except OSError as exc:
            print(f"[VISTORIA 5MIN] Falha ao gravar relatório: {type(exc).__name__}", flush=True)
        return report


def guardian_sweep_worker():
    """Robô autônomo que realiza vistoria e autocorreção completa a cada 5 minutos."""
    time.sleep(15)
    while True:
        try:
            perform_guardian_sweep(log_anomalies=True)
        except Exception as exc:
            print(f"[VISTORIA 5MIN] Erro na vistoria periódica: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(GUARDIAN_SWEEP_INTERVAL_SECONDS)


COLOR_AUDIT_INTERVAL_SECONDS = 180
last_color_audit = {"checked": None, "analyzed": 0, "validAfter": 0, "corrected": 0, "removed": 0, "deduplicated": 0, "learnedModels": [], "actions": []}


def perform_color_guardian_audit(log_anomalies=True, allow_learn=True):
    """Robô autônomo de cores: corrige cor de pulseira vs caixa, bloqueia cores inexistentes e aprende novos modelos."""
    global last_color_audit
    with products_lock:
        hoje = get_today_str()
        atuais_todos = load_produtos(only_today=False)
        atuais = [
            p for p in atuais_todos
            if p.get("data") == hoje and str(p.get("hora", "00:00:00")) >= "06:00:00"
        ]
        if not atuais:
            report = {
                "checked": time.strftime("%d/%m/%Y %H:%M:%S"),
                "changed": False,
                "analyzed": 0,
                "validAfter": 0,
                "corrected": 0,
                "removed": 0,
                "deduplicated": 0,
                "learnedModels": [],
                "actions": [],
            }
            last_color_audit = report
            return report

        healed, report = color_guardian.run_color_guardian_sweep(
            atuais, offer_key_fn=offer_key, log_anomalies=log_anomalies, allow_learn=allow_learn
        )
        if report["changed"]:
            save_produtos(healed)
            add_panel_log(
                f"[ROBÔ DE CORES] 🎨 {report['analyzed']} ofertas auditadas · "
                f"{report['corrected']} corrigidas · {report['removed']} cores inválidas removidas · "
                f"{report['deduplicated']} duplicatas limpas -> {report['validAfter']} válidas."
            )
        else:
            add_panel_log(
                f"[ROBÔ DE CORES] ✅ Auditoria limpa: {report['analyzed']} ofertas conferidas, 0 cores inexistentes."
            )
        if report.get("learnedModels"):
            add_panel_log(f"[ROBÔ DE CORES] 🧠 Cores oficiais aprendidas para: {', '.join(report['learnedModels'])}")
        last_color_audit = report
        try:
            atomic_json_write(color_guardian.COLOR_AUDIT_REPORT_FILE, report)
        except OSError as exc:
            print(f"[ROBÔ DE CORES] Falha ao gravar relatório: {type(exc).__name__}", flush=True)
        return report


def color_guardian_worker():
    """Robô autônomo dedicado a auditar e aprender cores oficiais a cada 3 minutos."""
    time.sleep(8)
    while True:
        try:
            perform_color_guardian_audit(log_anomalies=True, allow_learn=True)
        except Exception as exc:
            print(f"[ROBÔ DE CORES] Erro na auditoria periódica: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(COLOR_AUDIT_INTERVAL_SECONDS)


def main():
    reset_thread.start()
    load_config()
    cleanup_old_products_on_start()
    threading.Thread(target=catalog_monitor_worker, daemon=True).start()
    threading.Thread(target=guardian_sweep_worker, daemon=True).start()
    threading.Thread(target=color_guardian_worker, daemon=True).start()
    threading.Thread(target=refresh_model_reference, daemon=True).start()
    threading.Thread(target=live_review_worker, daemon=True).start()
    threading.Thread(target=daily_review_worker, daemon=True).start()
    ensure_bridge_running()
    threading.Thread(target=bridge_supervisor, daemon=True).start()
    add_panel_log(f"Buscador de Preços (/) e Painel Admin (/admin) rodando em http://0.0.0.0:{PANEL_PORT}")
    server = QuietThreadingHTTPServer(("0.0.0.0", PANEL_PORT), PanelHandler)
    server.serve_forever()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Ignora clientes que fecham a conexão antes do fim da resposta (BrokenPipe/ConnectionReset)."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys as _sys
        if isinstance(_sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


if __name__ == "__main__":
    main()
