"""
Robô Auditor de Cobertura de Comunidades, Canais e Contatos (coverage_guardian.py).

Responsabilidades:
1. Conferir se TODOS os grupos, comunidades e canais em que o WhatsApp participa estão
   devidamente mapeados e sendo lidos (nenhum grupo/comunidade esquecido).
2. Cruzar leitura real de hoje (ofertas + mensagens brutas recebidas) contra:
   - canais/comunidades monitorados (arquivados_monitorados.json + /api/groups)
   - fornecedores cadastrados (fornecedores.json)
   - fornecedores que enviaram lista ontem (catalogo_arquivo/)
3. Detectar silêncio anormal durante a janela comercial (08h-17h) ou fila de captura travada.
4. Acionar recuperação automática (/api/scan-archived) com cooldown seguro (30 min) apenas quando necessário.
"""
import json
import os
import re
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COVERAGE_REPORT_FILE = os.path.join(BASE_DIR, "ultima_auditoria_cobertura.json")
ARCHIVED_MONITORED_FILE = os.path.join(BASE_DIR, "arquivados_monitorados.json")
COMMUNITIES_FILE = os.path.join(BASE_DIR, "comunidades_entradas.json")

RECOVERY_COOLDOWN_SECONDS = 1800  # 30 minutos entre recuperações automáticas no WhatsApp
SILENCE_ALERT_MINUTES = 25        # Alerta se passar > 25 min sem nenhuma leitura em horário comercial

_last_recovery_ts = 0.0


def _read_json_safe(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _norm_phone(val):
    return re.sub(r"\D+", "", str(val or ""))


def _load_yesterday_suppliers(base_dir, today_str):
    arch_dir = os.path.join(base_dir, "catalogo_arquivo")
    if not os.path.isdir(arch_dir):
        return set()
    files = sorted(fn for fn in os.listdir(arch_dir) if fn.endswith(".json"))
    if not files:
        return set()
    # Pega o arquivo mais recente com ofertas (pode ser o arquivado na virada de hoje)
    for fn in reversed(files[-3:]):
        data = _read_json_safe(os.path.join(arch_dir, fn), [])
        items = data if isinstance(data, list) else (data.get("products") if isinstance(data, dict) else [])
        sups = {str(p.get("fornecedor") or "").strip().upper() for p in items if p.get("fornecedor") and p.get("data") != today_str}
        if sups:
            return sups
    return set()


def _load_raw_channels_read_today(base_dir):
    """Lê os canais/grupos que tiveram QUALQUER mensagem recebida hoje (mesmo sem preço), via revisao_listas."""
    day_iso = time.strftime("%Y-%m-%d")
    rev_dir = os.path.join(base_dir, "revisao_listas")
    channels = set()
    if not os.path.isdir(rev_dir):
        return channels
    for fn in os.listdir(rev_dir):
        if day_iso not in fn or not fn.endswith(".jsonl"):
            continue
        p = os.path.join(rev_dir, fn)
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    for key in ("remoteJid", "groupName", "origem", "fornecedor"):
                        v = str(obj.get(key) or "").strip()
                        if v:
                            channels.add(v.upper())
        except OSError:
            continue
    return channels


def evaluate_coverage(
    *,
    base_dir=BASE_DIR,
    today_str=None,
    now_hour=None,
    wa_status=None,
    participating_groups=None,
    produtos_today=None,
    fornecedores=None,
    monitored_list=None,
    yesterday_suppliers=None,
    raw_channels_today=None,
):
    """
    Avalia cobertura completa de comunidades, grupos, canais e contatos.
    Função pura/testável: aceita injeção de dados em testes ou lê direto do disco em produção.
    """
    today = today_str or time.strftime("%d/%m/%Y")
    hour = datetime.now().hour if now_hour is None else int(now_hour)
    wa = wa_status or {}
    prods = produtos_today if produtos_today is not None else []
    forns = fornecedores if fornecedores is not None else []
    monitored = monitored_list if monitored_list is not None else _read_json_safe(os.path.join(base_dir, "arquivados_monitorados.json"), [])
    if not isinstance(monitored, list):
        monitored = []

    monitored_jids = {str(m.get("jid") or "").strip() for m in monitored if isinstance(m, dict) and m.get("jid")}
    monitored_groups = [m for m in monitored if isinstance(m, dict) and str(m.get("jid") or "").endswith("@g.us")]
    monitored_channels = [m for m in monitored if isinstance(m, dict) and str(m.get("jid") or "").endswith("@newsletter")]
    monitored_privates = [
        m for m in monitored
        if isinstance(m, dict)
        and not str(m.get("jid") or "").endswith("@g.us")
        and not str(m.get("jid") or "").endswith("@newsletter")
    ]

    # 1. Compara grupos/comunidades reais no WhatsApp vs monitorados
    unmonitored_groups = []
    participating_count = 0
    if isinstance(participating_groups, list):
        participating_count = len(participating_groups)
        for g in participating_groups:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or "").strip()
            if not gid:
                continue
            if g.get("blacklisted"):
                continue
            is_comm = bool(g.get("isCommunity") or g.get("isCommunityAnnounce"))
            raw_dec = g.get("decision")
            # Ignora grupos explicitamente fora do escopo (ex: grupo pessoal / decision.allowed == False)
            if isinstance(raw_dec, dict):
                if raw_dec.get("allowed") is False and not is_comm:
                    continue
            elif str(raw_dec or "").lower() == "block":
                continue
            if gid not in monitored_jids:
                unmonitored_groups.append({
                    "id": gid,
                    "subject": str(g.get("subject") or gid),
                    "isCommunity": is_comm,
                })

    # 2. Leitura efetiva de hoje (ofertas + canais/origens + fornecedores)
    today_prods = [p for p in prods if str(p.get("data") or "") == today]
    suppliers_today = {str(p.get("fornecedor") or "").strip().upper() for p in today_prods if p.get("fornecedor")}
    origins_today = {str(p.get("origem") or "").strip() for p in today_prods if p.get("origem")}
    phones_today = {_norm_phone(p.get("telefone")) for p in today_prods if p.get("telefone")}
    raw_seen = raw_channels_today if raw_channels_today is not None else _load_raw_channels_read_today(base_dir)

    # 3. Fornecedores habituais (ontem) que ainda não enviaram hoje
    yest_sups = yesterday_suppliers if yesterday_suppliers is not None else _load_yesterday_suppliers(base_dir, today)
    silent_usual = sorted(s for s in yest_sups if s not in suppliers_today and s not in raw_seen)
    coverage_pct = round((len(suppliers_today & yest_sups) / len(yest_sups)) * 100, 1) if yest_sups else 100.0

    # 4. Contatos cadastrados com atividade hoje
    reg_total = len(forns)
    reg_active_today = 0
    for f in forns:
        nm = str(f.get("nome") or "").strip().upper()
        tel = _norm_phone(f.get("telefone"))
        if nm in suppliers_today or (tel and tel in phones_today) or nm in raw_seen:
            reg_active_today += 1

    # 5. Saúde da fila de captura e tempo desde última mensagem recebida
    cap = wa.get("capture") if isinstance(wa.get("capture"), dict) else {}
    pending = int(cap.get("pending") or 0)
    failed = int(cap.get("failed") or 0)
    last_error = cap.get("lastError")
    last_rx_ms = wa.get("lastReceivedAt") or cap.get("lastProcessedAt")
    minutes_since_rx = None
    if isinstance(last_rx_ms, (int, float)) and last_rx_ms > 0:
        minutes_since_rx = round(max(0.0, (time.time() * 1000.0 - float(last_rx_ms)) / 60000.0), 1)

    wa_conn = str(wa.get("status") or "UNKNOWN")
    in_business_hours = 8 <= hour < 17

    issues = []
    needs_recovery = False

    if wa_conn != "CONNECTED":
        issues.append(f"WhatsApp fora do ar ({wa_conn})")
    if unmonitored_groups:
        issues.append(f"{len(unmonitored_groups)} grupo(s)/comunidade(s) fora do monitoramento")
        needs_recovery = True
    if failed > 0 or last_error:
        issues.append(f"Fila de captura com {failed} falha(s)" + (f": {last_error}" if last_error else ""))
        needs_recovery = True
    if in_business_hours and wa_conn == "CONNECTED" and minutes_since_rx is not None and minutes_since_rx >= SILENCE_ALERT_MINUTES:
        issues.append(f"Sem nenhuma mensagem recebida há {minutes_since_rx:.0f} min em horário comercial")
        needs_recovery = True

    status_level = "OK" if not issues else ("ALERTA" if wa_conn == "CONNECTED" else "CRITICO")

    return {
        "checked": time.strftime("%d/%m/%Y %H:%M:%S"),
        "status": status_level,
        "whatsappStatus": wa_conn,
        "monitoredTotal": len(monitored),
        "monitoredGroups": len(monitored_groups),
        "monitoredChannels": len(monitored_channels),
        "monitoredPrivates": len(monitored_privates),
        "communitiesCount": int(wa.get("communitiesCount") or 0),
        "participatingGroups": participating_count,
        "unmonitoredGroupsCount": len(unmonitored_groups),
        "unmonitoredGroups": unmonitored_groups[:15],
        "activeOriginsToday": len(origins_today),
        "activeSuppliersToday": len(suppliers_today),
        "yesterdaySuppliers": len(yest_sups),
        "coverageVsYesterdayPct": coverage_pct,
        "silentUsualSuppliersCount": len(silent_usual),
        "silentUsualSuppliers": silent_usual[:20],
        "registeredContacts": reg_total,
        "registeredActiveToday": reg_active_today,
        "capturePending": pending,
        "captureFailed": failed,
        "captureLastError": last_error,
        "minutesSinceLastMessage": minutes_since_rx,
        "issues": issues,
        "needsRecovery": needs_recovery,
        "recoveryTriggered": False,
    }


def run_coverage_audit(
    *,
    base_dir=BASE_DIR,
    bridge_get_fn=None,
    bridge_post_fn=None,
    produtos_today=None,
    fornecedores=None,
    allow_recovery=True,
):
    """Executa auditoria ao vivo no bridge WhatsApp e dispara recuperação automática (com cooldown) se necessário."""
    global _last_recovery_ts
    wa_status = {}
    participating = None

    if callable(bridge_get_fn):
        try:
            wa_status = bridge_get_fn("/api/status?lite=1") or {}
        except Exception:
            wa_status = {"status": "OFFLINE"}
        if wa_status.get("status") == "CONNECTED":
            try:
                g_resp = bridge_get_fn("/api/groups") or {}
                if g_resp.get("ok") and isinstance(g_resp.get("groups"), list):
                    participating = g_resp["groups"]
            except Exception:
                participating = None

    report = evaluate_coverage(
        base_dir=base_dir,
        wa_status=wa_status,
        participating_groups=participating,
        produtos_today=produtos_today,
        fornecedores=fornecedores,
    )

    if (
        allow_recovery
        and report["needsRecovery"]
        and report["whatsappStatus"] == "CONNECTED"
        and callable(bridge_post_fn)
        and (time.time() - _last_recovery_ts) >= RECOVERY_COOLDOWN_SECONDS
    ):
        try:
            scan_res = bridge_post_fn("/api/scan-archived", {})
            _last_recovery_ts = time.time()
            report["recoveryTriggered"] = True
            report["recoveryResult"] = {
                "monitoredCount": scan_res.get("monitoredCount") if isinstance(scan_res, dict) else None,
                "historyRequestsSent": scan_res.get("historyRequestsSent") if isinstance(scan_res, dict) else None,
            }
        except Exception as exc:
            report["recoveryError"] = f"{type(exc).__name__}: {exc}"

    return report
