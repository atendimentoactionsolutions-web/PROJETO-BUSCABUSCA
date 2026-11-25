"""
price_guardian.py - Guardião de Preços e Detector de Anomalias em Segundo Plano

Regras:
1. Rejeição de itens não-Apple / réplicas / seminovos (sem bloqueio cego de gerações Apple futuras).
2. Faixas de sanidade realistas por modelo Apple (mínimo e máximo).
3. Detecção de desvio da mediana dos fornecedores de hoje (outliers de digitação).
4. Registro transparente de todos os itens barrados em anomalias_detectadas.json para auditoria.
"""

import json
import math
import os
import re
import time
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANOMALY_LOG_FILE = os.path.join(BASE_DIR, "anomalias_detectadas.json")

def _is_seminovo_condition(cond, cat=""):
    c = f"{cond} {cat}".lower()
    return bool(re.search(r"\b(?:semi[\s-]*nov[oa]s?|vitrine|swap|usad[oa]s?|grade\s*[ab]\+?|as\s*is|asis|[aá]sis|ativad[oa]s?|deslacrad[oa]s?|open\s*box|caixa\s*aberta|recondicionad[oa]s?)\b", c))


# Modelos de iPhone descontinuados que NÃO existem mais Novos/Lacrados (apenas Seminovo ou CPO)
DISCONTINUED_NEW_IPHONE_RE = re.compile(
    r"\bIPHONE\s+(?:[78]|X[RS]?|SE(?:\s*[23])?|11|12|13|14\s+(?:PLUS|PRO))\b",
    re.I,
)


# Faixas de sanidade por modelo para Novos / Lacrados (em R$)
SANITY_BOUNDS = [
    (re.compile(r"MAGIC\s+KEYBOARD|SMART\s+KEYBOARD|TECLADO", re.I), 400, 4200),
    (re.compile(r"MAGIC\s+TRACKPAD|TRACKPAD", re.I), 500, 2000),
    (re.compile(r"MAGIC\s+MOUSE", re.I), 400, 1500),
    (re.compile(r"APPLE\s+PENCIL|PENCIL", re.I), 350, 1500),
    (re.compile(r"AIRTAG.*4\s*PACK", re.I), 400, 950),
    (re.compile(r"AIRTAG", re.I), 140, 350),
    (re.compile(r"EARPODS", re.I), 75, 250),
    (re.compile(r"FONTE|CARREGADOR", re.I), 40, 450),
    (re.compile(r"CABO", re.I), 40, 300),
    (re.compile(r"IPHONE\s+13\b", re.I), 2500, 4800),
    (re.compile(r"IPHONE\s+14\s+PLUS", re.I), 3400, 5500),
    (re.compile(r"IPHONE\s+14\b", re.I), 3000, 5000),
    (re.compile(r"IPHONE\s+15\s+PLUS", re.I), 4000, 6200),
    (re.compile(r"IPHONE\s+15\s+PRO\s+MAX", re.I), 4900, 8800),
    (re.compile(r"IPHONE\s+15\s+PRO\b", re.I), 4400, 7800),
    (re.compile(r"IPHONE\s+15\b", re.I), 3500, 5600),
    (re.compile(r"IPHONE\s+16\s+PLUS", re.I), 4700, 7600),
    (re.compile(r"IPHONE\s+16\s+PRO\s+MAX", re.I), 5800, 12500),
    (re.compile(r"IPHONE\s+16\s+PRO\b", re.I), 5000, 9800),
    (re.compile(r"IPHONE\s+16E\b", re.I), 3000, 5500),
    (re.compile(r"IPHONE\s+16\b", re.I), 4000, 6800),
    (re.compile(r"IPHONE\s+17E\b", re.I), 3600, 5800),
    (re.compile(r"IPHONE\s+17", re.I), 4800, 13500),
    (re.compile(r"IPHONE.*(?:DUO|DOU|FOLD)", re.I), 11000, 30000),
    (re.compile(r"IPHONE\s+18\s+PRO\s+MAX", re.I), 7800, 22000),
    (re.compile(r"IPHONE\s+18\s+PRO\b", re.I), 6800, 16500),
    (re.compile(r"IPHONE\s+18\b", re.I), 5800, 11500),
    (re.compile(r"MACBOOK\s+AIR", re.I), 4500, 16500),
    (re.compile(r"MACBOOK\s+PRO", re.I), 7000, 42000),
    (re.compile(r"MACBOOK", re.I), 4500, 42000),
    (re.compile(r"MAC\s+MINI", re.I), 3900, 22000),
    (re.compile(r"MAC\s+STUDIO", re.I), 12000, 38000),
    (re.compile(r"IMAC", re.I), 6500, 19500),
    (re.compile(r"IPAD\s+(?:9|10|11)\b", re.I), 1900, 4800),
    (re.compile(r"IPAD\s+MINI", re.I), 2800, 6500),
    (re.compile(r"IPAD\s+AIR", re.I), 1700, 9500),
    (re.compile(r"IPAD\s+PRO", re.I), 5000, 16000),
    (re.compile(r"IPAD", re.I), 1700, 16000),
    (re.compile(r"WATCH\s+SE\b", re.I), 1400, 2800),
    (re.compile(r"WATCH\s+(?:S9|SERIES\s*9|S10|SERIES\s*10|S11|SERIES\s*11|S12|SERIES\s*12)", re.I), 1350, 5200),
    (re.compile(r"WATCH\s+ULTRA", re.I), 3900, 7500),
    (re.compile(r"APPLE\s+WATCH", re.I), 1350, 7500),
    (re.compile(r"AIRPODS\s+PRO", re.I), 1100, 2400),
    (re.compile(r"AIRPODS\s+MAX", re.I), 2400, 5500),
    (re.compile(r"AIRPODS\s+(?:[2-9])\b", re.I), 450, 1950),
    (re.compile(r"AIRPODS", re.I), 450, 5500),
]

# Faixas de sanidade específicas para Seminovos / Vitrine / Swap (em R$)
SEMINOVO_SANITY_BOUNDS = [
    (re.compile(r"MAGIC\s+KEYBOARD|SMART\s+KEYBOARD|TECLADO", re.I), 350, 3800),
    (re.compile(r"MAGIC\s+TRACKPAD|TRACKPAD", re.I), 350, 1800),
    (re.compile(r"MAGIC\s+MOUSE", re.I), 250, 1200),
    (re.compile(r"APPLE\s+PENCIL|PENCIL", re.I), 250, 1200),
    (re.compile(r"AIRTAG", re.I), 100, 800),
    (re.compile(r"EARPODS", re.I), 75, 250),
    (re.compile(r"AIRPODS\s+PRO", re.I), 600, 2200),
    (re.compile(r"AIRPODS\s+MAX", re.I), 1500, 4800),
    (re.compile(r"AIRPODS", re.I), 350, 4500),
    (re.compile(r"IPHONE\s+11\s+PRO\s+MAX", re.I), 1100, 2600),
    (re.compile(r"IPHONE\s+11\s+PRO\b", re.I), 1000, 2400),
    (re.compile(r"IPHONE\s+11\b", re.I), 750, 2100),
    (re.compile(r"IPHONE\s+12\s+PRO\s+MAX", re.I), 1400, 3200),
    (re.compile(r"IPHONE\s+12\s+PRO\b", re.I), 1100, 2900),
    (re.compile(r"IPHONE\s+12(?:\s+MINI)?\b", re.I), 800, 2500),
    (re.compile(r"IPHONE\s+13\s+PRO\s+MAX", re.I), 2100, 4200),
    (re.compile(r"IPHONE\s+13\s+PRO\b", re.I), 1800, 3800),
    (re.compile(r"IPHONE\s+13(?:\s+MINI)?\b", re.I), 1200, 3200),
    (re.compile(r"IPHONE\s+14\s+PRO\s+MAX", re.I), 2400, 4800),
    (re.compile(r"IPHONE\s+14\s+PRO\b", re.I), 2000, 4200),
    (re.compile(r"IPHONE\s+14(?:\s+PLUS)?\b", re.I), 1500, 3500),
    (re.compile(r"IPHONE\s+15\s+PRO\s+MAX", re.I), 3400, 6200),
    (re.compile(r"IPHONE\s+15\s+PRO\b", re.I), 3000, 5400),
    (re.compile(r"IPHONE\s+15(?:\s+PLUS)?\b", re.I), 2000, 4800),
    (re.compile(r"IPHONE\s+16\s+PRO\s+MAX", re.I), 4400, 8500),
    (re.compile(r"IPHONE\s+16\s+PRO\b", re.I), 3600, 7200),
    (re.compile(r"IPHONE\s+16(?:\s+PLUS|\s+16E)?\b", re.I), 2800, 5500),
    (re.compile(r"IPHONE\s+17\s+PRO\s+MAX", re.I), 5500, 11500),
    (re.compile(r"IPHONE\s+17\s+PRO\b", re.I), 4800, 9500),
    (re.compile(r"IPHONE\s+17\s+AIR\b", re.I), 4000, 8500),
    (re.compile(r"IPHONE\s+17E\b", re.I), 2800, 5200),
    (re.compile(r"IPHONE\s+17\b", re.I), 3800, 7800),
    (re.compile(r"IPHONE.*(?:DUO|DOU|FOLD)", re.I), 8500, 28000),
    (re.compile(r"IPHONE\s+18\s+PRO\s+MAX", re.I), 6500, 18000),
    (re.compile(r"IPHONE\s+18\s+PRO\b", re.I), 5500, 14500),
    (re.compile(r"IPHONE\s+18\b", re.I), 4500, 9500),
    (re.compile(r"MACBOOK\s+AIR", re.I), 2400, 13000),
    (re.compile(r"MACBOOK\s+PRO", re.I), 3500, 35000),
    (re.compile(r"MACBOOK", re.I), 2400, 35000),
    (re.compile(r"IPAD\s+PRO", re.I), 2500, 14500),
    (re.compile(r"IPAD\s+AIR", re.I), 1400, 7500),
    (re.compile(r"IPAD", re.I), 900, 14500),
    (re.compile(r"WATCH\s+ULTRA", re.I), 2400, 5800),
    (re.compile(r"APPLE\s+WATCH", re.I), 750, 4500),
]

FORBIDDEN_PATTERNS = [
    re.compile(r"REDMI|XIAOMI|POCO|REALME|SAMSUNG|GALAXY|MOTOROLA|MOTO\s|INFINIX|TECNO|HONOR|AMAZFIT|T-REX|HAYLOU|HUAWEI|HOTWAVE|OUKITEL", re.I),
    re.compile(r"PS5|PLAYSTATION|XBOX|NINTENDO|SWITCH|JBL|STARLINK|GARMIN|GOPRO|DJI|RAY-BAN|RAYBAN|BRACELET|TABLET|KIT\s+GAMER|LOGITECH|MULTILASER|REDRAGON", re.I),
    re.compile(r"PELLE|CAPINHA|OURA\s*RING", re.I),
]


def _norm_cap_for_guardian(cap):
    s = re.sub(r"\s+", "", str(cap or "-").upper())
    s = re.sub(r"^(\d+)T$", r"\1TB", s)
    return s or "-"


def _median_of(values):
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 != 0 else (vals[mid - 1] + vals[mid]) / 2.0


def _group_bucket_key(item):
    model = str(item.get("modelo") or "").upper().strip()
    cap = _norm_cap_for_guardian(item.get("capacidade"))
    if any(k in model for k in ("MACBOOK", "IMAC", "MAC MINI", "MAC STUDIO")) and "/" not in cap:
        ram_src = f"{item.get('capacidade') or ''} {item.get('linhaOriginal') or ''} {model}".upper()
        ram_m = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:GB|G)?\s*(?:RAM|/)", ram_src) or re.search(r"/\s*(8|16|18|24|32|36|48|64|96|128)\b", ram_src)
        if ram_m:
            cap = f"{ram_m.group(1)}/{cap}"
    is_cpo = "CPO" in model or "CPO" in str(item.get("condicao") or "").upper()
    is_semi = _is_seminovo_condition(item.get("condicao", ""), item.get("categoria", ""))
    return (model, cap, is_cpo, is_semi)


def _supplier_id(item):
    tel = re.sub(r"\D", "", str(item.get("telefone") or ""))
    return tel or str(item.get("fornecedor") or "").upper().strip()


def _variant_key(item, bucket):
    return (bucket, str(item.get("cor") or "").strip().upper(),
            str(item.get("condicao") or "").strip().upper())


def review_quoted_price(item):
    """Lê apenas o bloco desta oferta, antes de substituir um preço recebido."""
    from parser_listas import CURRENCY_PRICE, MONEY_NUMBER, PROMOTION, parse_num_br
    source = str(item.get("contextoOriginal") or item.get("linhaOriginal") or "")
    if re.search(r"\b\d{1,2}\s*(?:x|parcelas?)\s*(?:de\s*)?(?:R?\$\s*)?\d", source, re.I):
        return "VALOR_PARCELADO_CONFERIR_TOTAL"
    # Uma linha específica de cor pode substituir o preço genérico do cabeçalho.
    # Exige uma cor isolada e um único valor, sem opções ou parcelas.
    lines = source.strip().splitlines()
    if len(lines) > 1:
        variant = re.fullmatch(r"([\wÀ-ÿ -]+?)\s*(?:R?\$\.?\s*|\(\s*)(" +
                               MONEY_NUMBER + r")\s*\)?", lines[-1].strip(), re.I)
        if variant:
            from parser_listas import extract_color
            color = extract_color(variant[1])
            if color != "-":
                model = str(item.get("modelo") or "").upper()
                colors = [{"cor": c} for c in (color, item.get("cor"))]
                if all(_normalize_and_validate_model_color(c, model)[0] for c in colors) and colors[0]["cor"] == colors[1]["cor"]:
                    source = lines[-1]
                    if parse_num_br(variant[2]) != item["preco"]:
                        return "PRECO_DIVERGE_DA_MENSAGEM"
                    return ""
    # Também cobre listas que colocam o símbolo só no primeiro dos dois preços.
    for pair in re.finditer(r"(?:R?\$\s*|\b)(" + MONEY_NUMBER +
                            r")\s*(?:/|\bou\b)\s*(?:R?\$\s*)?(" + MONEY_NUMBER + r")", source, re.I):
        if "$" in pair[0] and parse_num_br(pair[1]) != parse_num_br(pair[2]):
            return "PRECOS_MULTIPLOS_CONFERIR_MENSAGEM"
    prices = {parse_num_br(value) for value in CURRENCY_PRICE.findall(source)}
    change = PROMOTION.search(source)
    if change:
        old, new = (parse_num_br(v) for v in change.groups())
        if prices <= {old, new} and new == item["preco"]:
            return ""
        return "PRECO_DIVERGE_DA_MENSAGEM"
    if not prices:
        return None
    if len(prices) == 1:
        return "" if prices.pop() == item["preco"] else "PRECO_DIVERGE_DA_MENSAGEM"
    return "PRECOS_MULTIPLOS_CONFERIR_MENSAGEM"


def _price_evidence(item):
    if review_quoted_price(item) == "":
        return "O preço consta na mensagem do fornecedor."
    return "Conferir o preço na mensagem do fornecedor."


def equalize_catalog_prices(products, log_anomalies=True):
    """Compara ofertas equivalentes e sinaliza desvios sem retirar ofertas."""
    groups = {}
    for item in products:
        sid = _supplier_id(item)
        if sid:
            key = _variant_key(item, _group_bucket_key(item))
            groups.setdefault(key, {}).setdefault(sid, []).append(float(item["preco"]))
    medians = {key: _median_of([_median_of(prices) for prices in suppliers.values()])
               for key, suppliers in groups.items() if len(suppliers) >= 2}
    for item in products:
        price = float(item["preco"])
        median = medians.get(_variant_key(item, _group_bucket_key(item)))
        note = item.get("alertaFaixaModelo") or ""
        if median and (price < median * 0.88 or price > median * 1.20):
            direction = "abaixo" if price < median else "acima"
            percent = round(abs(price - median) / median * 100)
            note = f"Valor {percent}% {direction} da mediana (R$ {median:.0f})."
        previous = str(item.get("observacao") or "")
        if note:
            item["observacao"] = f"{note} {_price_evidence(item)}"
        elif previous.startswith("Valor ") and ("mediana" in previous or "faixa" in previous):
            item["observacao"] = ""
        item["alertaPreco"] = bool(note)
    return products, []


def offer_color(product):
    """A descrição de uma pulseira ou um apelido do fornecedor pode ser ambíguo."""
    model = str(product.get("modelo") or "").upper()
    color = str(product.get("cor") or "")
    if not (model.startswith("APPLE WATCH SE") or model.startswith("SMART KEYBOARD")):
        return color
    source = str(product.get("linhaOriginal") or "").rsplit(") - ", 1)[-1]
    if re.search(r"\bWATCH SE\s*3\b", model) and color.upper() in {"AZUL", "NAVY", "BLUE"}:
        return "Cor da caixa a confirmar"
    if model.startswith("SMART KEYBOARD") and re.search(r"\b(?:SPACE GR[AE]Y|CINZA[ -]ESPACIAL)\b", source, re.I):
        return "Cor a confirmar"
    return color


def _review_condition_bucket(condition_str, model_str=""):
    c = str(condition_str or "").strip().casefold()
    m = str(model_str or "").upper()
    if "cpo" in c or "CPO" in m:
        return "cpo"
    if _is_seminovo_condition(c):
        return c
    if c in {"", "condição não informada", "novo / lacrado", "novo — lacre não informado", "lacrado", "novo"}:
        return "novo"
    return c


def public_price_review(rows):
    """Separa as ofertas publicáveis comparando consenso de fornecedores independentes."""
    groups = {}
    model_cap_groups = {}
    ambiguous = set()
    indexed = []
    for row in rows:
        color = offer_color(row)
        if color != row.get("cor"):
            row = dict(row, cor=color)
        variant = (row.get("data"), row.get("modelo"), row.get("capacidade"), color)
        raw_condition = str(row.get("condicao") or "").strip().casefold()
        bucket = _review_condition_bucket(raw_condition, row.get("modelo"))
        supplier = _supplier_id(row)
        price = float(row["preco"])
        group = (variant, bucket)
        mc_key = (row.get("data"), row.get("modelo"), row.get("capacidade"), bucket)
        indexed.append((row, group, mc_key, raw_condition, supplier, price))
        if supplier:
            groups.setdefault(group, {}).setdefault(supplier, []).append(price)
            if bucket == "novo":
                model_cap_groups.setdefault(mc_key, {}).setdefault(supplier, []).append(price)
            if raw_condition in {"seminovo", "deslacrado", "ativado", "asis", "cpo"}:
                ambiguous.add((variant, supplier, price))

    visible, held = [], []
    for group, suppliers in groups.items():
        groups[group] = {sid: _median_of(prices) for sid, prices in suppliers.items()}
    for mc_key, suppliers in model_cap_groups.items():
        model_cap_groups[mc_key] = {sid: _median_of(prices) for sid, prices in suppliers.items()}

    for row, group, mc_key, raw_condition, supplier, price in indexed:
        variant, bucket = group
        if raw_condition in {"", "condição não informada"} and (variant, supplier, price) in ambiguous:
            held.append((row, "Mesmo fornecedor e preço com outra condição declarada"))
            continue
        peers = [value for sid, value in groups.get(group, {}).items() if sid != supplier]
        if len(peers) >= 3:
            median = _median_of(peers)
            agreement = sum(abs(value - median) <= median * 0.08 for value in peers)
            if agreement >= max(3, len(peers) * 0.67) and abs(price - median) >= median * 0.17:
                held.append((row, f"Desvio de pelo menos 17% da mediana de R$ {median:.2f}"))
                continue
        if bucket == "novo":
            mc_peers = [value for sid, value in model_cap_groups.get(mc_key, {}).items() if sid != supplier]
            if len(mc_peers) >= 3:
                mc_median = _median_of(mc_peers)
                if price <= mc_median * 0.82:
                    held.append((row, f"Valor muito abaixo da mediana do modelo (R$ {mc_median:.2f}) — provável seminovo ou erro"))
                    continue
        visible.append(row)
    return visible, held


def log_anomaly(product, reason):
    try:
        data = []
        if os.path.exists(ANOMALY_LOG_FILE):
            try:
                with open(ANOMALY_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = []

        today = time.strftime("%d/%m/%Y")
        entry = {
            "timestamp": time.strftime("%d/%m/%Y, %H:%M:%S"),
            "data": product.get("data") or today,
            "modelo": product.get("modelo"),
            "capacidade": product.get("capacidade") or "-",
            "cor": product.get("cor") or "-",
            "preco": product.get("preco"),
            "fornecedor": product.get("fornecedor"),
            "telefone": product.get("telefone"),
            "origem": product.get("origem"),
            "linhaOriginal": product.get("linhaOriginal"),
            "motivoRejeicao": reason,
        }
        sig = (
            str(entry.get("data") or ""),
            str(entry.get("fornecedor") or ""),
            str(entry.get("modelo") or ""),
            str(entry.get("capacidade") or ""),
            str(entry.get("cor") or ""),
            str(reason),
        )
        data = [
            d for d in (data if isinstance(data, list) else [])
            if (
                str(d.get("data") or str(d.get("timestamp") or "")[:10]),
                str(d.get("fornecedor") or ""),
                str(d.get("modelo") or ""),
                str(d.get("capacidade") or ""),
                str(d.get("cor") or ""),
                str(d.get("motivoRejeicao") or ""),
            ) != sig
        ]
        data.insert(0, entry)
        if len(data) > 1000:
            data = data[:1000]
        tmp_path = f"{ANOMALY_LOG_FILE}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, ANOMALY_LOG_FILE)
        print(f"[GUARDIÃO ANTI-ERROS] 🛡️ Anomalia bloqueada: {product.get('modelo')} R$ {product.get('preco')} -> {reason}")
    except Exception as e:
        print(f"[GUARDIÃO] Erro ao registrar anomalia: {e}")


def load_anomalies(date_str=None):
    if not os.path.exists(ANOMALY_LOG_FILE):
        return []
    try:
        with open(ANOMALY_LOG_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            return []
        if date_str:
            rows = [r for r in rows if (r.get("data") == date_str or str(r.get("timestamp") or "").startswith(date_str))]
        return rows
    except Exception:
        return []


MISSING_COLOR_TOKENS = {
    "",
    "-",
    "NAO INFORMADA",
    "NÃO INFORMADA",
    "COR NAO INFORMADA",
    "COR NÃO INFORMADA",
    "PADRAO",
    "PADRÃO",
    "0",
    "00",
}


def _ascii_key(text):
    nfkd = unicodedata.normalize("NFKD", str(text or ""))
    clean = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", clean.lower()).strip()


_COMMON_COLOR_MAP = {
    "black": "PRETO",
    "white": "BRANCO",
    "silver": "PRATA",
    "gold": "DOURADO",
    "pink": "ROSA",
    "rose": "ROSA",
    "soft pink": "ROSA",
    "rosa claro": "ROSA",
    "purple": "ROXO",
    "lilas": "ROXO",
    "lavender": "LAVANDA",
    "green": "VERDE",
    "blue": "AZUL",
    "yellow": "AMARELO",
    "orange": "LARANJA",
    "red": "VERMELHO",
    "gray": "CINZA ESPACIAL",
    "grey": "CINZA ESPACIAL",
    "space gray": "CINZA ESPACIAL",
    "space grey": "CINZA ESPACIAL",
    "cinza espacial": "CINZA ESPACIAL",
    "space black": "PRETO ESPACIAL",
    "preto espacial": "PRETO ESPACIAL",
    "spb": "PRETO ESPACIAL",
    "midnight": "MEIA-NOITE",
    "meia noite": "MEIA-NOITE",
    "meia-noite": "MEIA-NOITE",
    "mindnight": "MEIA-NOITE",
    "mdn": "MEIA-NOITE",
    "starlight": "ESTELAR",
    "estelar": "ESTELAR",
    "stl": "ESTELAR",
    "graphite": "GRAFITE",
    "grafite": "GRAFITE",
    "bordo": "BURGUNDY",
    "bord": "BURGUNDY",
    "bordeaux": "BURGUNDY",
    "burgundy": "BURGUNDY",
    "burgamdy": "BURGUNDY",
    "vinho": "BURGUNDY",
    "glacier": "GLACIER",
    "glacial": "GLACIER",
    "glarceir": "GLACIER",
    "glacie": "GLACIER",
    "night sky": "NIGHT SKY",
    "sky blue": "AZUL",
    "skyblue": "AZUL",
    "mist blue": "AZUL",
    "deep blue": "AZUL",
    "cosmic orange": "LARANJA",
    "cloud white": "BRANCO",
    "light gold": "DOURADO",
    "white gold": "DOURADO",
    "radiant gold": "DOURADO",
    "sage": "VERDE",
    "teal": "VERDE ACINZENTADO",
    "verde acinzentado": "VERDE ACINZENTADO",
    "verde-acinzentado": "VERDE ACINZENTADO",
    "petroleo": "VERDE ACINZENTADO",
    "ultramarine": "AZUL",
    "ultramarino": "AZUL",
    "azul ultramarino": "AZUL",
    "natural": "NATURAL TITÂNIO",
    "natural titanium": "NATURAL TITÂNIO",
    "natural titanio": "NATURAL TITÂNIO",
    "titanio natural": "NATURAL TITÂNIO",
    "black titanium": "PRETO TITÂNIO",
    "titanio preto": "PRETO TITÂNIO",
    "preto titanio": "PRETO TITÂNIO",
    "white titanium": "BRANCO TITÂNIO",
    "titanio branco": "BRANCO TITÂNIO",
    "branco titanio": "BRANCO TITÂNIO",
    "desert": "DESERT TITÂNIO",
    "dessert": "DESERT TITÂNIO",
    "areia": "DESERT TITÂNIO",
    "desert titanium": "DESERT TITÂNIO",
    "desert titanio": "DESERT TITÂNIO",
    "titanio deserto": "DESERT TITÂNIO",
    "blue titanium": "AZUL TITÂNIO",
    "titanio azul": "AZUL TITÂNIO",
    "azul titanio": "AZUL TITÂNIO",
    "sierra blue": "AZUL SIERRA",
    "azul sierra": "AZUL SIERRA",
    "alpine green": "VERDE",
    "verde alpine": "VERDE",
    "deep purple": "ROXO",
    "jet black": "PRETO",
    "jetblack": "PRETO",
    "ardosia": "ARDÓSIA",
    "slate": "ARDÓSIA",
    "rosa gold": "ROSA",
    "rose gold": "ROSA",
    "ouro rosa": "ROSA",
    "dark bronze": "BRONZE",
    "bronze": "BRONZE",
    "blush": "ROSA",
    "bluch": "ROSA",
    "citrus": "VERDE",
    "indigo": "AZUL",
}

MODEL_COLOR_RULES = [
    (re.compile(r"^MAC MINI M4(?: PRO)?$"), {}, {"PRATA"}),
    # Acessórios
    (re.compile(r"\b(?:PENCIL|AIRTAG|FONTE|CARREGADOR|CABO)\b", re.I), {"branco": "BRANCO", "white": "BRANCO"}, {"BRANCO"}),
    (re.compile(r"\b(?:MAGIC\s+MOUSE|MAGIC\s+TRACKPAD|TRACKPAD|MAGIC\s+KEYBOARD|SMART\s+KEYBOARD|TECLADO)\b", re.I),
     {"preto espacial": "PRETO", "space black": "PRETO", "spb": "PRETO", "cinza espacial": "PRETO"}, {"BRANCO", "PRETO", "PRATA"}),

    # iPhones
    (re.compile(r"\bIPHONE\s+DUO\b", re.I),
     {"preto": "NIGHT SKY", "meia-noite": "NIGHT SKY", "night sky": "NIGHT SKY",
      "branco": "BRANCO", "prata": "BRANCO", "estelar": "BRANCO"},
     {"NIGHT SKY", "BRANCO"}),
    (re.compile(r"\bIPHONE\s+18\s+PRO(?:\s+MAX)?\b", re.I),
     {"azul": "GLACIER", "glacier": "GLACIER", "vermelho": "BURGUNDY", "burgundy": "BURGUNDY",
      "preto": "PRETO", "preto espacial": "PRETO", "prata": "PRATA", "branco": "PRATA"},
     {"BURGUNDY", "GLACIER", "PRETO", "PRATA"}),
    (re.compile(r"\bIPHONE\s+17\s+PRO(?:\s+MAX)?\b", re.I),
     {"branco": "PRATA", "prata": "PRATA", "laranja": "LARANJA", "azul": "AZUL"},
     {"LARANJA", "AZUL", "PRATA"}),
    (re.compile(r"\bIPHONE\s+17\s+AIR\b", re.I),
     {"preto espacial": "PRETO", "preto": "PRETO", "branco": "BRANCO", "dourado": "DOURADO", "azul": "AZUL"},
     {"BRANCO", "DOURADO", "PRETO", "AZUL"}),
    (re.compile(r"\bIPHONE\s+17E\b", re.I), {}, {"BRANCO", "PRETO", "ROSA"}),
    (re.compile(r"\bIPHONE\s+17\b", re.I), {"roxo": "LAVANDA"}, {"LAVANDA", "VERDE", "AZUL", "BRANCO", "PRETO"}),
    (re.compile(r"\bIPHONE\s+16\s+PRO(?:\s+MAX)?\b", re.I),
     {"preto": "PRETO TITÂNIO", "preto espacial": "PRETO TITÂNIO", "branco": "BRANCO TITÂNIO",
      "prata": "NATURAL TITÂNIO", "cinza espacial": "NATURAL TITÂNIO", "natural": "NATURAL TITÂNIO",
      "dourado": "DESERT TITÂNIO"},
     {"PRETO TITÂNIO", "BRANCO TITÂNIO", "NATURAL TITÂNIO", "DESERT TITÂNIO"}),
    (re.compile(r"\bIPHONE\s+16E\b", re.I), {}, {"BRANCO", "PRETO"}),
    (re.compile(r"\bIPHONE\s+16(?:\s+PLUS)?\b", re.I),
     {"verde": "VERDE ACINZENTADO"},
     {"AZUL", "VERDE ACINZENTADO", "ROSA", "BRANCO", "PRETO"}),
    (re.compile(r"\bIPHONE\s+15\s+PRO(?:\s+MAX)?\b", re.I),
     {"preto": "PRETO TITÂNIO", "preto espacial": "PRETO TITÂNIO", "branco": "BRANCO TITÂNIO",
      "prata": "NATURAL TITÂNIO", "cinza espacial": "NATURAL TITÂNIO", "azul": "AZUL TITÂNIO"},
     {"PRETO TITÂNIO", "BRANCO TITÂNIO", "NATURAL TITÂNIO", "AZUL TITÂNIO"}),
    (re.compile(r"\bIPHONE\s+15(?:\s+PLUS)?\b", re.I), {}, {"PRETO", "AZUL", "VERDE", "AMARELO", "ROSA"}),
    (re.compile(r"\bIPHONE\s+14\s+PRO(?:\s+MAX)?\b", re.I),
     {"preto espacial": "PRETO", "grafite": "PRETO", "branco": "PRATA"},
     {"PRETO", "PRATA", "DOURADO", "ROXO"}),
    (re.compile(r"\bIPHONE\s+14(?:\s+PLUS)?\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "AZUL", "ROXO", "VERMELHO", "AMARELO"}),
    (re.compile(r"\bIPHONE\s+13\s+PRO(?:\s+MAX)?\b", re.I),
     {"preto": "GRAFITE", "cinza espacial": "GRAFITE", "branco": "PRATA", "azul": "AZUL SIERRA"},
     {"GRAFITE", "PRATA", "DOURADO", "AZUL SIERRA", "VERDE"}),
    (re.compile(r"\bIPHONE\s+13(?:\s+MINI)?\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "AZUL", "ROSA", "VERDE", "VERMELHO"}),
    (re.compile(r"\bIPHONE\s+12\s+PRO(?:\s+MAX)?\b", re.I),
     {"preto": "GRAFITE", "branco": "PRATA"},
     {"GRAFITE", "PRATA", "DOURADO", "AZUL"}),
    (re.compile(r"\bIPHONE\s+12(?:\s+MINI)?\b", re.I), {}, {"PRETO", "BRANCO", "AZUL", "VERDE", "ROXO", "VERMELHO"}),
    (re.compile(r"\bIPHONE\s+11\b", re.I), {}, {"PRETO", "BRANCO", "VERDE", "AMARELO", "ROXO", "VERMELHO"}),
    (re.compile(r"\bIPHONE\s+SE\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "VERMELHO"}),

    # Mac
    (re.compile(r"\bMACBOOK\s+PRO\b.*\bM[1-3]\b", re.I),
     {"preto": "PRETO ESPACIAL", "branco": "PRATA"},
     {"PRETO ESPACIAL", "PRATA", "CINZA ESPACIAL"}),
    (re.compile(r"\bMACBOOK\s+PRO\b", re.I),
     {"preto": "PRETO ESPACIAL", "branco": "PRATA"},
     {"PRETO ESPACIAL", "PRATA"}),
    (re.compile(r"\bMACBOOK\s+AIR\b.*\bM[1-3]\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "CINZA ESPACIAL", "PRATA", "DOURADO"}),
    (re.compile(r"\bMACBOOK\s+AIR\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "PRATA", "AZUL"}),
    (re.compile(r"\bMACBOOK\s+NEO\b", re.I), {}, {"ROSA", "VERDE", "AZUL", "PRATA"}),
    (re.compile(r"\bIMAC\b", re.I), {}, {"AZUL", "PRATA", "ROSA", "VERDE", "AMARELO", "LARANJA", "ROXO"}),
    (re.compile(r"\bMAC\s+(?:MINI|STUDIO)\b", re.I), {}, {"PRATA"}),

    # iPad
    (re.compile(r"\bIPAD\s+PRO\b", re.I),
     {"preto": "PRETO ESPACIAL", "branco": "PRATA"},
     {"PRETO ESPACIAL", "PRATA"}),
    (re.compile(r"\bIPAD\s+AIR\b.*\bM[2-5]\b", re.I),
     {"preto": "CINZA ESPACIAL", "branco": "ESTELAR"},
     {"CINZA ESPACIAL", "ESTELAR", "ROXO", "AZUL"}),
    (re.compile(r"\bIPAD\s+AIR\b", re.I),
     {"preto": "CINZA ESPACIAL", "branco": "PRATA"},
     {"CINZA ESPACIAL", "ESTELAR", "PRATA", "DOURADO", "ROSA", "AZUL", "VERDE"}),
    (re.compile(r"\bIPAD\b.*\b(?:10|11|A16)\b", re.I),
     {"branco": "PRATA"},
     {"AZUL", "ROSA", "PRATA", "AMARELO"}),
    (re.compile(r"\bIPAD\s+MINI\b.*\b(?:7|A17)\b", re.I),
     {"preto": "CINZA ESPACIAL", "branco": "ESTELAR"},
     {"CINZA ESPACIAL", "ESTELAR", "ROXO", "AZUL"}),
    (re.compile(r"\bIPAD\s+MINI\b", re.I),
     {"preto": "CINZA ESPACIAL", "branco": "ESTELAR"},
     {"CINZA ESPACIAL", "ESTELAR", "ROXO", "ROSA", "PRATA"}),
    (re.compile(r"\bIPAD\b.*\b9\b", re.I),
     {"preto": "CINZA ESPACIAL", "branco": "PRATA"},
     {"CINZA ESPACIAL", "PRATA"}),

    # Watch — APENAS cores oficiais da CAIXA (nunca cor de pulseira!)
    (re.compile(r"\bAPPLE\s+WATCH\s+ULTRA\b", re.I),
     {"prata": "NATURAL TITÂNIO", "natural": "NATURAL TITÂNIO", "preto titânio": "PRETO", "preto titanio": "PRETO"},
     {"NATURAL TITÂNIO", "PRETO"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S12\b.*\bCELLULAR\b", re.I),
     {"meia-noite": "PRETO", "preto espacial": "PRETO", "preto brilhante": "PRETO"},
     {"PRETO", "CINZA ESPACIAL", "BRONZE", "DOURADO", "NATURAL TITÂNIO", "BRANCO", "AZUL"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S12\b", re.I),
     {"meia-noite": "PRETO", "preto espacial": "PRETO", "preto brilhante": "PRETO"},
     {"PRETO", "CINZA ESPACIAL", "BRONZE", "DOURADO"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S11\b.*\bCELLULAR\b", re.I),
     {}, {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA", "DOURADO", "NATURAL TITÂNIO", "ARDÓSIA"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S11\b", re.I),
     {}, {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S(?:9|10)\b", re.I),
     {},
     {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA", "DOURADO", "NATURAL TITÂNIO", "ESTELAR", "MEIA-NOITE", "VERMELHO"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+SE\s*3\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+SE\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "PRATA"}),

    # AirPods
    (re.compile(r"\bAIRPODS\s+MAX\b", re.I),
     {"preto": "MEIA-NOITE", "branco": "ESTELAR"},
     {"MEIA-NOITE", "ESTELAR", "AZUL", "ROXO", "LARANJA", "PRATA", "CINZA ESPACIAL", "VERDE", "ROSA"}),
    (re.compile(r"\b(?:AIRPODS|EARPODS)\b", re.I), {}, {"BRANCO"}),
]


def _normalize_and_validate_model_color(product, u_model):
    resolved = offer_color(product)
    if ((resolved == "Cor da caixa a confirmar" and re.search(r"\bWATCH SE\s*3\b", u_model)) or
            (resolved == "Cor a confirmar" and u_model.startswith("SMART KEYBOARD"))):
        product["cor"] = resolved
        return True, "OK"
    import color_guardian
    ok, status, _ = color_guardian.audit_and_heal_color(product, u_model=u_model, allow_learn=False)
    if ok:
        return True, "OK"
    return False, status


def _check_completeness(product, raw_model):
    """
    Exige especificações completas antes de aceitar qualquer item no catálogo:
    modelo exato, capacidade/RAM/polegadas/mm obrigatórios e cor oficial válida para aquele modelo.
    """
    u_model = raw_model.upper().strip()
    source = str(product.get("linhaOriginal") or "")
    if re.search(r"\b(?:ITEL|HOTWAVE?)\b", source, re.I):
        return False, "LINHA_DE_OUTRA_MARCA"
    if re.search(r"\b(?:PENCIL|AIRTAG|FONTE|CARREGADOR|CABO|TECLADO|KEYBOARD|MOUSE|TRACKPAD|AIRPODS|EARPODS)\b", u_model):
        if re.search(r"\d\s*(?:GB|TB)\b", f"{product.get('capacidade')} {u_model} {source}", re.I):
            return False, "ACESSORIO_COM_ARMAZENAMENTO"
    if "AIRTAG" in u_model:
        detail = source.rsplit(") - ", 1)[-1]
        pack = re.search(r"\b([14])\s*PACK\b", detail, re.I)
        if pack and f"{pack[1]} PACK" not in u_model and not (pack[1] == "1" and u_model == "AIRTAG 2"):
            return False, "QUANTIDADE_AIRTAG_CONFLITANTE"
        colors = {_COMMON_COLOR_MAP.get(word, word.upper()) for word in re.findall(
            r"\b(?:branco|white|preto|black|azul|blue|verde|green|rosa|pink|roxo|purple|amarelo|yellow|vermelho|red|laranja|orange)\b",
            _ascii_key(detail))}
        if colors - {"BRANCO"}:
            return False, "COR_CONFLITANTE_NA_LINHA"
    is_cpo = "CPO" in u_model or "CPO" in str(product.get("condicao") or "").upper()
    raw_color = re.sub(r"\s+", " ", str(product.get("cor") or "").strip().upper())
    is_missing_color = (
        raw_color in MISSING_COLOR_TOKENS
        or bool(re.match(r"^(?:MOD\s+A\d+|\d+C\s*(?:CPU|GPU)|\d+GB|0+)$", raw_color))
    )
    is_single_color_accessory = (
        (("AIRPODS" in u_model and "MAX" not in u_model)
         or any(k in u_model for k in ("EARPODS", "PENCIL", "AIRTAG", "FONTE", "CARREGADOR", "CABO")))
        and not is_cpo
    )
    if is_missing_color:
        if u_model in ("MAC MINI M4", "MAC MINI M4 PRO"):
            product["cor"] = "PRATA"  # Único acabamento na ficha Apple 121555.
        elif is_single_color_accessory:
            product["cor"] = "BRANCO"
        else:
            return False, "INCOMPLETO_SEM_COR"

    cap_norm = _norm_cap_for_guardian(product.get("capacidade"))

    if u_model in ("MAC MINI M4", "MAC MINI M4 PRO"):
        pro = u_model.endswith(" PRO")
        allowed_ram = {"24", "48", "64"} if pro else {"16", "24", "32"}
        allowed_disk = {"512GB", "1TB", "2TB", "4TB", "8TB"} if pro else {"256GB", "512GB", "1TB", "2TB"}
        if "/" in cap_norm:
            ram, disk = cap_norm.split("/", 1)
            if ram not in allowed_ram or disk.replace("1024GB", "1TB") not in allowed_disk:
                return False, "CONFIGURACAO_MAC_MINI_INCOMPATIVEL"
        cores = re.findall(r"\b(\d+)C\s*CPU[/ |]+(\d+)C\s*GPU\b", str(product.get("contextoOriginal") or source).upper())
        allowed_cores = {("12", "16"), ("14", "20")} if pro else {("10", "10")}
        if any(pair not in allowed_cores for pair in cores):
            return False, "NUCLEOS_MAC_MINI_INCOMPATIVEIS"

    if any(
        k in u_model
        for k in (
            "TECLADO",
            "MAGIC KEYBOARD",
            "SMART KEYBOARD",
            "MAGIC MOUSE",
            "APPLE MOUSE",
            "MAGIC TRACKPAD",
            "TRACKPAD",
            "APPLE PENCIL",
            "CANETA PENCIL",
            "PENCIL",
            "AIRTAG",
            "FONTE",
            "CARREGADOR",
            "CABO",
        )
    ):
        return _normalize_and_validate_model_color(product, u_model)

    if "IPHONE" in u_model:
        if not re.match(r"^IPHONE\s+(?:(?:1[1-9]|2\d)E?|SE(?:\s*[2-9])?|X[RS]?|[78]|DUO)(?:\s+(?:PRO\s+MAX|PRO|PLUS|AIR|MINI))?$", u_model):
            return False, f"INCOMPLETO_MODELO_IPHONE: {u_model}"
        if not re.match(r"^(?:32|64|128|256|512|1024)GB$|^[124]TB$", cap_norm):
            return False, f"INCOMPLETO_IPHONE_SEM_GB: {u_model}"
        return _normalize_and_validate_model_color(product, u_model)

    if any(k in u_model for k in ("MACBOOK", "IMAC", "MAC MINI", "MAC STUDIO")):
        if not re.search(r"\b(?:M[1-9]|NEO)\b", u_model):
            return False, f"INCOMPLETO_MAC_SEM_CHIP: {u_model}"
        if "MACBOOK" in u_model:
            if not re.search(r"\b(?:PRO|AIR|NEO)\b", u_model):
                return False, f"INCOMPLETO_MACBOOK_SEM_LINHA: {u_model}"
            if not re.search(r'\b(?:13|14|15|16)"', u_model):
                return False, f"INCOMPLETO_MACBOOK_SEM_POLEGADAS: {u_model}"
        spec_src = f"{cap_norm} {str(product.get('linhaOriginal') or '').upper()} {u_model}"
        ram_m = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:GB|G)?\s*(?:RAM|/)", spec_src) or re.search(r"/\s*(8|16|18|24|32|36|48|64|96|128)\b", spec_src)
        ssd_m = re.search(r"(?:^|/|\s)(?:128|256|512|1024)\s*GB\b|\b([124])\s*TB?\b", cap_norm) or re.search(r"(?:/|\s)((?:128|256|512|1024)GB|[124]TB)\b", spec_src)
        if not ram_m or not ssd_m:
            return False, f"INCOMPLETO_MAC_SEM_RAM_OU_SSD: {u_model} ({cap_norm})"
        ssd_clean = re.search(r"((?:128|256|512|1024)GB|[124]TB)", cap_norm)
        if not ssd_clean and ssd_m:
            ssd_txt = ssd_m.group(0).replace("/", "").strip()
            ssd_val = re.sub(r"^(\d+)T$", r"\1TB", ssd_txt)
        else:
            ssd_val = ssd_clean.group(1) if ssd_clean else ""
        if ssd_val and "/" not in cap_norm:
            product["capacidade"] = f"{ram_m.group(1)}/{ssd_val}"
        return _normalize_and_validate_model_color(product, u_model)

    if "IPAD" in u_model:
        ipad_u = re.sub(r"\s+", " ", u_model.replace("-", " ")).strip()
        is_ipad_pro = bool(re.search(r"\bIPAD\s+PRO\b", ipad_u) and re.search(r"\bM\d\b", ipad_u))
        is_ipad_air_m = bool(re.search(r"\bIPAD\s+AIR\b", ipad_u) and re.search(r"\bM[2-5]\b", ipad_u))
        is_ipad_air_legacy = bool(re.search(r"\bIPAD\s+AIR\b", ipad_u) and re.search(r"\b(?:M1|[345](?:\s*GER(?:ACAO)?)?)\b", ipad_u))
        is_ipad_mini = bool(re.search(r"\bIPAD\s+MINI\s*(?:[567]|A17)\b", ipad_u))
        is_ipad_base = bool(re.search(r"\bIPAD\s+(?:(?:9|10|11)(?:TH|\s*GER(?:A\s*O|ACAO)?|\s*A16|\b)|A16\b)", ipad_u))
        if not (is_ipad_pro or is_ipad_air_m or is_ipad_air_legacy or is_ipad_mini or is_ipad_base):
            return False, f"INCOMPLETO_MODELO_IPAD: {u_model}"
        if (
            not is_cpo
            and (is_ipad_pro or is_ipad_air_m)
            and not re.search(r'\b(?:T?(?:11|13)|(?:10\.9|11|12\.9|13)(?:\s*(?:-\s*)?INCH|")?)\b', ipad_u)
        ):
            return False, f"INCOMPLETO_IPAD_SEM_POLEGADAS: {u_model}"
        if not re.match(r"^(?:32|64|128|256|512|1024)GB$|^[124]TB$", cap_norm):
            return False, f"INCOMPLETO_IPAD_SEM_GB: {u_model}"
        return _normalize_and_validate_model_color(product, u_model)

    if "WATCH" in u_model:
        if not re.match(r"^APPLE WATCH\s+(?:ULTRA\s*[2-9]|S(?:1[0-9]|9)|SE(?:\s*[2-9])?)\b", u_model):
            return False, f"INCOMPLETO_WATCH_MODELO_INVALIDO: {u_model}"
        if not re.search(r"\b(?:38|40|41|42|44|45|46|49)\s*MM\b", f"{u_model} {cap_norm}"):
            return False, f"INCOMPLETO_WATCH_SEM_MM: {u_model}"
        return _normalize_and_validate_model_color(product, u_model)

    if "AIRPODS" in u_model or "EARPODS" in u_model:
        if not re.match(r"^(?:AIRPODS\s+(?:PRO\s*[1-3]|MAX\s*[1-9]\s*USB-C|[2-9](?:\s*ANC)?)|EARPODS(?:\s+(?:USB-C|LIGHTNING))?)$", u_model):
            return False, f"INCOMPLETO_AIRPODS_MODELO: {u_model}"
        return _normalize_and_validate_model_color(product, u_model)

    return _normalize_and_validate_model_color(product, u_model)


def validate_product(product, existing_day_products=None, log_anomalies=True, allow_seminovo=False):
    if not product:
        return False, "PRODUTO_NULO", None

    def _reject(log_msg, ret_reason=None):
        if log_anomalies:
            log_anomaly(product, log_msg)
        return False, (ret_reason or log_msg), None

    raw_model = str(product.get("modelo", "")).strip()
    try:
        raw_price = float(product.get("preco", 0))
    except (ValueError, TypeError):
        return False, "PRECO_NAO_NUMERICO", None

    if raw_model:
        try:
            from parser_listas import canonicalize_apple_model
        except ImportError:
            canonicalize_apple_model = None
        if canonicalize_apple_model is not None:
            canonical_model = canonicalize_apple_model(
                raw_model,
                str(product.get("linhaOriginal") or product.get("texto_original") or raw_model) if "WATCH" in raw_model.upper() else str(product.get("texto_original") or raw_model),
            )
            if canonical_model:
                raw_model = canonical_model
                product["modelo"] = canonical_model
            elif canonical_model == "":
                return _reject(f"MODELO_DESCARTADO: {raw_model}", "MODELO_INVALIDO")

    if not raw_model or len(raw_model) < 3:
        return False, "MODELO_INVALIDO", None

    # 1. Checar termos proibidos
    for pat in FORBIDDEN_PATTERNS:
        if pat.search(raw_model):
            return _reject(f"MODELO_PROIBIDO: {raw_model}")

    # 2. Classificação de Seminovo vs Novo / Lacrado
    is_cpo = "CPO" in raw_model.upper() or "CPO" in str(product.get("condicao") or "").upper()
    is_seminovo = _is_seminovo_condition(product.get("condicao", ""), product.get("categoria", ""))
    if is_seminovo and not allow_seminovo:
        return _reject(f"SEMINOVO_REJEITADO: {product.get('condicao')}", "SEMINOVO_BLOQUEADO")
    if is_seminovo and not product.get("condicao"):
        product["condicao"] = "Seminovo"

    # 2.5. Trava estrita de completude (não aceitar itens faltando cor, GB, RAM, mm ou modelo exato)
    complete_ok, complete_reason = _check_completeness(product, raw_model)
    if not complete_ok:
        return _reject(complete_reason)

    # 2.8. Modelos descontinuados que não existem mais novos/lacrados (ex: 11, 12, 13, 14 Plus/Pro/Pro Max)
    if not is_cpo and not is_seminovo and DISCONTINUED_NEW_IPHONE_RE.search(raw_model):
        if allow_seminovo:
            is_seminovo = True
            product["condicao"] = "Seminovo"
        else:
            return _reject(f"MODELO_DESCONTINUADO_NOVO: {raw_model}", "SEMINOVO_BLOQUEADO")

    # 3. Faixa global razoável
    if not math.isfinite(raw_price) or raw_price <= 50 or raw_price > 45000:
        return _reject(f"PRECO_FORA_DA_FAIXA_GLOBAL: R$ {raw_price}", f"PRECO_INVALIDO: R$ {raw_price}")

    # 4. Faixa por modelo (rejeita réplicas/1ª linha/erros absurdos e reclassifica seminovos sem rótulo)
    def _lookup_bound(table):
        for pat, lo, hi in table:
            if pat.search(raw_model):
                return (lo, hi)
        return None

    new_bound = _lookup_bound(SANITY_BOUNDS)
    semi_bound = _lookup_bound(SEMINOVO_SANITY_BOUNDS)

    if is_cpo:
        lo = semi_bound[0] if semi_bound else (new_bound[0] if new_bound else None)
        hi = new_bound[1] if new_bound else (semi_bound[1] if semi_bound else None)
        matched_bound = (lo, hi) if (lo is not None and hi is not None) else None
    elif is_seminovo:
        matched_bound = semi_bound or new_bound
    else:
        matched_bound = new_bound

    if matched_bound and not (matched_bound[0] <= raw_price <= matched_bound[1]):
        raw_cond_low = str(product.get("condicao") or "").strip().lower()
        # Se for iPhone (não Duo) sem condição declarada abaixo do piso de Novo mas dentro do piso de Seminovo, move para Seminovo
        if (
            not is_cpo
            and not is_seminovo
            and allow_seminovo
            and raw_price < matched_bound[0]
            and "IPHONE" in raw_model.upper()
            and "DUO" not in raw_model.upper()
            and raw_cond_low in ("", "condição não informada")
            and semi_bound
            and semi_bound[0] <= raw_price <= semi_bound[1]
        ):
            is_seminovo = True
            product["condicao"] = "Seminovo"
        else:
            return _reject(
                f"PRECO_FORA_DA_FAIXA_DO_MODELO: R$ {raw_price} (esperado R$ {matched_bound[0]} - R$ {matched_bound[1]})"
            )

    sanitized = dict(product)
    sanitized["preco"] = raw_price
    sanitized["condicao"] = product.get("condicao") or "Condição não informada"
    if is_seminovo:
        sanitized["is_seminovo"] = True
    sanitized.pop("alertaFaixaModelo", None)
    return True, "OK", sanitized


def _capacity_to_gb(cap_str):
    s = _norm_cap_for_guardian(cap_str)
    m_tb = re.search(r"(\d+)\s*TB", s)
    if m_tb:
        return int(m_tb.group(1)) * 1024
    m_gb = re.search(r"(\d+)\s*GB", s)
    if m_gb:
        return int(m_gb.group(1))
    return 0


def _has_hidden_seminovo_signal(item):
    raw = unicodedata.normalize(
        "NFKC",
        f"{item.get('contextoOriginal') or ''} {item.get('linhaOriginal') or ''} {item.get('condicao') or ''}",
    ).upper()
    if re.search(r"\b(?:SEMI[\s-]*NOV[OA]S?|VITRINE|SWAP|USAD[OA]S?|GRADE\s*[AB]\+?|[AÁ]S\s*IS|ASIS|ATIVAD[OA]S?|DESLACRAD[OA]S?|OPEN\s*BOX|RECONDICIONAD[OA]S?|BATERIA\s*\d|CICLOS?|(?:30|60|90)\s*DIAS\s*(?:DE\s*)?GARANTIA|GARANTIA\s*(?:DE\s*)?(?:1\s*M[ÊE]S|PELA\s+LOJA|COM\s+FORNECEDOR))\b", raw):
        if not re.search(r"N[ÃA]O\s+(?:A\s+PARTIR\s+DO\s+MOMENTO\s+EM\s+QUE\s+FOI\s+)?ATIVAD[OA]", raw):
            return True
    if re.search(r"(?:^|\s)(?:8\d|9\d)\s*%", raw):
        return True
    return False


def run_guardian_sweep(rows, offer_key_fn=None, log_anomalies=True):
    """
    Vistoria periódica completa (a cada 5 minutos):
    1. Revalida todas as ofertas ativas (regras de completude, 1ª linha, modelos descontinuados e faixas).
    2. Compara divergências de preço entre fornecedores e inversões de capacidade (ex: 512GB mais barato que 256GB).
    3. Entra em módulo de análise para qualquer item suspeito: reclassifica seminovo disfarçado ou remove erro/anomalia.
    """
    def _default_key(it):
        return (
            it.get("data", ""),
            it.get("fornecedor", ""),
            it.get("modelo", ""),
            it.get("capacidade", "-"),
            it.get("cor", "-"),
            it.get("condicao", ""),
        )

    key_fn = offer_key_fn or _default_key
    actions = []
    removed = 0
    reclassified = 0
    deduplicated = 0

    # Fase 1: Revalidação individual + deduplicação
    stage1_map = {}
    for row in rows or []:
        orig_cond = str(row.get("condicao") or "")
        orig_model = str(row.get("modelo") or "")
        orig_color = str(row.get("cor") or "")
        ev_issue = review_quoted_price(row)
        if ev_issue:
            removed += 1
            if log_anomalies:
                log_anomaly(row, f"VISTORIA_5MIN:{ev_issue}")
            if len(actions) < 30:
                actions.append(f"Removido ({ev_issue}): {orig_model} R$ {row.get('preco')} [{row.get('fornecedor')}]")
            continue

        ok, reason, item = validate_product(dict(row), allow_seminovo=True, log_anomalies=False)
        if not ok or not item:
            removed += 1
            if log_anomalies:
                log_anomaly(row, f"VISTORIA_5MIN:{reason}")
            if len(actions) < 30:
                actions.append(f"Removido ({reason}): {orig_model} R$ {row.get('preco')} [{row.get('fornecedor')}]")
            continue

        # Detecta se houve correção automática (ex: modelo descontinuado ou preço de seminovo movido para Seminovo)
        if item.get("condicao") != orig_cond or item.get("modelo") != orig_model or item.get("cor") != orig_color:
            if item.get("condicao") == "Seminovo" and orig_cond != "Seminovo":
                reclassified += 1
                if len(actions) < 30:
                    actions.append(f"Reclassificado para Seminovo: {item.get('modelo')} {item.get('capacidade')} R$ {item.get('preco')} [{item.get('fornecedor')}]")

        k = key_fn(item)
        if k in stage1_map:
            deduplicated += 1
            if str(item.get("hora", "")) >= str(stage1_map[k].get("hora", "")):
                stage1_map[k] = item
        else:
            stage1_map[k] = item

    stage1_list = list(stage1_map.values())

    # Fase 2: Consenso de mercado em produtos NOVOS + detecção de inversão de capacidade (ex: 512GB < mediana do 256GB)
    held_ids = {}

    novo_mc_prices = {}
    novo_tier_prices = {}
    for it in stage1_list:
        if _review_condition_bucket(it.get("condicao"), it.get("modelo")) != "novo":
            continue
        mod = str(it.get("modelo") or "").upper()
        cap = _norm_cap_for_guardian(it.get("capacidade"))
        sid = _supplier_id(it)
        if not sid:
            continue
        novo_mc_prices.setdefault((it.get("data"), mod, cap), {}).setdefault(sid, []).append(float(it["preco"]))
        if mod.startswith("IPHONE "):
            gb = _capacity_to_gb(it.get("capacidade"))
            if gb > 0:
                novo_tier_prices.setdefault((mod, gb), {}).setdefault(sid, []).append(float(it["preco"]))

    mc_medians = {
        k: {sid: _median_of(ps) for sid, ps in sup_map.items()}
        for k, sup_map in novo_mc_prices.items()
    }
    tier_medians = {}
    for (mod, gb), sup_map in novo_tier_prices.items():
        if len(sup_map) >= 2:
            tier_medians.setdefault(mod, {})[gb] = _median_of([_median_of(ps) for ps in sup_map.values()])

    for it in stage1_list:
        if _review_condition_bucket(it.get("condicao"), it.get("modelo")) != "novo":
            continue
        mod = str(it.get("modelo") or "").upper()
        cap = _norm_cap_for_guardian(it.get("capacidade"))
        sid = _supplier_id(it)
        price = float(it["preco"])

        # Checagem 2A: desvio severo contra mediana de fornecedores independentes do mesmo modelo+capacidade
        sup_peers = mc_medians.get((it.get("data"), mod, cap), {})
        peer_vals = [val for psid, val in sup_peers.items() if psid != sid]
        if len(peer_vals) >= 3:
            med = _median_of(peer_vals)
            is_core_device = any(k in mod for k in ("IPHONE", "MACBOOK", "IMAC", "MAC MINI", "MAC STUDIO", "IPAD", "APPLE WATCH"))
            low_threshold = 0.78 if is_core_device else 0.65
            if price <= med * low_threshold:
                pct = round((1 - price / med) * 100)
                held_ids[id(it)] = f"Valor {pct}% abaixo da mediana do modelo (R$ {med:.0f}) — provável seminovo ou erro"
                continue
            if price >= med * 1.45:
                pct = round((price / med - 1) * 100)
                held_ids[id(it)] = f"Valor {pct}% acima da mediana do modelo (R$ {med:.0f}) — erro de digitação"
                continue

        # Checagem 2B: inversão de capacidade em iPhones Novos (ex: 512GB mais barato que 256GB)
        gb = _capacity_to_gb(it.get("capacidade"))
        if mod.startswith("IPHONE ") and gb > 0 and mod in tier_medians:
            lower_medians = [med for l_gb, med in tier_medians[mod].items() if l_gb < gb]
            if lower_medians:
                max_lower_med = max(lower_medians)
                if price < max_lower_med * 0.90:
                    held_ids[id(it)] = f"Inversão de capacidade: {it.get('capacidade')} por R$ {price:.0f} abaixo da mediana de capacidade menor (R$ {max_lower_med:.0f})"

    # Fase 3: Módulo de Análise e Autocorreção para itens divergentes
    final_map = {}
    for it in stage1_list:
        div_reason = held_ids.get(id(it))
        if not div_reason:
            final_map[key_fn(it)] = it
            continue

        # Módulo de Análise: verifica se é seminovo disfarçado ou erro real
        mod_up = str(it.get("modelo") or "").upper()
        raw_cond_low = str(it.get("condicao") or "").strip().lower()
        is_cpo = "CPO" in mod_up or "cpo" in raw_cond_low
        price = float(it.get("preco", 0))
        semi_bound = next(((lo, hi) for pat, lo, hi in SEMINOVO_SANITY_BOUNDS if pat.search(mod_up)), None)

        can_reclassify_semi = (
            not is_cpo
            and "IPHONE" in mod_up
            and "DUO" not in mod_up
            and semi_bound is not None
            and semi_bound[0] <= price <= semi_bound[1]
            and (_has_hidden_seminovo_signal(it) or raw_cond_low in ("", "condição não informada"))
        )

        if can_reclassify_semi:
            fixed = dict(it)
            fixed["condicao"] = "Seminovo"
            fixed["is_seminovo"] = True
            ok_s, _, fixed_sanitized = validate_product(fixed, allow_seminovo=True, log_anomalies=False)
            if ok_s and fixed_sanitized:
                reclassified += 1
                final_map[key_fn(fixed_sanitized)] = fixed_sanitized
                if len(actions) < 30:
                    actions.append(f"Módulo Análise -> Seminovo ({div_reason}): {mod_up} {it.get('capacidade')} R$ {price:.0f} [{it.get('fornecedor')}]")
                continue

        removed += 1
        if log_anomalies:
            log_anomaly(it, f"VISTORIA_5MIN_DIVERGENCIA: {div_reason}")
        if len(actions) < 30:
            actions.append(f"Módulo Análise -> Removido ({div_reason}): {mod_up} {it.get('capacidade')} R$ {price:.0f} [{it.get('fornecedor')}]")

    final_items = list(final_map.values())
    final_items, _ = equalize_catalog_prices(final_items, log_anomalies=False)

    changed = bool(removed or reclassified or deduplicated or len(final_items) != len(rows or []))
    report = {
        "checked": time.strftime("%d/%m/%Y %H:%M:%S"),
        "changed": changed,
        "analyzed": len(rows or []),
        "validAfter": len(final_items),
        "removed": removed,
        "reclassified": reclassified,
        "deduplicated": deduplicated,
        "actions": actions,
    }
    return final_items, report


def compute_dfu_delta(product):
    """
    Regras oficiais de DFU (Ajuste Global de Custo Base):
    - iPhones (Novos / Lacrados): +R$ 100
    - MacBooks & Macs / iMac (Novos / Lacrados): +R$ 200
    - iPads (Novos / Lacrados): +R$ 50
    - Apple Watch (Novos / Lacrados): +R$ 100
    - Isentos (+R$ 0): AirPods, Acessórios Apple, Seminovos, Vitrine, Ativados, ASIS, CPO
    """
    if not isinstance(product, dict):
        return 0
    model = str(product.get("modelo") or "").upper()
    cond = str(product.get("condicao") or "")
    cat = str(product.get("categoria") or "").upper()
    if product.get("is_seminovo") or _is_seminovo_condition(cond, cat):
        return 0
    if "CPO" in model or "CPO" in cond.upper():
        return 0
    if DISCONTINUED_NEW_IPHONE_RE.search(model):
        return 0
    if "AIRPODS" in model or "EARPODS" in model or "AIRPODS" in cat:
        return 0
    if any(k in model for k in ("TECLADO", "KEYBOARD", "MAGIC MOUSE", "APPLE MOUSE", "TRACKPAD", "PENCIL", "AIRTAG", "FONTE", "CARREGADOR", "CABO", "MAGSAFE", "ADAPTADOR", "FOLIO", "CAPA")) or "ACESS" in cat:
        return 0
    if "IMAC" in model or any(k in model for k in ("MACBOOK", "MAC MINI", "MAC STUDIO", "MAC PRO")) or "MAC" in cat:
        return 200
    if "IPAD" in model or "IPAD" in cat:
        return 50
    if "WATCH" in model or "WATCH" in cat:
        return 100
    if "IPHONE" in model or "IPHONE" in cat:
        return 100
    return 0


def classify_vitrine_category(product):
    """Mapeia produto Apple para a categoria oficial da vitrine (IPH, SEMI, MCB, IMAC, IPAD, RLG, PODS, ACSS, CPO)."""
    model = str(product.get("modelo") or "").upper()
    cond = str(product.get("condicao") or "")
    cat = str(product.get("categoria") or "").upper()
    if product.get("is_seminovo") or _is_seminovo_condition(cond, cat):
        return "SEMI"
    if "CPO" in model or "CPO" in cond.upper():
        return "CPO"
    if "EARPODS" in model or any(k in model for k in ("TECLADO", "KEYBOARD", "MAGIC MOUSE", "APPLE MOUSE", "TRACKPAD", "PENCIL", "AIRTAG", "FONTE", "CARREGADOR", "CABO", "MAGSAFE", "ADAPTADOR", "FOLIO", "CAPA")) or "ACESS" in cat:
        return "ACSS"
    if "AIRPODS" in model or "AIRPODS" in cat:
        return "PODS"
    if "IMAC" in model:
        return "IMAC"
    if any(k in model for k in ("MACBOOK", "MAC MINI", "MAC STUDIO", "MAC PRO")) or "MAC" in cat:
        return "MCB"
    if "IPAD" in model or "IPAD" in cat:
        return "IPAD"
    if "WATCH" in model or "WATCH" in cat:
        return "RLG"
    if "IPHONE" in model or "IPHONE" in cat:
        if DISCONTINUED_NEW_IPHONE_RE.search(model):
            return "SEMI"
        return "IPH"
    return "ACSS"
