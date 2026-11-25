"""
Gestão de contas, autenticação e regra de DFU por usuário do BuscaBusca.

Regras de negócio:
- Usuário Master (`cledson`): acesso total ao /admin (criar, remover, pausar, alterar senha e vencimento de contas),
  SEM DFU no buscador (preço puro de lista).
- Usuário exclusivo com DFU (`admin`): único login do sistema com DFU ativo no buscador (invisível na tela),
  SEM acesso ao /admin.
- Demais contas vendidas (criadas pelo Master): sempre SEM DFU, acesso apenas ao buscador, com bloqueio automático
  na data de vencimento (sem limitar telas simultâneas).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "usuarios_acesso.json")
SESSION_SECRET_FILE = os.path.join(BASE_DIR, "session_secret.txt")
COOKIE_NAME = "bb_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 dias

MASTER_USERNAME = "cledson"
EXCLUSIVE_DFU_USERNAME = "admin"

_accounts_lock = threading.RLock()
_secret_cache = None


def _atomic_write_json(path, payload):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".acc-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _get_session_secret():
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    env_sec = os.environ.get("BUSCABUSCA_SESSION_SECRET", "").strip()
    if env_sec:
        _secret_cache = env_sec
        return _secret_cache
    with _accounts_lock:
        if os.path.exists(SESSION_SECRET_FILE):
            try:
                val = open(SESSION_SECRET_FILE, "r", encoding="utf-8").read().strip()
                if len(val) >= 32:
                    _secret_cache = val
                    return _secret_cache
            except OSError:
                pass
        val = secrets.token_hex(32)
        try:
            fd, tmp = tempfile.mkstemp(prefix=".sec-", suffix=".tmp", dir=os.path.dirname(SESSION_SECRET_FILE) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(val + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, SESSION_SECRET_FILE)
        except OSError:
            pass
        _secret_cache = val
        return _secret_cache


def hash_password(password):
    pw = str(password or "")
    if not pw:
        raise ValueError("A senha não pode ser vazia.")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000, dklen=32)
    return f"pbkdf2_sha256${salt.hex()}${dk.hex()}"


def verify_password(password, stored_hash):
    try:
        scheme, salt_hex, hash_hex = str(stored_hash or "").split("$", 2)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        if scheme == "pbkdf2_sha256":
            actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, 200_000, dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        if scheme == "scrypt" and hasattr(hashlib, "scrypt"):
            actual = hashlib.scrypt(str(password or "").encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        return False
    except Exception:
        return False


def normalize_username(username):
    u = re.sub(r"\s+", "", str(username or "").strip().lower())
    return u


def _validate_username(username):
    u = normalize_username(username)
    if not u or len(u) < 2 or len(u) > 40 or not re.fullmatch(r"[a-z0-9._-]+", u):
        raise ValueError("Usuário inválido (use 2 a 40 caracteres: letras, números, ponto, traço ou underline).")
    return u


def _validate_expiry(expires_at):
    raw = str(expires_at or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError("Data de vencimento inválida (use o formato AAAA-MM-DD).")
    datetime.strptime(raw, "%Y-%m-%d")
    return raw


def _today_iso():
    return time.strftime("%Y-%m-%d")


def is_expired(account):
    exp = account.get("expires_at")
    if not exp:
        return False
    return _today_iso() > str(exp)


def account_status(account):
    if not account.get("active", True):
        return "pausado"
    if is_expired(account):
        return "vencido"
    return "ativo"


def _seed_defaults():
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        MASTER_USERNAME: {
            "username": MASTER_USERNAME,
            "name": "Cledson (Master)",
            "role": "master",
            "dfu_enabled": False,
            "active": True,
            "expires_at": None,
            "password_hash": hash_password("cledson"),
            "password_version": 1,
            "created_at": now,
            "last_login_at": None,
        },
        EXCLUSIVE_DFU_USERNAME: {
            "username": EXCLUSIVE_DFU_USERNAME,
            "name": "Admin",
            "role": "user",
            "dfu_enabled": True,
            "active": True,
            "expires_at": None,
            "password_hash": hash_password("admin"),
            "password_version": 1,
            "created_at": now,
            "last_login_at": None,
        },
    }


def _enforce_invariants(accounts):
    """Garante: master sempre ativo/sem DFU, e SOMENTE `admin` com DFU ativo."""
    changed = False
    defaults = None
    if MASTER_USERNAME not in accounts:
        defaults = defaults or _seed_defaults()
        accounts[MASTER_USERNAME] = defaults[MASTER_USERNAME]
        changed = True
    if EXCLUSIVE_DFU_USERNAME not in accounts:
        defaults = defaults or _seed_defaults()
        accounts[EXCLUSIVE_DFU_USERNAME] = defaults[EXCLUSIVE_DFU_USERNAME]
        changed = True

    for uname, acc in list(accounts.items()):
        if uname == MASTER_USERNAME:
            if acc.get("role") != "master" or acc.get("dfu_enabled") is not False or acc.get("active") is not True:
                acc["role"] = "master"
                acc["dfu_enabled"] = False
                acc["active"] = True
                acc["expires_at"] = None
                changed = True
        elif uname == EXCLUSIVE_DFU_USERNAME:
            if acc.get("role") != "user" or acc.get("dfu_enabled") is not True:
                acc["role"] = "user"
                acc["dfu_enabled"] = True
                changed = True
        else:
            if acc.get("role") != "user" or acc.get("dfu_enabled") is not False:
                acc["role"] = "user"
                acc["dfu_enabled"] = False
                changed = True
    return changed


def load_accounts():
    with _accounts_lock:
        if not os.path.exists(ACCOUNTS_FILE):
            accounts = _seed_defaults()
            _atomic_write_json(ACCOUNTS_FILE, accounts)
            return accounts
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Formato inválido")
        except Exception:
            data = _seed_defaults()
            _atomic_write_json(ACCOUNTS_FILE, data)
            return data
        if _enforce_invariants(data):
            _atomic_write_json(ACCOUNTS_FILE, data)
        return data


def _save_accounts(accounts):
    with _accounts_lock:
        _enforce_invariants(accounts)
        _atomic_write_json(ACCOUNTS_FILE, accounts)


def sanitize_account(acc):
    return {
        "username": acc.get("username", ""),
        "name": acc.get("name") or acc.get("username", ""),
        "role": acc.get("role", "user"),
        "dfu_enabled": bool(acc.get("dfu_enabled", False)),
        "active": bool(acc.get("active", True)),
        "status": account_status(acc),
        "expires_at": acc.get("expires_at"),
        "created_at": acc.get("created_at"),
        "last_login_at": acc.get("last_login_at"),
        "is_master": acc.get("username") == MASTER_USERNAME,
    }


def list_accounts():
    accounts = load_accounts()
    order = lambda a: (0 if a["username"] == MASTER_USERNAME else (1 if a["username"] == EXCLUSIVE_DFU_USERNAME else 2), a["username"])
    return [sanitize_account(a) for a in sorted(accounts.values(), key=order)]


def create_account(username, password, name="", expires_at=None):
    u = _validate_username(username)
    if len(str(password or "")) < 3:
        raise ValueError("A senha precisa ter pelo menos 3 caracteres.")
    exp = _validate_expiry(expires_at)
    with _accounts_lock:
        accounts = load_accounts()
        if u in accounts:
            raise ValueError(f"O login '{u}' já existe.")
        accounts[u] = {
            "username": u,
            "name": str(name or "").strip() or u,
            "role": "user",
            "dfu_enabled": False,
            "active": True,
            "expires_at": exp,
            "password_hash": hash_password(password),
            "password_version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_login_at": None,
        }
        _save_accounts(accounts)
        return sanitize_account(accounts[u])


def set_account_active(username, active):
    u = normalize_username(username)
    with _accounts_lock:
        accounts = load_accounts()
        if u not in accounts:
            raise ValueError("Conta não encontrada.")
        if u == MASTER_USERNAME and not active:
            raise ValueError("Não é possível pausar o usuário master.")
        accounts[u]["active"] = bool(active)
        _save_accounts(accounts)
        return sanitize_account(accounts[u])


def change_account_password(username, new_password):
    u = normalize_username(username)
    if len(str(new_password or "")) < 3:
        raise ValueError("A nova senha precisa ter pelo menos 3 caracteres.")
    with _accounts_lock:
        accounts = load_accounts()
        if u not in accounts:
            raise ValueError("Conta não encontrada.")
        accounts[u]["password_hash"] = hash_password(new_password)
        accounts[u]["password_version"] = int(accounts[u].get("password_version", 1)) + 1
        _save_accounts(accounts)
        return sanitize_account(accounts[u])


def update_account_expiry(username, expires_at):
    u = normalize_username(username)
    exp = _validate_expiry(expires_at)
    with _accounts_lock:
        accounts = load_accounts()
        if u not in accounts:
            raise ValueError("Conta não encontrada.")
        if u == MASTER_USERNAME:
            raise ValueError("O usuário master não tem data de vencimento.")
        accounts[u]["expires_at"] = exp
        _save_accounts(accounts)
        return sanitize_account(accounts[u])


def delete_account(username):
    u = normalize_username(username)
    with _accounts_lock:
        accounts = load_accounts()
        if u not in accounts:
            raise ValueError("Conta não encontrada.")
        if u == MASTER_USERNAME:
            raise ValueError("Não é possível remover o usuário master.")
        if u == EXCLUSIVE_DFU_USERNAME:
            raise ValueError("Não é possível remover a conta exclusiva com DFU ('admin'). Você pode pausá-la ou alterar a senha.")
        del accounts[u]
        _save_accounts(accounts)
        return True


def create_session_token(account):
    payload = f"{account['username']}|{int(account.get('password_version', 1))}|{int(time.time())}"
    sig = hmac.new(_get_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_session_token(token):
    """Valida assinatura HMAC + estado em tempo real da conta (ativa, não vencida, mesma versão de senha)."""
    raw = str(token or "").strip()
    if not raw or raw.count("|") != 3:
        return None
    username, pv_str, issued_str, sig = raw.split("|", 3)
    payload = f"{username}|{pv_str}|{issued_str}"
    expected = hmac.new(_get_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        issued = int(issued_str)
        pv = int(pv_str)
    except ValueError:
        return None
    if time.time() - issued > SESSION_MAX_AGE:
        return None
    accounts = load_accounts()
    acc = accounts.get(username)
    if not acc:
        return None
    if int(acc.get("password_version", 1)) != pv:
        return None
    if not acc.get("active", True):
        return None
    if is_expired(acc):
        return None
    return acc


def authenticate(username, password):
    """Retorna (ok, error_message, account_or_none, token_or_none)."""
    u = normalize_username(username)
    with _accounts_lock:
        accounts = load_accounts()
        acc = accounts.get(u)
        if not acc or not verify_password(password, acc.get("password_hash")):
            return False, "Usuário ou senha incorretos.", None, None
        if not acc.get("active", True):
            return False, "Esta conta está pausada. Fale com o administrador.", None, None
        if is_expired(acc):
            return False, f"Acesso vencido em {acc.get('expires_at')}. Fale com o administrador para renovar.", None, None
        acc["last_login_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_accounts(accounts)
        token = create_session_token(acc)
        return True, "", acc, token


def extract_cookie_token(cookie_header):
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip() == COOKIE_NAME:
            return v.strip()
    return ""


def apply_user_pricing(products, dfu_enabled, compute_dfu_fn):
    """Aplica DFU silenciosamente SOMENTE se dfu_enabled=True (único usuário `admin`); demais recebem preço puro."""
    out = []
    strip_keys = ("alertaPreco", "alertaFaixaModelo", "observacao", "revisao", "contextoOriginal")
    for p in products:
        clean = {k: v for k, v in p.items() if k not in strip_keys}
        if dfu_enabled:
            try:
                base = float(clean.get("preco") or 0)
                if base > 0:
                    clean["preco"] = round(base + compute_dfu_fn(clean), 2)
            except (TypeError, ValueError):
                pass
        out.append(clean)
    return out
