"""Arquivo diário das listas recebidas e segunda leitura das cotações."""
import base64
import binascii
import subprocess
import hashlib
import json
import re
import sqlite3
import time
import threading
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from parser_listas import (clean_line, extract_price_from_line, looks_like_product_header,
                           parse_supplier_message, CURRENCY_PRICE, MONEY_NUMBER, COLOR_ALIASES, excluded_power_accessory)
from price_guardian import validate_product, review_quoted_price
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
    return db


def image_text(db, day, key, encoded):
    cached = db.execute("SELECT texto FROM transcricoes WHERE dia=? AND chave=?", (day, key)).fetchone()
    if cached and cached[0] is not None:
        return cached[0]
    try:
        image = base64.b64decode(encoded.rsplit(",", 1)[-1], validate=True)
        for mode in ("6", "11"):
            proc = subprocess.run(["tesseract", "stdin", "stdout", "-l", "por+eng", "--psm", mode],
                                  input=image, capture_output=True, timeout=12)
            if proc.returncode == 0 and proc.stdout.strip():
                break
    except (OSError, binascii.Error, subprocess.TimeoutExpired):
        return ""
    if proc.returncode:
        return ""
    result = proc.stdout.decode("utf-8", "replace").strip()
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


def save_message(root, msg):
    with closing(database(root)) as db, db:
        identity = insert(db, msg, live=True)
    live_wakeup.set()
    return identity


def import_previous_log(root):
    path = Path(root) / "ultimas_listas_brutas.json"
    if path.exists():
        with closing(database(root)) as db, db:
            for msg in json.loads(path.read_text()):
                if time.strftime("%Y-%m-%d", time.localtime(msg.get("timestamp") or 0)) == time.strftime("%Y-%m-%d"):
                    insert(db, msg)


def priced_variant(line):
    amount = CURRENCY_PRICE.search(line)
    if not amount:
        bare = re.fullmatch(r"(.+?)\s+(" + MONEY_NUMBER + r")", line)
        return bool(bare and bare[1].upper() in COLOR_ALIASES and extract_price_from_line(line))
    label = line[:amount.start()].removesuffix("R").strip(" .-").upper()
    return label in COLOR_ALIASES or bool(re.fullmatch(r"\d+\s*(?:GB|TB)(?:\s+\d+\s*GB)?", label))


def evidence_block(row, positions, lines):
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
                sibling = ((target.upper() in COLOR_ALIASES and skipped.upper() in COLOR_ALIASES) or
                           (priced_variant(target) and priced_variant(skipped)))
                if skipped and (not sibling or looks_like_product_header(skipped)):
                    break
                cursor += 1
            if cursor >= len(lines) or lines[cursor] != target:
                break
            matched.append(cursor)
        else:
            for i in reversed(matched):
                if extract_price_from_line(lines[i]) == row["preco"]:
                    return i
    return None


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
            transcription = image_text(db, day, message_id, image)
            if transcription:
                sources.append(("imagem", transcription))
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
        lines = [clean_line(l) for l in text.splitlines()]
        positions = defaultdict(list)
        for i, line in enumerate(lines):
            positions[line].append(i)
        covered = set()
        for row in rows:
            index = evidence_block(row, positions, lines)
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
            keys = db.execute("SELECT chave FROM mensagens WHERE dia=?", (day,)).fetchall()
        for (message_id,) in keys:
            if pace:
                time.sleep(pace)
            raw = db.execute("SELECT dados FROM mensagens WHERE dia=? AND chave=?", (day, message_id)).fetchone()[0]
            if time.strftime("%Y-%m-%d") != day:
                return
            msg = json.loads(raw)
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
