"""
Gestão de contas com banco de dados SQLite persistente e backup automático — O Fornecedor.

Armazenamento isolado do diretório de código (na VPS: /var/lib/ofornecedor-db/accounts.db):
- NUNCA é apagado ou sobrescrito em deploys, atualizações git ou reinícios.
- Cada alteração (criar, pausar, trocar senha, alterar vencimento, remover) grava em SQLite (WAL + FULL sync),
  registra no histórico `account_audit_log` e gera backup automático em `<db_dir>/backups/`.

Regras de negócio:
- Usuário Master (`cledson`): acesso total ao /admin, SEM DFU no buscador.
- Usuário exclusivo com DFU (`admin`): único login com DFU ativo no buscador, SEM acesso ao /admin.
- Demais contas vendidas: sempre SEM DFU, acesso apenas ao buscador, bloqueio automático no vencimento.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_ACCOUNTS_FILE = os.path.join(BASE_DIR, "usuarios_acesso.json")
LEGACY_SECRET_FILE = os.path.join(BASE_DIR, "session_secret.txt")
VPS_DEFAULT_DB_DIR = "/var/lib/ofornecedor-db"
COOKIE_NAME = "bb_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 dias
MAX_BACKUPS = 30

MASTER_USERNAME = "cledson"
EXCLUSIVE_DFU_USERNAME = "admin"

_db_lock = threading.RLock()
_secret_cache = None


def resolve_db_dir():
    """Retorna a pasta persistente do banco de contas (isolada do código na VPS)."""
    env_dir = os.environ.get("OFORNECEDOR_DB_DIR", "").strip()
    if env_dir:
        return env_dir
    if os.path.isdir(VPS_DEFAULT_DB_DIR) and os.access(VPS_DEFAULT_DB_DIR, os.W_OK):
        return VPS_DEFAULT_DB_DIR
    return os.path.join(BASE_DIR, "data_contas")


def get_db_path():
    return os.path.join(resolve_db_dir(), "accounts.db")


def get_secret_path():
    return os.path.join(resolve_db_dir(), "session_secret.txt")


def get_backups_dir():
    return os.path.join(resolve_db_dir(), "backups")


def _ensure_db_dir():
    d = resolve_db_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.makedirs(get_backups_dir(), mode=0o700, exist_ok=True)
    return d


def _get_session_secret():
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    env_sec = os.environ.get("BUSCABUSCA_SESSION_SECRET", "").strip()
    if env_sec:
        _secret_cache = env_sec
        return _secret_cache
    with _db_lock:
        _ensure_db_dir()
        sec_path = get_secret_path()
        # Migra segredo legado se existir para não derrubar sessões ativas
        if not os.path.exists(sec_path) and os.path.exists(LEGACY_SECRET_FILE):
            try:
                shutil.copy2(LEGACY_SECRET_FILE, sec_path)
                os.chmod(sec_path, 0o600)
            except OSError:
                pass
        if os.path.exists(sec_path):
            try:
                val = open(sec_path, "r", encoding="utf-8").read().strip()
                if len(val) >= 32:
                    _secret_cache = val
                    return _secret_cache
            except OSError:
                pass
        val = secrets.token_hex(32)
        try:
            fd, tmp = tempfile.mkstemp(prefix=".sec-", suffix=".tmp", dir=resolve_db_dir())
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(val + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, sec_path)
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
    return re.sub(r"\s+", "", str(username or "").strip().lower())


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


def _connect():
    _ensure_db_dir()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _row_to_dict(row):
    return {
        "username": row["username"],
        "name": row["name"],
        "role": row["role"],
        "dfu_enabled": bool(row["dfu_enabled"]),
        "active": bool(row["active"]),
        "expires_at": row["expires_at"],
        "password_hash": row["password_hash"],
        "password_version": int(row["password_version"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
    }


def _create_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            username TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            dfu_enabled INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT,
            password_hash TEXT NOT NULL,
            password_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            username TEXT NOT NULL,
            details TEXT
        )
    """)


def _log_audit(conn, action, username, details=""):
    conn.execute(
        "INSERT INTO account_audit_log (timestamp, action, username, details) VALUES (?, ?, ?, ?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), action, username, details),
    )


def _snapshot_backup(conn):
    """Salva backup JSON seguro em <db_dir>/backups/ a cada alteração de conta (mantém últimos 30)."""
    try:
        rows = [_row_to_dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY username").fetchall()]
        bdir = get_backups_dir()
        os.makedirs(bdir, mode=0o700, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = os.path.join(bdir, f"accounts_{stamp}.json")
        fd, tmp = tempfile.mkstemp(prefix=".bak-", suffix=".tmp", dir=bdir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"backedUpAt": time.strftime("%Y-%m-%d %H:%M:%S"), "accounts": rows}, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        # Mantém apenas os últimos MAX_BACKUPS arquivos
        files = sorted(fn for fn in os.listdir(bdir) if fn.startswith("accounts_") and fn.endswith(".json"))
        for old in files[:-MAX_BACKUPS]:
            try:
                os.remove(os.path.join(bdir, old))
            except OSError:
                pass
    except OSError:
        pass


def _migrate_legacy_json_if_needed(conn):
    """Importa automaticamente contas de usuarios_acesso.json legado sem sobrescrever quem já está no SQLite."""
    if not os.path.exists(LEGACY_ACCOUNTS_FILE):
        return
    try:
        with open(LEGACY_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            legacy = json.load(f)
        if not isinstance(legacy, dict):
            return
    except Exception:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    imported = 0
    for uname, acc in legacy.items():
        if not isinstance(acc, dict) or not acc.get("password_hash"):
            continue
        u = normalize_username(uname)
        if not u:
            continue
        role = "master" if u == MASTER_USERNAME else "user"
        dfu = 1 if u == EXCLUSIVE_DFU_USERNAME else 0
        active = 1 if (u == MASTER_USERNAME or acc.get("active", True)) else 0
        exp = None if u == MASTER_USERNAME else acc.get("expires_at")
        cur = conn.execute(
            """INSERT OR IGNORE INTO accounts
               (username, name, role, dfu_enabled, active, expires_at, password_hash, password_version, created_at, updated_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                u,
                str(acc.get("name") or u),
                role,
                dfu,
                active,
                exp,
                acc["password_hash"],
                int(acc.get("password_version", 1)),
                acc.get("created_at") or now,
                now,
                acc.get("last_login_at"),
            ),
        )
        if cur.rowcount:
            imported += 1
    if imported:
        _log_audit(conn, "migrate_legacy", "system", f"Importadas {imported} contas do JSON legado")


def _ensure_seed_and_invariants(conn):
    """Cria cledson/admin apenas se não existirem (NUNCA sobrescreve senhas alteradas) e garante invariantes."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # 1. Migra legado antes do seed (para preservar senhas/contas já existentes)
    _migrate_legacy_json_if_needed(conn)

    # 2. Insere contas padrão somente se ausentes (INSERT OR IGNORE -> nunca apaga nem reseta senha!)
    if not conn.execute("SELECT 1 FROM accounts WHERE username = ?", (MASTER_USERNAME,)).fetchone():
        conn.execute(
            """INSERT INTO accounts
               (username, name, role, dfu_enabled, active, expires_at, password_hash, password_version, created_at, updated_at, last_login_at)
               VALUES (?, ?, 'master', 0, 1, NULL, ?, 1, ?, ?, NULL)""",
            (MASTER_USERNAME, "Cledson (Master)", hash_password("cledson"), now, now),
        )
    if not conn.execute("SELECT 1 FROM accounts WHERE username = ?", (EXCLUSIVE_DFU_USERNAME,)).fetchone():
        conn.execute(
            """INSERT INTO accounts
               (username, name, role, dfu_enabled, active, expires_at, password_hash, password_version, created_at, updated_at, last_login_at)
               VALUES (?, ?, 'user', 1, 1, NULL, ?, 1, ?, ?, NULL)""",
            (EXCLUSIVE_DFU_USERNAME, "Admin", hash_password("admin"), now, now),
        )

    # 3. Garante invariantes estruturais sem nunca apagar clientes nem alterar senhas
    conn.execute(
        "UPDATE accounts SET role='master', dfu_enabled=0, active=1, expires_at=NULL WHERE username=?",
        (MASTER_USERNAME,),
    )
    conn.execute(
        "UPDATE accounts SET role='user', dfu_enabled=1 WHERE username=?",
        (EXCLUSIVE_DFU_USERNAME,),
    )
    conn.execute(
        "UPDATE accounts SET role='user', dfu_enabled=0 WHERE username NOT IN (?, ?)",
        (MASTER_USERNAME, EXCLUSIVE_DFU_USERNAME),
    )


def init_db():
    with _db_lock:
        conn = _connect()
        try:
            _create_schema(conn)
            _ensure_seed_and_invariants(conn)
            conn.commit()
            try:
                os.chmod(get_db_path(), 0o600)
            except OSError:
                pass
        finally:
            conn.close()


def load_accounts():
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            rows = conn.execute("SELECT * FROM accounts").fetchall()
            return {r["username"]: _row_to_dict(r) for r in rows}
        finally:
            conn.close()


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


def db_info():
    bdir = get_backups_dir()
    backups = 0
    if os.path.isdir(bdir):
        backups = sum(1 for fn in os.listdir(bdir) if fn.startswith("accounts_") and fn.endswith(".json"))
    return {
        "engine": "sqlite3",
        "path": get_db_path(),
        "isolated": not os.path.abspath(get_db_path()).startswith(os.path.abspath(BASE_DIR) + os.sep),
        "backupsCount": backups,
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
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            if conn.execute("SELECT 1 FROM accounts WHERE username = ?", (u,)).fetchone():
                raise ValueError(f"O login '{u}' já existe.")
            conn.execute(
                """INSERT INTO accounts
                   (username, name, role, dfu_enabled, active, expires_at, password_hash, password_version, created_at, updated_at, last_login_at)
                   VALUES (?, ?, 'user', 0, 1, ?, ?, 1, ?, ?, NULL)""",
                (u, str(name or "").strip() or u, exp, hash_password(password), now, now),
            )
            _log_audit(conn, "create", u, f"expires_at={exp or 'sem_limite'}")
            conn.commit()
            _snapshot_backup(conn)
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            return sanitize_account(_row_to_dict(row))
        finally:
            conn.close()


def set_account_active(username, active):
    u = normalize_username(username)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            if not row:
                raise ValueError("Conta não encontrada.")
            if u == MASTER_USERNAME and not active:
                raise ValueError("Não é possível pausar o usuário master.")
            conn.execute("UPDATE accounts SET active = ?, updated_at = ? WHERE username = ?", (1 if active else 0, now, u))
            _log_audit(conn, "activate" if active else "pause", u)
            conn.commit()
            _snapshot_backup(conn)
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            return sanitize_account(_row_to_dict(row))
        finally:
            conn.close()


def change_account_password(username, new_password):
    u = normalize_username(username)
    if len(str(new_password or "")) < 3:
        raise ValueError("A nova senha precisa ter pelo menos 3 caracteres.")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            if not row:
                raise ValueError("Conta não encontrada.")
            new_pv = int(row["password_version"]) + 1
            conn.execute(
                "UPDATE accounts SET password_hash = ?, password_version = ?, updated_at = ? WHERE username = ?",
                (hash_password(new_password), new_pv, now, u),
            )
            _log_audit(conn, "change_password", u, f"pv={new_pv}")
            conn.commit()
            _snapshot_backup(conn)
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            return sanitize_account(_row_to_dict(row))
        finally:
            conn.close()


def update_account_expiry(username, expires_at):
    u = normalize_username(username)
    exp = _validate_expiry(expires_at)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            if not row:
                raise ValueError("Conta não encontrada.")
            if u == MASTER_USERNAME:
                raise ValueError("O usuário master não tem data de vencimento.")
            conn.execute("UPDATE accounts SET expires_at = ?, updated_at = ? WHERE username = ?", (exp, now, u))
            _log_audit(conn, "update_expiry", u, f"expires_at={exp or 'sem_limite'}")
            conn.commit()
            _snapshot_backup(conn)
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            return sanitize_account(_row_to_dict(row))
        finally:
            conn.close()


def delete_account(username):
    u = normalize_username(username)
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            if not row:
                raise ValueError("Conta não encontrada.")
            if u == MASTER_USERNAME:
                raise ValueError("Não é possível remover o usuário master.")
            if u == EXCLUSIVE_DFU_USERNAME:
                raise ValueError("Não é possível remover a conta exclusiva com DFU ('admin'). Você pode pausá-la ou alterar a senha.")
            conn.execute("DELETE FROM accounts WHERE username = ?", (u,))
            _log_audit(conn, "delete", u)
            conn.commit()
            _snapshot_backup(conn)
            return True
        finally:
            conn.close()


def create_session_token(account):
    payload = f"{account['username']}|{int(account.get('password_version', 1))}|{int(time.time())}"
    sig = hmac.new(_get_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_session_token(token):
    """Valida assinatura HMAC + estado em tempo real da conta no SQLite (ativa, não vencida, mesma senha)."""
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
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
            if not row:
                return None
            acc = _row_to_dict(row)
        finally:
            conn.close()
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
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        init_db()
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
            if not row:
                return False, "Usuário ou senha incorretos.", None, None
            acc = _row_to_dict(row)
            if not verify_password(password, acc.get("password_hash")):
                return False, "Usuário ou senha incorretos.", None, None
            if not acc.get("active", True):
                return False, "Esta conta está pausada. Fale com o administrador.", None, None
            if is_expired(acc):
                return False, f"Acesso vencido em {acc.get('expires_at')}. Fale com o administrador para renovar.", None, None
            conn.execute("UPDATE accounts SET last_login_at = ? WHERE username = ?", (now, u))
            conn.commit()
            acc["last_login_at"] = now
            token = create_session_token(acc)
            return True, "", acc, token
        finally:
            conn.close()


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
