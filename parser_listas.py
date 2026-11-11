import re
import time

# Mapeamento de cores comuns nas listas da Santa Efigênia / Shopping Oriental
COLOR_ALIASES = {
    "BLACK": "PRETO",
    "PRETO": "PRETO",
    "PRETA": "PRETO",
    "CAIXA PRETA": "PRETO",
    "BLACK TITANIUM": "PRETO TITÂNIO",
    "TITANIO PRETO": "PRETO TITÂNIO",
    "MIDNIGHT": "MEIA-NOITE",
    "MEIA NOITE": "MEIA-NOITE",
    "MEIA-NOITE": "MEIA-NOITE",
    "NIGHT SKY": "NIGHT SKY",
    "SPACE GRAY": "CINZA ESPACIAL",
    "SPACE GREY": "CINZA ESPACIAL",
    "SPACE BLACK": "PRETO ESPACIAL",
    "CINZA": "CINZA",
    "GRAPHITE": "GRAFITE",
    "GRAFITE": "GRAFITE",
    "STAR WHITE": "BRANCO",
    "CLOUD WHITE": "BRANCO",
    "WHITE": "BRANCO",
    "BRANCO": "BRANCO",
    "BRANCA": "BRANCO",
    "WHITE TITANIUM": "BRANCO TITÂNIO",
    "STARLIGHT": "ESTELAR",
    "ESTELAR": "ESTELAR",
    "SILVER": "PRATA",
    "PRATA": "PRATA",
    "NATURAL": "NATURAL TITÂNIO",
    "NATURAL TITANIUM": "NATURAL TITÂNIO",
    "TITANIO NATURAL": "NATURAL TITÂNIO",
    "DESERT": "DESERT TITÂNIO",
    "DESERT TITANIUM": "DESERT TITÂNIO",
    "TITANIO DESERT": "DESERT TITÂNIO",
    "LIGHT GOLD": "DOURADO",
    "GOLD": "DOURADO",
    "DOURADO": "DOURADO",
    "SOFT PINK": "ROSA",
    "ROSE": "ROSA",
    "ROSE GOLD": "ROSA",
    "PINK": "ROSA",
    "ROSA": "ROSA",
    "LAVANDA": "LAVANDA",
    "LAVENDER": "LAVANDA",
    "GLACIER": "GLACIER",
    "BURGUNDY": "BURGUNDY",
    "DEEP BLUE": "AZUL",
    "BLUE": "AZUL",
    "AZUL": "AZUL",
    "ULTRAMARINE": "AZUL ULTRAMARINO",
    "PACIFIC BLUE": "AZUL PACÍFICO",
    "SIERRA BLUE": "AZUL SIERRA",
    "BLUE TITANIUM": "AZUL TITÂNIO",
    "SKY BLUE": "AZUL",
    "GREEN": "VERDE",
    "VERDE": "VERDE",
    "ALPINE GREEN": "VERDE ALPINO",
    "MINT": "VERDE MENTA",
    "TEAL": "VERDE ACINZENTADO",
    "PURPLE": "ROXO",
    "ROXO": "ROXO",
    "LILAS": "ROXO",
    "DEEP PURPLE": "ROXO PROFUNDO",
    "RED": "VERMELHO",
    "VERMELHO": "VERMELHO",
    "YELLOW": "AMARELO",
    "AMARELO": "AMARELO",
    "COSMIC ORANGE": "LARANJA",
    "ORANGE": "LARANJA",
    "LARANJA": "LARANJA",
    "TITANIUM": "TITÂNIO",
}

SEMINOVO_KEYWORDS = [
    "SEMINOVO", "SEMI NOVO", "SEMI-NOVO", "VITRINE", "SWAP",
    "USADO", "AS IS", "ASIS", "GRADE A", "GRADE B", "RECONDICIONADO",
    "BAT 8", "BAT 9", "BAT 100", "BATERIA 8", "BATERIA 9", "BATERIA 100",
    "85%", "86%", "87%", "88%", "89%", "90%", "91%", "92%", "93%", "94%", "95%", "98%", "99%"
]

PRODUCT_KEYWORDS = [
    "IPHONE", "IPH ", "IP ", "MACBOOK", "MAC MINI", "IMAC", "MAC STUDIO",
    "IPAD", "APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10", "S10 ", "S11 ", "S12 ",
    "AIRPODS", "EARPODS", "AIRTAG", "APPLE PENCIL", "PENCIL", "MAGIC MOUSE", "MAGIC KEYBOARD",
    "REDMI", "POCO", "XIAOMI", "REALME", "GALAXY", "SAMSUNG", "MOTO ", "MOTOROLA", "INFINIX", "HONOR", "TECNO",
    "PS5", "PLAYSTATION", "XBOX", "NINTENDO", "SWITCH", "JBL", "STARLINK", "GARMIN", "GOPRO", "DJI"
]


def clean_line(line):
    cleaned = re.sub(r"[^\w\s\$\.,/\-\+\(\)%]", " ", line, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_price_from_line(line):
    upper = line.upper()
    scrubbed = re.sub(r"\b\d{1,2}\s*/\s*\d{2,4}\s*(?:GB|TB)?\b", " ", upper)
    scrubbed = re.sub(r"\b(?:16|32|64|128|256|512|1024)\s*(?:GB|G)\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:1|2|4)\s*TB\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{2,3}\s*%\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:MM|40MM|41MM|42MM|44MM|45MM|46MM|49MM)\b", " ", scrubbed)

    m_rs = re.findall(r"(?:R\$|\$)\s*(\d{1,2}\.\d{3}(?:,\d{2})?|\d{3,5}(?:,\d{2})?)", scrubbed)
    if m_rs:
        val = parse_num_br(m_rs[-1])
        if 50 <= val <= 45000:
            return val

    m_mil = re.findall(r"\b(\d{1,2}\.\d{3}(?:,\d{2})?)\b", scrubbed)
    if m_mil:
        val = parse_num_br(m_mil[-1])
        if 100 <= val <= 45000:
            return val

    m_end = re.search(r"(?:[\-\:\=]|(?:\s))(\d{3,5}(?:,\d{2})?)\s*$", scrubbed)
    if m_end:
        val = parse_num_br(m_end.group(1))
        if val not in (64, 128, 256, 512, 1024, 2023, 2024, 2025, 2026) and 120 <= val <= 45000:
            return val

    return None


def parse_num_br(s):
    s = str(s).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        if re.match(r"^\d{1,2}\.\d{3}$", s):
            s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


def extract_capacity(text):
    upper = text.upper()
    m_ram_rom = re.search(r"\b(\d{1,2}\s*/\s*(?:64|128|256|512|1024))\s*(?:GB|G)?\b", upper)
    if m_ram_rom:
        return m_ram_rom.group(1).replace(" ", "") + "GB"

    m_tb = re.search(r"\b([124])\s*TB\b", upper)
    if m_tb:
        return f"{m_tb.group(1)}TB"

    m_gb = re.search(r"\b(32|64|128|256|512|1024)\s*(?:GB|G)?\b", upper)
    if m_gb:
        return f"{m_gb.group(1)}GB"

    return "-"


def extract_color(text):
    upper = text.upper()
    for key in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", upper):
            return COLOR_ALIASES[key]
    return "-"


def is_seminovo(text, section_is_seminovo=False):
    if section_is_seminovo:
        return True
    upper = text.upper()
    for kw in SEMINOVO_KEYWORDS:
        if kw in upper:
            return True
    return False


def is_shorthand_iphone_line(line):
    """
    Detecta cabeçalhos curtos de iPhone onde o fornecedor omite a palavra IPHONE,
    ex: '15 128GB', '15 PRO MAX 256GB', '16 PRO 128GB', '17 AIR 256GB', '16E 128GB'
    """
    u = line.upper().strip()
    if re.match(
        r"^(?:IPHONE\s+|IP\s+|IPH\s+)?(1[1-8](?:E)?)\s*(?:(AIR|MINI|PLUS|PRO\s*MAX|PRO|PM|DUO))?\s*(?:(64|128|256|512|1024)\s*(?:GB|G)?|([124])\s*TB)?(?:\s+(?:LL/A|HN/A|CH/A|JA/A|BR/A|ZP/A|AH/A|E-SIM|ESIM|ANATEL))?\s*$",
        u,
    ):
        # Precisa ter variante (PRO, PRO MAX, PLUS, AIR, E) ou capacidade explícita (128GB, 256GB...)
        if any(x in u for x in ["PRO", "MAX", "PLUS", "AIR", "MINI", "16E", "17E", "GB", "TB", "64", "128", "256", "512"]):
            return True
    return False


def normalize_model_name(raw_text):
    upper = raw_text.upper().strip()

    # Ignorar se for apenas "LINHA IPHONE 15" (título de seção genérico)
    upper = re.sub(r"^\bLINHA\s+", "", upper)

    # Remover preço do texto
    upper = re.sub(r"(?:R\$|\$)\s*\d{1,2}\.\d{3}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"(?:R\$|\$)\s*\d{3,5}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"\b\d{1,2}\.\d{3}(?:,\d{2})?\b", " ", upper)
    upper = re.sub(r"(?:[\-\:\=])\s*\d{3,5}(?:,\d{2})?\s*$", " ", upper)
    upper = re.sub(r"\s+\d{3,5}\s*$", " ", upper)

    # Se começa direto com 11..18 (ex: "15 PRO MAX 256GB" ou "16 128GB"), prefixar IPHONE
    if re.match(r"^(1[1-8](?:E)?)\b", upper):
        upper = "IPHONE " + upper

    # Padronizar abreviações de iPhone (IP 15 -> IPHONE 15, IPH 16 -> IPHONE 16, PM -> PRO MAX)
    upper = re.sub(r"\b(?:IPH|IP)\s*(1[1-8](?:E)?|[78]|X[RS]?|SE)\b", r"IPHONE \1", upper)
    upper = re.sub(r"\b(IPHONE\s+1[1-8])\s+PM\b", r"\1 PRO MAX", upper)

    # Remover cores conhecidas do nome do modelo para padronizar o agrupamento
    for key in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        upper = re.sub(r"\b" + re.escape(key) + r"\b", " ", upper)

    # Remover capacidades do nome base para não duplicar
    upper = re.sub(r"\b\d{1,2}\s*/\s*(?:64|128|256|512|1024)\s*(?:GB|G)?\b", " ", upper)
    upper = re.sub(r"\b(?:32|64|128|256|512|1024)\s*(?:GB|G)\b", " ", upper)
    upper = re.sub(r"\b[124]\s*TB\b", " ", upper)
    upper = re.sub(r"\b(64|128|256|512)\b", " ", upper)

    upper = re.sub(r"[^A-Z0-9\s\+\-/]", " ", upper)
    upper = re.sub(r"\s+", " ", upper).strip(" -/+")
    return upper


def classify_category(model_name, full_text, seminovo_flag):
    u = (model_name + " " + full_text).upper()
    if "IPHONE" in u or re.search(r"\bIP\s*1[1-8]\b", u):
        return "iPhones Seminovos" if seminovo_flag else "iPhones"
    if any(k in u for k in ["MACBOOK", "MAC MINI", "IMAC", "MAC STUDIO"]):
        return "MacBook"
    if "IPAD" in u:
        return "iPad"
    if any(k in u for k in ["APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10"]):
        return "Apple Watch"
    if any(k in u for k in ["AIRPODS", "EARPODS"]):
        return "AirPods"
    if any(k in u for k in ["REDMI", "POCO", "XIAOMI", "REALME", "GALAXY", "SAMSUNG", "MOTO ", "MOTOROLA", "INFINIX", "HONOR", "TECNO", "NOTE 13", "NOTE 14", "S23", "S24", "S25"]):
        return "Androids"
    return "Acessórios & Outros"


def looks_like_product_header(line):
    u = line.upper()
    if is_shorthand_iphone_line(u):
        return True
    for kw in PRODUCT_KEYWORDS:
        if kw in u:
            return True
    return False


def parse_supplier_message(text, supplier_name, supplier_phone, group_name="", date_str=None):
    if not text or len(text.strip()) < 6:
        return []

    if not date_str:
        date_str = time.strftime("%d/%m/%Y")
    time_str = time.strftime("%H:%M:%S")

    lines = [clean_line(l) for l in text.splitlines()]
    lines = [l for l in lines if len(l) >= 2]

    parsed_products = []
    current_header_model = ""
    current_header_capacity = "-"
    current_header_color = "-"
    upper_all = text.upper()
    section_seminovo = ("SEMINOVO" in upper_all and "NOVO, LACRADO" not in upper_all and "LACRADO" not in upper_all)

    for line in lines:
        u = line.upper()

        if any(k in u for k in ["SEMINOVO", "SEMI NOVO", "SEMI-NOVO", "VITRINE", "SWAP", "AS IS", "USADOS"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = True
                continue
        if any(k in u for k in ["LACRADO", "LACRADOS", "NOVOS", "APPLE NOVO", "LINHA LACRADA"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = False
                continue

        # Ignorar linhas que são apenas "LINHA IPHONE 15" sem capacidade nem variante
        if re.match(r"^LINHA\s+IPHONE\s+\d+\s*$", u):
            current_header_model = normalize_model_name(u)
            current_header_capacity = "-"
            current_header_color = "-"
            continue

        price = extract_price_from_line(line)
        has_product_kw = looks_like_product_header(line)

        # Caso 1: Cabeçalho de modelo sem preço (ex: "📱 IPHONE 16 PRO MAX 256GB" ou "📱 iPhone 15 Pro Max 512GB Natural")
        if has_product_kw and price is None:
            current_header_model = normalize_model_name(line)
            current_header_capacity = extract_capacity(line)
            current_header_color = extract_color(line)
            if is_seminovo(line):
                section_seminovo = True
            continue

        # Caso 2: Linha tem nome de produto E tem preço na mesma linha
        if has_product_kw and price is not None:
            model = normalize_model_name(line)
            if not model or len(model) < 3:
                continue
            cap = extract_capacity(line)
            color = extract_color(line)
            sem = is_seminovo(line, section_seminovo)
            cat = classify_category(model, line, sem)

            current_header_model = model
            current_header_capacity = cap
            current_header_color = color

            parsed_products.append({
                "modelo": model,
                "capacidade": cap,
                "cor": color,
                "categoria": cat,
                "condicao": "Seminovo" if sem else "Novo / Lacrado",
                "preco": price,
                "fornecedor": supplier_name,
                "telefone": supplier_phone,
                "origem": group_name or supplier_name,
                "data": date_str,
                "hora": time_str,
                "linhaOriginal": line,
            })
            continue

        # Caso 3: Linha de cor + preço ou "Valor: R$ ..." abaixo de um cabeçalho de produto
        if not has_product_kw and price is not None and current_header_model:
            color = extract_color(line)
            if color == "-" and current_header_color != "-":
                color = current_header_color
            cap_line = extract_capacity(line)
            cap = cap_line if cap_line != "-" else current_header_capacity
            if color != "-" or len(line) <= 35:
                sem = is_seminovo(line, section_seminovo)
                cat = classify_category(current_header_model, line, sem)
                norm_line = normalize_model_name(line)
                if norm_line in ("VALOR", "PRECO", "POR", "A VISTA", "AVISTA", "PIX", ""):
                    final_color = color if color != "-" else "PADRÃO"
                else:
                    final_color = color if color != "-" else norm_line
                parsed_products.append({
                    "modelo": current_header_model,
                    "capacidade": cap,
                    "cor": final_color,
                    "categoria": cat,
                    "condicao": "Seminovo" if sem else "Novo / Lacrado",
                    "preco": price,
                    "fornecedor": supplier_name,
                    "telefone": supplier_phone,
                    "origem": group_name or supplier_name,
                    "data": date_str,
                    "hora": time_str,
                    "linhaOriginal": f"{current_header_model} {cap} ({final_color}) - {line}",
                })

    return parsed_products
