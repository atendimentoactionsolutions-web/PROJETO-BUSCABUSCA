"""Índice de modelos a partir dos títulos dos guias oficiais de identificação."""
import html
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "apple_reference_cache.json"
GUIDES = {"IPHONE": "108044", "AIRPODS": "109525", "APPLE WATCH": "108056",
          "IPAD": "108043", "MACBOOK AIR": "102869", "MACBOOK PRO": "108052",
          "MAC MINI": "102852", "MAC STUDIO": "102231", "IMAC": "108054"}
lock = threading.Lock()


def normalized(value):
    value = unicodedata.normalize("NFKD", html.unescape(value)).upper()
    return " ".join(re.findall(r"[A-Z0-9]+", value))


def read_index(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


index = read_index(ROOT / "apple_reference_seed.json")
index.update(read_index(CACHE))


def refresh(force=False):
    updated = dict(index)
    for family, article in GUIDES.items():
        if not force and time.time() - updated.get(family, {}).get("checked", 0) < 21600:
            continue
        url = "https://support.apple.com/en-us/" + article
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                page = response.read(3000000).decode()
            headings = re.findall(r"<h[234]\b[^>]*>(.*?)</h[234]>", page, re.S | re.I)
            names = sorted({normalized(re.sub("<[^>]+>", "", h)) for h in headings})
            names = [n for n in names if n.startswith(family + " ") and re.search(r"\d|DUO|AIR$", n)]
            if len(names) >= 3:
                updated[family] = {"url": url, "checked": time.time(), "names": names}
        except (OSError, ValueError):
            continue
    if updated != index:
        with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, delete=False) as f:
            json.dump(updated, f, ensure_ascii=False)
        os.replace(f.name, CACHE)
        with lock:
            index.update(updated)


def model_status(model):
    """None: família sem guia; True: correspondência; False: exige confirmação."""
    m = normalized(model)
    # Algumas gerações aparecem apenas com o ano no título do guia.
    spec_articles = {"IMAC M4 24": "121557", "MAC MINI M4": "121555", "MAC MINI M4 PRO": "121555",
                     "MAC STUDIO M4 MAX": "122211", "MACBOOK AIR M1 13": "111883",
                     "MACBOOK PRO M3 MAX 14": "117736", "MACBOOK NEO 13": "126322"}
    if m in spec_articles:
        return True
    family = next((f for f in GUIDES if m.startswith(f + " ")), None)
    if not family:
        return None
    with lock:
        names = index.get(family, {}).get("names", ())
    if family == "IPHONE":
        key = m.replace("IPHONE 17 AIR", "IPHONE AIR")
        return key in names
    if family == "AIRPODS":
        key = m.replace(" USB C", "")
        if key == "AIRPODS 5 ANC":
            key = "AIRPODS 5 WITH WIRELESS CHARGING CASE"
        return any(n == key or n.startswith(key + " WITH ") or n.startswith(key + " USB C") for n in names)
    if family == "APPLE WATCH":
        key = re.sub(r"\bS(\d+)\b", r"SERIES \1", m)
        key = re.sub(r"\bSE(\d)", r"SE \1", key)
        key = re.split(r" \d{2}\s*MM\b| GPS\b", key)[0]
        return any(n == key or n.startswith(key + " GPS") for n in names)
    if family == "IPAD":
        kind = re.match(r"IPAD(?: (?:AIR|PRO|MINI))?", m)[0]
        if kind == "IPAD" and re.match(r"IPAD (?:11(?:TH)?\b|A16\b)", m):
            return "IPAD A16" in names
        if kind == "IPAD MINI" and re.search(r"\b(?:7|A17)\b", m):
            return "IPAD MINI A17 PRO" in names
        chip = re.search(r"\bM\d+\b", m)
        size = re.search(r"\b(?:T)?(11|13)\b", m)
        if chip and size:
            return f"{kind} {size[1]} INCH {chip[0]}" in names
        gen = re.search(r"\b(\d+)(?:TH|GER)?\b", m[len(kind):])
        return bool(gen and any(re.match(re.escape(kind) + rf" {gen[1]}(?:ST|ND|RD|TH) GENERATION\b", n) for n in names))
    chip = re.search(r"\bM\d+(?: (?:PRO|MAX|ULTRA))?\b", m)
    size = re.search(r'\b(13|14|15|16|24)"', model)
    if not chip:
        return False
    return any((not size or size[1] + " INCH" in n) and
               re.search(r"\b" + chip[0] + r"\b" + (r"(?! (?:PRO|MAX|ULTRA))" if " " not in chip[0] else ""), n)
               for n in names)
