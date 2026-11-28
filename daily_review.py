"""Arquivo diário das listas recebidas e segunda leitura das cotações."""
import base64
import binascii
import subprocess
import hashlib
import json
import math
import re
import sqlite3
import time
import threading
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from parser_listas import (clean_line, extract_price_from_line, looks_like_product_header,
                           parse_supplier_message, CURRENCY_PRICE, MONEY_NUMBER, COLOR_ALIASES,
                           excluded_power_accessory, extract_color, _is_clear_ocr_text)
from price_guardian import validate_product, review_quoted_price
import color_guardian
import apple_reference


live_wakeup = threading.Event()


def database(root):
    directory = Path(root) / "revisao_listas"
    directory.mkdir(mode=0o700, exist_ok=True)
    db = sqlite3.connect(directory / "mensagens.sqlite3", timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS mensagens
        (dia TEXT, chave TEXT, dados TEXT NOT NULL, PRIMARY KEY (dia,chave))""")
    db.execute("""CREATE TABLE IF NOT EXISTS transcricoes
        (dia TEXT, chave TEXT, texto TEXT NOT NULL, PRIMARY KEY (dia,chave))""")
    db.execute("""CREATE TABLE IF NOT EXISTS revisoes_ao_vivo
        (dia TEXT, chave TEXT, concluida TEXT, PRIMARY KEY (dia,chave))""")
    db.execute("""CREATE TABLE IF NOT EXISTS resultados_captura
        (dia TEXT NOT NULL, origem TEXT NOT NULL, oferta TEXT NOT NULL,
         timestamp REAL NOT NULL, editada INTEGER NOT NULL, dados TEXT NOT NULL,
         status TEXT NOT NULL, motivo TEXT NOT NULL,
         PRIMARY KEY (dia, origem, oferta))""")
    db.execute("""CREATE INDEX IF NOT EXISTS mensagens_origem ON mensagens
        (dia, json_extract(dados, '$.remoteJid'), json_extract(dados, '$.messageId'))""")
    return db


def _image_text_score(text, msg_timestamp):
    clear = _is_clear_ocr_text(text)
    if not clear:
        return (0, 0, 0)
    rows = parse_supplier_message(text, "OCR", "", msg_timestamp=msg_timestamp,
                                  include_non_new=True)
    lines = [clean_line(line) for line in text.splitlines()]
    positions = defaultdict(list)
    for index, line in enumerate(lines):
        positions[line].append(index)
    confirmed = 0
    for row in rows:
        valid, _, item = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
        if (valid and item and not review_quoted_price(row) and
                evidence_block(row, positions, lines, text.splitlines()) is not None):
            confirmed += 1
    return (1, confirmed, len(rows))


def image_text(db, day, key, encoded, msg_timestamp=None):
    cached = db.execute("SELECT texto FROM transcricoes WHERE dia=? AND chave=?", (day, key)).fetchone()
    if cached and cached[0] is not None:
        return cached[0]
    try:
        image = base64.b64decode(encoded.rsplit(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        return ""
    candidates = []
    for mode in ("6", "11"):
        try:
            proc = subprocess.run(["tesseract", "stdin", "stdout", "-l", "por+eng", "--psm", mode],
                                  input=image, capture_output=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            text = proc.stdout.decode("utf-8", "replace").strip()
            if text and text not in candidates:
                candidates.append(text)
    if not candidates:
        return ""
    result = max(candidates, key=lambda text: _image_text_score(text, msg_timestamp))
    with db:
        db.execute("INSERT OR REPLACE INTO transcricoes VALUES (?,?,?)", (day, key, result))
    return result


def insert(db, msg, live=False):
    timestamp = msg.get("timestamp")
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    identity = [msg.get(k) for k in ("remoteJid", "participantJid", "supplierPhone", "timestamp", "text")]
    identity.extend([msg.get("messageId") or "", msg.get("imageBase64") or ""])
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    db.execute("INSERT OR IGNORE INTO mensagens VALUES (?,?,?)",
               (day, key, json.dumps(msg, ensure_ascii=False)))
    if live:
        db.execute("INSERT OR IGNORE INTO revisoes_ao_vivo(dia,chave) VALUES (?,?)", (day, key))
    return day, key


def message_origin(msg):
    if not msg.get("messageId"):
        return ""
    return json.dumps([msg.get("remoteJid") or "", msg.get("messageId")], ensure_ascii=False)


def message_revision(msg):
    return (msg.get("timestamp") or 0, bool(msg.get("editKey")))


def latest_message_keys(db, day, origin=None):
    latest = {}
    sql = "SELECT chave,dados FROM mensagens WHERE dia=?"
    args = [day]
    if origin:
        remote, message_id = json.loads(origin)
        sql += " AND json_extract(dados, '$.remoteJid')=? AND json_extract(dados, '$.messageId')=?"
        args += [remote, message_id]
    for key, raw in db.execute(sql + " ORDER BY rowid", args):
        msg = json.loads(raw)
        origin = message_origin(msg)
        if origin and (origin not in latest or message_revision(msg) > latest[origin][0]):
            latest[origin] = (message_revision(msg), key)
    return {origin: value[1] for origin, value in latest.items()}


def source_is_current(root, origin, timestamp, edited=False):
    if not origin:
        return True
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    with closing(database(root)) as db:
        key = latest_message_keys(db, day, origin).get(origin)
        if not key:
            return True
        raw = db.execute("SELECT dados FROM mensagens WHERE dia=? AND chave=?", (day, key)).fetchone()
    return (timestamp, bool(edited)) >= message_revision(json.loads(raw[0]))


def attach_message_source(items, msg):
    for item in items:
        item["mensagemOrigem"] = message_origin(msg)
        item["mensagemTimestamp"] = msg["timestamp"]
        item["mensagemEditada"] = bool(msg.get("editKey"))
    return items


def record_capture_outcomes(root, outcomes):
    rows = []
    for outcome in outcomes:
        item = outcome.get("item") or {}
        origin = item.get("mensagemOrigem")
        timestamp = item.get("mensagemTimestamp")
        if (not origin or not isinstance(timestamp, (int, float)) or
                not math.isfinite(timestamp) or timestamp <= 0):
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
        data = {
            field: item.get(field)
            for field in ("modelo", "categoria", "capacidade", "cor", "condicao",
                          "fornecedor", "telefone", "data", "hora", "fonteCaptura")
        }
        price = item.get("preco")
        data["preco"] = price if isinstance(price, (int, float)) and math.isfinite(price) else None
        data["mensagemTimestamp"] = timestamp
        rows.append((day, origin, outcome["offerKey"], timestamp,
                     int(bool(item.get("mensagemEditada"))),
                     json.dumps(data, ensure_ascii=False, allow_nan=False),
                     outcome["status"], str(outcome.get("reason") or "")))
    if not rows:
        return
    with closing(database(root)) as db, db:
        db.executemany("""INSERT OR REPLACE INTO resultados_captura
            (dia, origem, oferta, timestamp, editada, dados, status, motivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)


def mark_capture_origin_superseded(root, origin, timestamp):
    if not origin or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        return
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    with closing(database(root)) as db, db:
        db.execute("""UPDATE resultados_captura SET status='superseded', motivo='mensagem_editada'
            WHERE dia=? AND origem=? AND timestamp<=?""", (day, origin, timestamp))


def load_capture_outcomes(root, day):
    with closing(database(root)) as db:
        rows = db.execute("""SELECT origem, oferta, timestamp, editada, dados, status, motivo
            FROM resultados_captura WHERE dia=?""", (day,)).fetchall()
    return [{
        "origin": origin,
        "offerKey": offer,
        "timestamp": timestamp,
        "edited": bool(edited),
        "item": json.loads(data),
        "status": status,
        "reason": reason,
    } for origin, offer, timestamp, edited, data, status, reason in rows]


def save_message(root, msg):
    with closing(database(root)) as db, db:
        identity = insert(db, msg, live=True)
        origin = message_origin(msg)
        if origin and latest_message_keys(db, identity[0], origin).get(origin) != identity[1]:
            return None
    live_wakeup.set()
    return identity


def import_previous_log(root):
    path = Path(root) / "ultimas_listas_brutas.json"
    if path.exists():
        with closing(database(root)) as db, db:
            for msg in json.loads(path.read_text()):
                if time.strftime("%Y-%m-%d", time.localtime(msg.get("timestamp") or 0)) == time.strftime("%Y-%m-%d"):
                    insert(db, msg)


def _is_color_line(text):
    u = str(text or "").strip().upper()
    if not u:
        return False
    if u in COLOR_ALIASES:
        return True
    parts = [p for p in re.split(r"[\s/,|]+", u) if p]
    return bool(parts) and all(p in COLOR_ALIASES for p in parts)


def priced_variant(line):
    amount = CURRENCY_PRICE.search(line)
    if not amount:
        bare = re.fullmatch(r"(.+?)\s+(" + MONEY_NUMBER + r")\s*(?:R?\$)?", line)
        return bool(bare and _is_color_line(bare[1]) and extract_price_from_line(line))
    label = line[:amount.start()].removesuffix("R").strip(" .-$").upper()
    return _is_color_line(label) or bool(re.fullmatch(r"\d+\s*(?:GB|TB)(?:\s+\d+\s*GB)?", label))


def evidence_block(row, positions, lines, raw_lines=None):
    # O parser acrescenta a condição como primeira linha. Localize no original
    # tanto o cabeçalho como o preço, sem atravessar outro produto ou parágrafo.
    context = row["contextoOriginal"].splitlines()[1:]
    if not context:
        return None
    for start in positions.get(context[0], ()):
        cursor = start
        matched = [start]
        for target in context[1:]:
            cursor += 1
            while cursor < len(lines) and lines[cursor] != target:
                skipped = lines[cursor]
                target_variant = (
                    _is_color_line(target) or priced_variant(target) or
                    extract_price_from_line(target) is not None
                )
                skipped_variant = (
                    _is_color_line(skipped) or priced_variant(skipped) or
                    extract_price_from_line(skipped) is not None
                )
                sibling = target_variant and skipped_variant
                if skipped and (not sibling or looks_like_product_header(skipped)):
                    break
                cursor += 1
            if cursor >= len(lines) or lines[cursor] != target:
                break
            matched.append(cursor)
        else:
            for i in reversed(matched):
                if extract_price_from_line(lines[i]) != row["preco"]:
                    continue
                if _evidence_color_matches(row, matched, lines, raw_lines):
                    return i
                has_text_color = any(extract_color(lines[index]) != "-" for index in matched)
                if not has_text_color:
                    for sibling in range(matched[0] + 1, len(lines)):
                        if looks_like_product_header(lines[sibling]):
                            break
                        if (extract_price_from_line(lines[sibling]) == row["preco"] and
                                _evidence_color_matches(row, [sibling], lines, raw_lines)):
                            return sibling
    return None


def _evidence_color_matches(row, matched, lines, raw_lines=None):
    expected = str(row.get("cor") or "-").strip()
    if not expected or expected == "-":
        return True
    expected = COLOR_ALIASES.get(expected.upper(), expected).upper()
    model = row.get("modelo", "")
    observed_colors = []
    for index in reversed(matched):
        observed = extract_color(lines[index])
        if observed == "-" and raw_lines and index < len(raw_lines):
            observed = color_guardian.resolve_emoji_color(raw_lines[index], model) or "-"
        if observed != "-":
            observed_colors.append(COLOR_ALIASES.get(observed.upper(), observed).upper())
    return expected in observed_colors


def review_message(db, day, message_id, msg, offer_key, stats, pending):
    offers = {}
    sources = []
    image = msg.get("imageBase64")
    has_image = bool(image or msg.get("hasImage"))
    text = (msg.get("text") or "").strip()
    if text:
        sources.append(("legenda" if has_image else "lista", text))
    if has_image:
        stats["mensagens_com_imagem"] += 1
        if image:
            transcription = image_text(db, day, message_id, image, msg.get("timestamp"))
            if transcription and _is_clear_ocr_text(transcription):
                sources.append(("imagem", transcription))
            elif transcription:
                pending.append([message_id, None, "", "imagem_ocr_confuso", "imagem"])
            else:
                pending.append([message_id, None, "", "imagem_sem_texto_legivel", "imagem"])
        else:
            stats["imagens_sem_original"] += 1
            pending.append([message_id, None, "", msg.get("mediaIssue") or "imagem_sem_original", "imagem"])
    for source, text in sources:
        stats["leituras_" + source] += 1
        rows = parse_supplier_message(text, msg["supplierName"], msg["supplierPhone"],
                                      msg.get("groupName") or msg.get("pushName", ""),
                                      msg_timestamp=msg["timestamp"],
                                      include_non_new=msg.get("includeNonNew", True), _gemini_retry=True)
        if source == "imagem" and not rows:
            pending.append([message_id, None, "", "imagem_sem_cotacao_confirmada_no_texto", source])
        raw_lines = text.splitlines()
        lines = [clean_line(l) for l in raw_lines]
        positions = defaultdict(list)
        for i, line in enumerate(lines):
            positions[line].append(i)
        covered = set()
        for row in attach_message_source(rows, msg):
            index = evidence_block(row, positions, lines, raw_lines)
            ok, reason, item = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
            issue = review_quoted_price(row) or (reason if not ok else "")
            if ok and apple_reference.model_status(item["modelo"]) is False:
                issue = "modelo_sem_confirmacao"
            if index is None:
                issue = issue or "vinculo_com_a_mensagem_inconclusivo"
            else:
                covered.add(index)
            key = offer_key(item or row)
            if item:
                item["fonteCaptura"] = source
            if issue:
                stats["ofertas_pendentes"] += 1
                pending.append([message_id, index, row["modelo"], issue, source])
            if key in offers:
                previous = offers[key]
                if previous is None:
                    continue
                if previous["preco"] != row["preco"]:
                    stats["conflitos_de_preco"] += 1
                    pending.append([message_id, index, row["modelo"], "precos_divergentes_na_mensagem", source])
                    issue = True
            offers[key] = None if issue else item
        excluded_block = False
        for i, line in enumerate(lines):
            if excluded_power_accessory(line):
                excluded_block = True
            elif looks_like_product_header(line) or i in covered:
                excluded_block = False
            if not excluded_block and i not in covered and CURRENCY_PRICE.search(line):
                stats["linhas_com_preco_a_conferir"] += 1
                pending.append([message_id, i, "", "linha_com_preco_a_conferir", source])
    return offers


def media_offers(root, identity, msg, offer_key):
    with closing(database(root)) as db:
        offers = review_message(db, *identity, msg, offer_key, Counter(), [])
    return [item for item in offers.values() if item]


def run(root, offer_key, apply, *, live=False, pace=0):
    day = time.strftime("%Y-%m-%d")
    stats = Counter()
    latest = {}
    pending = []
    with closing(database(root)) as db:
        if live:
            keys = db.execute("""SELECT chave FROM revisoes_ao_vivo
                WHERE dia=? AND concluida IS NULL ORDER BY rowid LIMIT 8""", (day,)).fetchall()
            if not keys:
                return
        else:
            with db:
                db.execute("DELETE FROM mensagens WHERE dia < ?", (day,))
                db.execute("DELETE FROM transcricoes WHERE dia < ?", (day,))
                db.execute("DELETE FROM revisoes_ao_vivo WHERE dia < ?", (day,))
                db.execute("DELETE FROM resultados_captura WHERE dia < ?", (day,))
            keys = db.execute("SELECT chave FROM mensagens WHERE dia=?", (day,)).fetchall()
        current_keys = latest_message_keys(db, day)
        for (message_id,) in keys:
            if pace:
                time.sleep(pace)
            raw = db.execute("SELECT dados FROM mensagens WHERE dia=? AND chave=?", (day, message_id)).fetchone()[0]
            if time.strftime("%Y-%m-%d") != day:
                return
            msg = json.loads(raw)
            origin = message_origin(msg)
            if origin and current_keys.get(origin) != message_id:
                continue
            stats["mensagens_relidas"] += 1
            offers = review_message(db, day, message_id, msg, offer_key, stats, pending)
            ts = msg["timestamp"]
            for key, item in offers.items():
                if key not in latest or ts > latest[key][0]:
                    latest[key] = (ts, item)
                elif ts == latest[key][0]:
                    previous = latest[key][1]
                    if not item or (previous and previous["preco"] != item["preco"]):
                        latest[key] = (ts, None)
    if time.strftime("%Y-%m-%d") != day:
        return
    candidates = [item for _, item in latest.values() if item]
    stats["ofertas_confirmadas"] = len(candidates)
    stats["atualizacoes"] = apply(candidates)
    if live:
        with closing(database(root)) as db, db:
            db.executemany("UPDATE revisoes_ao_vivo SET concluida=? WHERE dia=? AND chave=?",
                           [(time.strftime("%H:%M:%S"), day, key) for key, in keys])
    return {"dia": day, "concluida": time.strftime("%H:%M:%S"), **stats, "pendencias": pending}
