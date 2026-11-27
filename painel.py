import gzip
import hashlib
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
import coverage_guardian
import access_control
from parser_listas import parse_supplier_message
from price_guardian import (
    offer_color, public_price_review, review_quoted_price, validate_product,
    log_anomaly, equalize_catalog_prices, load_anomalies, run_guardian_sweep,
    compute_dfu_delta, classify_vitrine_category, _is_seminovo_condition,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORNECEDORES_FILE = os.path.join(BASE_DIR, "fornecedores.json")
CATALOG_ARCHIVE_DIR = os.path.join(BASE_DIR, "catalogo_arquivo")
PRODUTOS_FILE = os.path.join(BASE_DIR, "produtos_dia.json")

PANEL_PORT = int(os.environ.get("PORT", "3050"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "3051"))
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"

DEFAULT_NODE_WIN = r"C:\Users\Mundo Apple\.gemini\antigravity\scratch\node-env\node-v20.17.0-win-x64\node.exe"
NODE_EXE = DEFAULT_NODE_WIN if os.path.exists(DEFAULT_NODE_WIN) else (shutil.which("node") or "node")
BRIDGE_SCRIPT = os.path.join(BASE_DIR, "whatsapp_bridge.js")

def automation_paused():
    return os.environ.get("BUSCABUSCA_PAUSED", "0") == "1"


state_lock = threading.Lock()
products_lock = threading.RLock()
data_lock = threading.RLock()
_panel_logs = []


def add_panel_log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with state_lock:
        _panel_logs.insert(0, line)
        del _panel_logs[250:]


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


def load_fornecedores():
    if not os.path.exists(FORNECEDORES_FILE):
        return []
    with open(FORNECEDORES_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


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
        invalidate_products_cache()


_products_cache_lock = threading.Lock()
_products_cache = {
    "version": 0,
    "built_at": 0.0,
    "base_list": None,
    "dfu_list": None,
    "wa_status": "CONNECTING",
    "comm_count": 0,
    "http_bytes": {},
}
PRODUCTS_CACHE_TTL_SECONDS = 30.0


def invalidate_products_cache():
    with _products_cache_lock:
        _products_cache["version"] += 1
        _products_cache["built_at"] = 0.0
        _products_cache["base_list"] = None
        _products_cache["dfu_list"] = None
        _products_cache["http_bytes"].clear()


def _get_products_http_payload(user):
    """Retorna (etag, raw_bytes, gz_bytes) em cache de memória, separado por modo (SEM DFU vs COM DFU)."""
    while True:
        now = time.monotonic()
        with _products_cache_lock:
            stale = (_products_cache["base_list"] is None) or (now - _products_cache["built_at"] > PRODUCTS_CACHE_TTL_SECONDS)
            version = _products_cache["version"]
        if stale:
            produtos, _ = public_price_review(load_produtos())
            wa = bridge_status_lite()
            archived_cnt = int(wa.get("archivedMonitoredCount", 0))
            comm_cnt = int(wa.get("communitiesCount", 0))
            base_list = access_control.apply_user_pricing(produtos, False, compute_dfu_delta)
            dfu_list = access_control.apply_user_pricing(produtos, True, compute_dfu_delta)
            with _products_cache_lock:
                if _products_cache["version"] != version:
                    continue
                _products_cache["built_at"] = time.monotonic()
                _products_cache["base_list"] = base_list
                _products_cache["dfu_list"] = dfu_list
                _products_cache["wa_status"] = wa.get("status", "CONNECTING")
                _products_cache["comm_count"] = max(comm_cnt, archived_cnt)
                _products_cache["http_bytes"].clear()

        if user:
            dfu_flag = bool(user.get("dfu_enabled"))
            cache_key = ("user", user.get("username"), user.get("role"), user.get("expires_at"), dfu_flag)
        else:
            dfu_flag = False
            cache_key = ("local", "", "", None, False)

        with _products_cache_lock:
            if _products_cache["base_list"] is None:
                continue
            cached = _products_cache["http_bytes"].get(cache_key)
            if cached:
                return cached
            items = _products_cache["dfu_list"] if dfu_flag else _products_cache["base_list"]
            if user:
                payload = {
                    "products": items,
                    "dfuApplied": True,
                    "user": {
                        "username": user.get("username"),
                        "name": user.get("name"),
                        "role": user.get("role"),
                        "expires_at": user.get("expires_at"),
                    },
                    "whatsappStatus": _products_cache["wa_status"],
                    "communitiesCount": _products_cache["comm_count"],
                }
            else:
                payload = {
                    "products": items,
                    "whatsappStatus": _products_cache["wa_status"],
                    "communitiesCount": _products_cache["comm_count"],
                }
            raw_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            gz_bytes = gzip.compress(raw_bytes, compresslevel=5)
            etag = f'"{hashlib.sha1(raw_bytes).hexdigest()[:16]}"'
            entry = (etag, raw_bytes, gz_bytes)
            if len(_products_cache["http_bytes"]) >= 64:
                _products_cache["http_bytes"].clear()
            _products_cache["http_bytes"][cache_key] = entry
            return entry


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
    from price_guardian import sim_variant
    phone = re.sub(r"\D", "", str(item.get("telefone") or ""))
    supplier = phone or str(item.get("fornecedor") or item.get("id") or "").strip().casefold()
    model = re.sub(r"\bSE\s*(\d+)\b", r"SE \1", str(item.get("modelo") or "").upper()).strip()
    if model.startswith("IPAD"):
        model = re.sub(r"\b(?:GB|TB)\b", " ", model)
        model = re.sub(r"\bWI[ -]?FI\b", "WI-FI", model)
        model = " ".join(model.split())
    return (item.get("data"), supplier, model,
            re.sub(r"\s+", "", str(item.get("capacidade") or "-")).upper(),
            offer_color(item), str(item.get("condicao") or "").strip().casefold(),
            sim_variant(item))


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
        source_current = {}
        for raw_item in new_items:
            source_key = (raw_item.get("mensagemOrigem"), raw_item.get("mensagemTimestamp", 0), raw_item.get("mensagemEditada", False))
            if source_key[0]:
                if source_key not in source_current:
                    source_current[source_key] = daily_review.source_is_current(BASE_DIR, *source_key)
                if not source_current[source_key]:
                    continue
            review_reason = review_quoted_price(raw_item)
            if review_reason:
                log_anomaly(raw_item, review_reason)
                continue
            # Confere modelo, capacidade, cor e condição antes da atualização.
            valid, reason, sanitized_item = validate_product(raw_item, atuais, allow_seminovo=True)
            if not valid or not sanitized_item:
                continue

            item = sanitized_item
            if is_guardian_quarantined(item):
                continue
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


def apply_supplier_message(items, message):
    """Substitui a revisão da mensagem, inclusive quando não resta oferta válida."""
    with products_lock:
        if not daily_review.source_is_current(BASE_DIR, daily_review.message_origin(message),
                                               message["timestamp"], bool(message.get("editKey"))):
            return 0
        if message.get("editKey"):
            origin = daily_review.message_origin(message)
            current = load_produtos()
            retained = [row for row in current if not (
                origin and row.get("mensagemOrigem") == origin and
                row.get("mensagemTimestamp", 0) <= message["timestamp"])]
            if retained != current:
                save_produtos(retained)
        return upsert_parsed_products(items) if items else 0


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
                try:
                    atomic_json_write(
                        os.path.join(BASE_DIR, "quarentena_guardiao.json"),
                        {"date": today_str, "keys": []},
                    )
                except OSError:
                    pass
                add_panel_log("[RESET DIÁRIO] 🕛 MEIA-NOITE! Catálogo e quarentena zerados; nova captura abre às 06:00 BRT.")
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
_panel_groups_lock = threading.Lock()
_panel_groups_refresh_lock = threading.Lock()
_panel_groups_cache = {"at": 0.0, "groups": []}


def bridge_get(path, timeout=8):
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def panel_groups_snapshot():
    now = time.time()
    with _panel_groups_lock:
        cached_at = _panel_groups_cache["at"]
        cached_groups = list(_panel_groups_cache["groups"])
    if cached_at and now - cached_at < 60:
        return cached_groups, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(cached_at)), None
    with _panel_groups_refresh_lock:
        now = time.time()
        with _panel_groups_lock:
            cached_at = _panel_groups_cache["at"]
            cached_groups = list(_panel_groups_cache["groups"])
        if cached_at and now - cached_at < 60:
            return cached_groups, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(cached_at)), None
        try:
            response = bridge_get("/api/groups", timeout=25)
            if not isinstance(response, dict) or not response.get("ok") or not isinstance(response.get("groups"), list):
                raise ValueError((response.get("error") if isinstance(response, dict) else None) or "Resposta da lista de grupos inválida.")
            groups = response["groups"]
            with _panel_groups_lock:
                _panel_groups_cache.update(at=now, groups=groups)
            return groups, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)), None
        except (OSError, ValueError, TimeoutError) as exc:
            checked_at = (
                time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(cached_at))
                if cached_at else None
            )
            return cached_groups, checked_at, f"{type(exc).__name__}: {exc}"


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


# ==============================================================================
# PÁGINA 1: BUSCADOR DOS MELHORES PREÇOS DO DIA (/) + LOGIN (/login)
# ==============================================================================
BUSCADOR_HTML = (Path(BASE_DIR) / "web" / "index.html").read_text(encoding="utf-8")
LOGIN_HTML = (Path(BASE_DIR) / "web" / "login.html").read_text(encoding="utf-8")


# ==============================================================================
# PÁGINA 2: PAINEL ADMINISTRATIVO WHATSAPP & COMUNIDADES (/admin)
# ==============================================================================
ADMIN_HTML = (Path(BASE_DIR) / "web" / "admin.html").read_text(encoding="utf-8")


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

    def _send_json(self, obj, status=200, cors=False, set_cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_redirect(self, location, status=302, set_cookie=None):
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_direct_local_request(self):
        peer = (self.client_address[0] or "")
        has_proxy = bool(self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP"))
        host_hdr = (self.headers.get("Host") or "").lower()
        is_local_host = (not host_hdr) or host_hdr.startswith(("127.0.0.1", "localhost"))
        return peer in ("127.0.0.1", "::1") and not has_proxy and is_local_host

    def _current_user(self):
        token = access_control.extract_cookie_token(self.headers.get("Cookie", ""))
        if not token:
            return None
        return access_control.verify_session_token(token)

    def _build_session_cookie(self, token, max_age=None):
        age = access_control.SESSION_MAX_AGE if max_age is None else max_age
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
        return f"{access_control.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"

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
        assets = {
            "/catalog.css": "text/css; charset=utf-8",
            "/catalog.js": "text/javascript; charset=utf-8",
            "/catalog-ui.js": "text/javascript; charset=utf-8",
            "/admin.css": "text/css; charset=utf-8",
            "/admin.js": "text/javascript; charset=utf-8",
        }
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


        # Tela de Login
        if parsed.path in ("/login", "/login.html"):
            user = self._current_user()
            if user:
                qs = parse_qs(parsed.query)
                nxt = (qs.get("next") or [""])[0]
                if nxt == "/admin" and user.get("role") == "master":
                    self._send_redirect("/admin")
                else:
                    self._send_redirect("/")
                return
            self._send_html(LOGIN_HTML)
            return

        if parsed.path == "/api/auth/me":
            user = self._current_user()
            if not user:
                self._send_json({"authenticated": False, "user": None})
                return
            self._send_json({"authenticated": True, "user": access_control.sanitize_account(user)})
            return

        # Buscador de Preços (protegido por login)
        if parsed.path in ("/", "/index.html", "/buscador"):
            user = self._current_user()
            if not user:
                self._send_redirect("/login")
                return
            self._send_html(BUSCADOR_HTML)
            return

        # Painel Administrativo & Gestão de Contas (EXCLUSIVO Master `cledson`)
        if parsed.path in ("/admin", "/painel"):
            user = self._current_user()
            if not user:
                self._send_redirect("/login?next=/admin")
                return
            if user.get("role") != "master":
                self._send_redirect("/")
                return
            self._send_html(ADMIN_HTML)
            return

        if parsed.path == "/api/products":
            user = self._current_user()
            is_local = self._is_direct_local_request()
            if not user and not is_local:
                self._send_json({"ok": False, "error": "Faça login para acessar o catálogo."}, status=401)
                return
            etag, raw_bytes, gz_bytes = _get_products_http_payload(user)
            inm = (self.headers.get("If-None-Match") or "").strip()
            if inm and inm == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Vary", "Accept-Encoding, Cookie")
                self.end_headers()
                return
            accept_enc = (self.headers.get("Accept-Encoding") or "").lower()
            use_gzip = "gzip" in accept_enc
            body = gz_bytes if use_gzip else raw_bytes
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding, Cookie")
            self.send_header("X-Content-Type-Options", "nosniff")
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return

        if parsed.path == "/api/accounts":
            user = self._current_user()
            if not (user and user.get("role") == "master") and not self._is_direct_local_request():
                self._send_json({"ok": False, "error": "Acesso restrito ao usuário master."}, status=403)
                return
            self._send_json({"ok": True, "accounts": access_control.list_accounts(), "db": access_control.db_info()})
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
                    "lastCoverageAudit": last_coverage_audit,
                }, cors=True)
                return

        if parsed.path == "/api/panel-state":
            user = self._current_user()
            if not (user and user.get("role") == "master") and not self._is_direct_local_request():
                self._send_json({"ok": False, "error": "Acesso restrito ao usuário master."}, status=403)
                return
            try:
                wa = bridge_get("/api/status?lite=1")
                wa_error = None
            except (OSError, ValueError, TimeoutError) as exc:
                wa = {"status": "PAUSADO" if automation_paused() else "CONNECTING"}
                wa_error = f"{type(exc).__name__}: {exc}"
            groups, groups_checked_at, groups_error = panel_groups_snapshot() if wa.get("status") == "CONNECTED" else ([], None, wa_error or "WhatsApp desconectado.")
            self._send_json({
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "whatsapp": wa,
                "whatsappError": wa_error,
                "productsTodayCount": len(load_produtos()),
                "groups": groups,
                "groupsCheckedAt": groups_checked_at,
                "groupsError": groups_error,
                "coverageAudit": last_coverage_audit,
                "robots": robot_dashboard_state(),
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

        # Rotas de autenticação (abertas)
        if parsed.path == "/api/auth/login":
            body = self._read_body_json()
            ok, err, acc, token = access_control.authenticate(body.get("username", ""), body.get("password", ""))
            if not ok or not acc or not token:
                self._send_json({"ok": False, "error": err or "Credenciais inválidas."}, status=401)
                return
            nxt = str(body.get("next") or "").strip()
            redirect_to = "/admin" if (nxt == "/admin" and acc.get("role") == "master") else "/"
            self._send_json(
                {"ok": True, "user": access_control.sanitize_account(acc), "redirect": redirect_to},
                set_cookie=self._build_session_cookie(token),
            )
            return

        if parsed.path == "/api/auth/logout":
            self._send_json({"ok": True}, set_cookie=self._build_session_cookie("", max_age=0))
            return

        if parsed.path.startswith("/api/internal/"):
            if not self._is_direct_local_request():
                self._send_json({"error": "Acesso interno"}, status=403)
                return
        else:
            # Todas as demais rotas POST são administrativas (exclusivas do Master `cledson`)
            user = self._current_user()
            if not (user and user.get("role") == "master") and not self._is_direct_local_request():
                self._send_json({"ok": False, "error": "Acesso restrito ao usuário master."}, status=403)
                return

        if parsed.path == "/api/accounts/create":
            body = self._read_body_json()
            try:
                acc = access_control.create_account(
                    body.get("username", ""),
                    body.get("password", ""),
                    name=body.get("name", ""),
                    expires_at=body.get("expires_at"),
                )
                add_panel_log(f"[CONTAS] Novo login criado: {acc['username']} (vencimento: {acc['expires_at'] or 'sem limite'})")
                self._send_json({"ok": True, "account": acc})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if parsed.path == "/api/accounts/pause":
            body = self._read_body_json()
            try:
                acc = access_control.set_account_active(body.get("username", ""), bool(body.get("active", False)))
                add_panel_log(f"[CONTAS] Login {acc['username']} {'ativado' if acc['active'] else 'pausado'}.")
                self._send_json({"ok": True, "account": acc})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if parsed.path == "/api/accounts/password":
            body = self._read_body_json()
            try:
                acc = access_control.change_account_password(body.get("username", ""), body.get("password", ""))
                add_panel_log(f"[CONTAS] Senha alterada para: {acc['username']}.")
                self._send_json({"ok": True, "account": acc})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if parsed.path == "/api/accounts/expiry":
            body = self._read_body_json()
            try:
                acc = access_control.update_account_expiry(body.get("username", ""), body.get("expires_at"))
                add_panel_log(f"[CONTAS] Vencimento atualizado para {acc['username']}: {acc['expires_at'] or 'sem limite'}.")
                self._send_json({"ok": True, "account": acc})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if parsed.path == "/api/accounts/delete":
            body = self._read_body_json()
            try:
                uname = access_control.normalize_username(body.get("username", ""))
                access_control.delete_account(uname)
                add_panel_log(f"[CONTAS] Login removido: {uname}.")
                self._send_json({"ok": True})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if parsed.path == "/api/coverage-audit":
            report = perform_coverage_audit(allow_recovery=True)
            self._send_json({"ok": True, "coverage": report})
            return

        if automation_paused() and parsed.path == "/api/join-community":
            self._send_json({"ok": False, "error": "Automações pausadas pela restrição da conta. Apenas configurações e consultas estão disponíveis."}, status=423)
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
                "messageId": body.get("messageId"), "editKey": body.get("editKey"), "imageBase64": image_base64,
                "hasImage": bool(image_base64 or body.get("hasImage")),
                "mediaIssue": body.get("mediaIssue")
            }
            identity = daily_review.save_message(BASE_DIR, message)
            if identity is None:
                self._send_json({"ok": True, "parsedCount": 0, "ignoredSuperseded": True})
                return
            if message["hasImage"]:
                items = daily_review.media_offers(BASE_DIR, identity, message, offer_key)
            else:
                items = parse_supplier_message(text, sup_name, sup_phone, group_name or push_name,
                                               msg_timestamp=msg_ts, include_non_new=True)
                for item in items:
                    item["fonteCaptura"] = "lista"
            daily_review.attach_message_source(items, message)
            saved = apply_supplier_message(items, message)
            if items:
                add_panel_log(f"[PRODUTO] {saved} produtos capturados automaticamente de {sup_name} ({group_name or 'WhatsApp'})")
                self._send_json({"ok": True, "parsedCount": saved, "supplierName": sup_name})
            else:
                self._send_json({"ok": True, "parsedCount": 0})
            return

        # Endpoint para importar/testar lista manualmente pelo /admin
        if parsed.path == "/api/logout-wa":
            try:
                bridge_post("/api/logout", {})
            except Exception:
                pass
            add_panel_log("[CONEXAO] Sessão do WhatsApp desconectada. Aguarde o novo QR Code.")
            self._send_json({"ok": True})
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
ROBOT_INTERVALS = {
    "colors": "3 min",
    "price": "5 min",
    "catalog": "5 min",
    "daily": "10 min",
    "coverage": "10 min",
}
ROBOT_INTERVAL_SECONDS = {
    "colors": 180,
    "price": 300,
    "catalog": 300,
    "daily": 600,
    "coverage": 600,
}
_robot_health_lock = threading.Lock()
_robot_health = {}


def record_robot_start(name):
    with _robot_health_lock:
        state = dict(_robot_health.get(name, {}))
        now = time.time()
        state.update(
            inProgress=True,
            startedAt=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            startedAtEpoch=now,
        )
        if not state.get("lastError"):
            state["status"] = "EXECUTANDO"
        _robot_health[name] = state


def record_robot_health(name, report=None, error=None):
    with _robot_health_lock:
        state = dict(_robot_health.get(name, {}))
        now = time.time()
        state.update(
            lastRun=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            lastRunEpoch=now,
            inProgress=False,
        )
        state["lastError"] = f"{type(error).__name__}: {error}" if error else None
        if error:
            state["status"] = "ERRO"
        elif report and report.get("status") in ("ALERTA", "CRITICO"):
            state["status"] = report["status"]
        elif report and report.get("issues"):
            state["status"] = "ALERTA"
        else:
            state["status"] = "OK"
        _robot_health[name] = state


def robot_dashboard_state():
    with _robot_health_lock:
        health = {key: dict(value) for key, value in _robot_health.items()}

    definitions = {
        "colors": ("Guardião de cores + IA", last_color_audit, "analyzed", "validAfter"),
        "price": ("Vistoria de preços e catálogo", last_guardian_sweep, "analyzed", "validAfter"),
        "catalog": ("Monitor de integridade do catálogo", catalog_monitor, "offers", None),
        "daily": ("Revisão e releitura de listas", {}, None, None),
        "coverage": ("Auditor de comunidades e cobertura", last_coverage_audit, "monitoredTotal", None),
    }
    result = {}
    for key, (label, report, analyzed_key, valid_key) in definitions.items():
        state = health.get(key, {})
        status = state.get("status")
        if not status:
            if not report.get("checked"):
                status = "AGUARDANDO"
            elif report.get("status") in ("ALERTA", "CRITICO"):
                status = report["status"]
            elif report.get("issues"):
                status = "ALERTA"
            else:
                status = "OK"
        if state.get("inProgress"):
            status = "ATRASADO" if time.time() - state.get("startedAtEpoch", time.time()) > ROBOT_INTERVAL_SECONDS[key] * 2 else "EXECUTANDO"
        elif state.get("lastRunEpoch") and time.time() - state["lastRunEpoch"] > ROBOT_INTERVAL_SECONDS[key] * 2 and status != "ERRO":
            status = "ATRASADO"
        if key == "catalog":
            issues = report.get("issues") or {}
            detail = ", ".join(f"{name}: {count}" for name, count in issues.items()) if issues else "Sem divergências"
        elif key == "coverage":
            issues = report.get("issues") or []
            detail = " · ".join(issues) if issues else f"{report.get('monitoredTotal', 0)} canais monitorados; 0 alertas"
        elif analyzed_key:
            detail = f"{report.get(analyzed_key, 0)} verificados"
            if valid_key:
                detail += f" · {report.get(valid_key, 0)} válidos"
            if report.get("removed"):
                detail += f" · {report['removed']} removidos"
            if report.get("corrected"):
                detail += f" · {report['corrected']} corrigidos"
        else:
            detail = "Execução concluída" if state.get("lastRun") else "Aguardando primeira execução"
        result[key] = {
            "label": label,
            "intervalLabel": ROBOT_INTERVALS[key],
            "status": status,
            "lastRun": state.get("lastRun") or report.get("checked"),
            "startedAt": state.get("startedAt"),
            "lastError": state.get("lastError"),
            "detail": detail,
        }
    return result


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
                             (previous["hora"] == row["hora"] and previous["preco"] == row["preco"] and
                              previous.get("mensagemOrigem") == row.get("mensagemOrigem"))):
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
        record_robot_start("daily")
        try:
            if not migrated:
                daily_review.import_previous_log(BASE_DIR)
                migrated = True
            report = daily_review.run(BASE_DIR, offer_key, apply_reviewed_offers, pace=0.03)
            if report is not None:
                atomic_json_write(os.path.join(BASE_DIR, "revisao_listas", "ultima_revisao.json"), report)
            record_robot_health("daily", report=report or {})
        except Exception as exc:
            record_robot_health("daily", error=exc)
            print(f"[RELEITURA] {type(exc).__name__}: {exc}", flush=True)
        time.sleep(600)


def catalog_monitor_worker():
    global catalog_monitor
    while True:
        record_robot_start("catalog")
        run_error = None
        try:
            rows = load_produtos(only_today=False)
            today = get_today_str()
            report = audit_catalog([r for r in rows if r.get("data") == today and
                                    str(r.get("hora", "")) >= "06:00:00"])
        except Exception as exc:
            run_error = exc
            report = {"offers": None, "issues": {"Verificação interrompida": 1},
                      "examples": [f"Falha ao verificar o catálogo ({type(exc).__name__})"]}
        report["checked"] = time.strftime("%d/%m/%Y %H:%M:%S")
        if report["issues"] != catalog_monitor["issues"]:
            print(f"[MONITOR] {report['issues'] or 'Nenhuma pendência detectada'}")
        catalog_monitor = report
        record_robot_health("catalog", report=report, error=run_error)
        try:
            atomic_json_write(os.path.join(BASE_DIR, "monitor_catalogo_interno.json"), report)
        except OSError as exc:
            print(f"[MONITOR] Falha ao gravar relatório interno: {type(exc).__name__}", flush=True)
        time.sleep(300)


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
_guardian_quarantine = {"date": "", "sigs": set()}


def _guardian_offer_sig(item):
    try:
        price_val = int(round(float(item.get("preco", 0))))
    except (TypeError, ValueError):
        price_val = 0
    return "|".join([
        str(item.get("data", "")).strip(),
        str(item.get("fornecedor", "")).strip().upper(),
        str(item.get("modelo", "")).strip().upper(),
        str(item.get("capacidade", "-")).strip().upper(),
        str(item.get("cor", "-")).strip().upper(),
        str(price_val),
    ])


def _load_guardian_quarantine():
    hoje = get_today_str()
    if _guardian_quarantine["date"] == hoje:
        return _guardian_quarantine["sigs"]
    qfile = os.path.join(BASE_DIR, "quarentena_guardiao.json")
    sigs = set()
    if os.path.exists(qfile):
        try:
            with open(qfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("date") == hoje and isinstance(data.get("sigs"), list):
                sigs = set(data["sigs"])
        except Exception:
            pass
    _guardian_quarantine["date"] = hoje
    _guardian_quarantine["sigs"] = sigs
    return sigs


def _record_guardian_quarantine(removed_items):
    if not removed_items:
        return
    hoje = get_today_str()
    sigs = _load_guardian_quarantine()
    added = False
    for it in removed_items:
        sig = _guardian_offer_sig(it)
        if sig not in sigs:
            sigs.add(sig)
            added = True
    if added:
        try:
            atomic_json_write(
                os.path.join(BASE_DIR, "quarentena_guardiao.json"),
                {"date": hoje, "sigs": sorted(sigs)},
            )
        except OSError:
            pass


def is_guardian_quarantined(item):
    if not item or item.get("data") != get_today_str():
        return False
    return _guardian_offer_sig(item) in _load_guardian_quarantine()


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
            record_robot_health("price", report=report)
            return report

        healed, report = run_guardian_sweep(atuais, offer_key_fn=offer_key, log_anomalies=log_anomalies)
        if report.get("removedItems"):
            _record_guardian_quarantine(report["removedItems"])
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
        record_robot_health("price", report=report)
        try:
            atomic_json_write(os.path.join(BASE_DIR, "ultima_vistoria_5min.json"), report)
        except OSError as exc:
            print(f"[VISTORIA 5MIN] Falha ao gravar relatório: {type(exc).__name__}", flush=True)
        return report


def guardian_sweep_worker():
    """Robô autônomo que realiza vistoria e autocorreção completa a cada 5 minutos."""
    time.sleep(15)
    while True:
        record_robot_start("price")
        try:
            perform_guardian_sweep(log_anomalies=True)
        except Exception as exc:
            record_robot_health("price", error=exc)
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
            record_robot_health("colors", report=report)
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
        record_robot_health("colors", report=report)
        try:
            atomic_json_write(color_guardian.COLOR_AUDIT_REPORT_FILE, report)
        except OSError as exc:
            print(f"[ROBÔ DE CORES] Falha ao gravar relatório: {type(exc).__name__}", flush=True)
        return report


def color_guardian_worker():
    """Robô autônomo dedicado a auditar e aprender cores oficiais a cada 3 minutos."""
    time.sleep(8)
    while True:
        record_robot_start("colors")
        try:
            perform_color_guardian_audit(log_anomalies=True, allow_learn=True)
        except Exception as exc:
            record_robot_health("colors", error=exc)
            print(f"[ROBÔ DE CORES] Erro na auditoria periódica: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(COLOR_AUDIT_INTERVAL_SECONDS)


COVERAGE_AUDIT_INTERVAL_SECONDS = 600  # 10 minutos (super leve)
last_coverage_audit = {"checked": None, "status": "AGUARDANDO", "monitoredTotal": 0, "issues": []}


def perform_coverage_audit(allow_recovery=True):
    """Robô autônomo que confere se TODAS as comunidades, canais e contatos estão sendo lidos."""
    global last_coverage_audit
    report = coverage_guardian.run_coverage_audit(
        base_dir=BASE_DIR,
        bridge_get_fn=lambda p: bridge_get(p, timeout=25 if "groups" in p else 8),
        bridge_post_fn=bridge_post,
        produtos_today=load_produtos(),
        fornecedores=load_fornecedores(),
        allow_recovery=allow_recovery,
    )
    last_coverage_audit = report
    record_robot_health("coverage", report=report)
    try:
        atomic_json_write(coverage_guardian.COVERAGE_REPORT_FILE, report)
    except OSError as exc:
        print(f"[ROBÔ COBERTURA] Falha ao gravar relatório: {type(exc).__name__}", flush=True)
    if report.get("issues"):
        rec_tag = " · recuperação automática acionada" if report.get("recoveryTriggered") else ""
        add_panel_log(
            f"[ROBÔ COBERTURA] ⚠️ {' | '.join(report['issues'])} "
            f"({report['monitoredTotal']} canais · {report['activeSuppliersToday']} fornecedores hoje){rec_tag}"
        )
    else:
        add_panel_log(
            f"[ROBÔ COBERTURA] ✅ {report['monitoredTotal']} canais/comunidades OK · "
            f"0 fora do radar · {report['activeSuppliersToday']} fornecedores ativos hoje."
        )
    return report


def coverage_guardian_worker():
    """Robô autônomo dedicado a garantir leitura de 100% das comunidades, canais e contatos (a cada 10 min)."""
    time.sleep(25)
    while True:
        record_robot_start("coverage")
        try:
            perform_coverage_audit(allow_recovery=True)
        except Exception as exc:
            record_robot_health("coverage", error=exc)
            print(f"[ROBÔ COBERTURA] Erro na auditoria periódica: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(COVERAGE_AUDIT_INTERVAL_SECONDS)


def main():
    access_control.load_accounts()
    reset_thread.start()
    cleanup_old_products_on_start()
    threading.Thread(target=catalog_monitor_worker, daemon=True).start()
    threading.Thread(target=guardian_sweep_worker, daemon=True).start()
    threading.Thread(target=color_guardian_worker, daemon=True).start()
    threading.Thread(target=coverage_guardian_worker, daemon=True).start()
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
