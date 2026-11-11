import re
import time

# Mapeamento de cores comuns nas listas da Santa Efigênia / Shopping Oriental
COLOR_ALIASES = {
    "BLACK": "PRETO",
    "PRETO": "PRETO",
    "PRETA": "PRETO",
    "BLACK TITANIUM": "PRETO TITÂNIO",
    "TITANIO PRETO": "PRETO TITÂNIO",
    "MIDNIGHT": "MEIA-NOITE",
    "MEIA NOITE": "MEIA-NOITE",
    "MEIA-NOITE": "MEIA-NOITE",
    "SPACE GRAY": "CINZA ESPACIAL",
    "SPACE GREY": "CINZA ESPACIAL",
    "SPACE BLACK": "PRETO ESPACIAL",
    "CINZA": "CINZA",
    "GRAPHITE": "GRAFITE",
    "GRAFITE": "GRAFITE",
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
    "GOLD": "DOURADO",
    "DOURADO": "DOURADO",
    "ROSE": "ROSA",
    "ROSE GOLD": "ROSA",
    "PINK": "ROSA",
    "ROSA": "ROSA",
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
    "IPAD", "APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10",
    "AIRPODS", "EARPODS", "AIRTAG", "APPLE PENCIL", "PENCIL", "MAGIC MOUSE", "MAGIC KEYBOARD",
    "REDMI", "POCO", "XIAOMI", "REALME", "GALAXY", "SAMSUNG", "MOTO ", "MOTOROLA", "INFINIX", "HONOR", "TECNO",
    "PS5", "PLAYSTATION", "XBOX", "NINTENDO", "SWITCH", "JBL", "STARLINK", "GARMIN", "GOPRO", "DJI"
]


def clean_line(line):
    # Remove emojis e caracteres decorativos mantendo letras, números, R$, pontuação básica
    cleaned = re.sub(r"[^\w\s\$\.,/\-\+\(\)%]", " ", line, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_price_from_line(line):
    """
    Extrai o valor de custo no final ou meio da linha (ex: R$ 4.350,00 | 4.350 | 4350 | $ 7.150).
    Ignora números que claramente fazem parte do modelo/capacidade (ex: 128, 256, 512, 16, 15).
    """
    upper = line.upper()
    # Remover temporariamente capacidades (64GB, 128GB, 256GB, 512GB, 8/256, 12/512) para não confundir com preço
    scrubbed = re.sub(r"\b\d{1,2}\s*/\s*\d{2,4}\s*(?:GB|TB)?\b", " ", upper)
    scrubbed = re.sub(r"\b(?:16|32|64|128|256|512|1024)\s*(?:GB|G)\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:1|2|4)\s*TB\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{2,3}\s*%\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:MM|40MM|41MM|42MM|44MM|45MM|46MM|49MM)\b", " ", scrubbed)

    # 1. Procurar padrão explícito com R$ ou $
    m_rs = re.findall(r"(?:R\$|\$)\s*(\d{1,2}\.\d{3}(?:,\d{2})?|\d{3,5}(?:,\d{2})?)", scrubbed)
    if m_rs:
        val = parse_num_br(m_rs[-1])
        if 50 <= val <= 45000:
            return val

    # 2. Procurar número formatado com milhar (ex: 4.350 ou 4.350,00 ou 10.500)
    m_mil = re.findall(r"\b(\d{1,2}\.\d{3}(?:,\d{2})?)\b", scrubbed)
    if m_mil:
        val = parse_num_br(m_mil[-1])
        if 100 <= val <= 45000:
            return val

    # 3. Procurar número inteiro de 3 a 5 dígitos no final da linha (ex: "- 4350" ou "PRETO 4350")
    m_end = re.search(r"(?:[\-\:\=]|(?:\s))(\d{3,5}(?:,\d{2})?)\s*$", scrubbed)
    if m_end:
        val = parse_num_br(m_end.group(1))
        # Evitar confundir anos ou capacidades soltas (128, 256, 512, 2023, 2024, 2025, 2026) se não tiver contexto
        if val not in (128, 256, 512) and 120 <= val <= 45000:
            return val

    return None


def parse_num_br(s):
    s = str(s).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # Se tiver ponto seguido de 3 dígitos (ex: 4.350), remover o ponto de milhar
        if re.match(r"^\d{1,2}\.\d{3}$", s):
            s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


def extract_capacity(text):
    upper = text.upper()
    # Ex: 8/256GB, 12/512GB, 16/512GB
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
    # Ordenar chaves por tamanho decrescente para casar "BLACK TITANIUM" antes de "BLACK"
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


def normalize_model_name(raw_text):
    upper = raw_text.upper()
    # Remover preço do texto
    upper = re.sub(r"(?:R\$|\$)\s*\d{1,2}\.\d{3}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"(?:R\$|\$)\s*\d{3,5}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"\b\d{1,2}\.\d{3}(?:,\d{2})?\b", " ", upper)
    upper = re.sub(r"(?:[\-\:\=])\s*\d{3,5}(?:,\d{2})?\s*$", " ", upper)
    upper = re.sub(r"\s+\d{3,5}\s*$", " ", upper)

    # Padronizar abreviações de iPhone (IP 15 -> IPHONE 15, IPH 16 -> IPHONE 16, PM -> PRO MAX)
    upper = re.sub(r"\b(?:IPH|IP)\s*(1[1-7]|[78]|X[RS]?|SE)\b", r"IPHONE \1", upper)
    upper = re.sub(r"\b(IPHONE\s+1[1-7])\s+PM\b", r"\1 PRO MAX", upper)

    # Remover cores conhecidas do nome do modelo para padronizar o agrupamento
    for key in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        upper = re.sub(r"\b" + re.escape(key) + r"\b", " ", upper)

    # Remover capacidades do nome base para não duplicar
    upper = re.sub(r"\b\d{1,2}\s*/\s*(?:64|128|256|512|1024)\s*(?:GB|G)?\b", " ", upper)
    upper = re.sub(r"\b(?:32|64|128|256|512|1024)\s*(?:GB|G)\b", " ", upper)
    upper = re.sub(r"\b[124]\s*TB\b", " ", upper)

    # Se o número da capacidade (128, 256, 512) ficou solto após IPHONE X ...
    upper = re.sub(r"\b(64|128|256|512)\b", " ", upper)

    # Limpar símbolos extras
    upper = re.sub(r"[^A-Z0-9\s\+\-/]", " ", upper)
    upper = re.sub(r"\s+", " ", upper).strip(" -/+")
    return upper


def classify_category(model_name, full_text, seminovo_flag):
    u = (model_name + " " + full_text).upper()
    if "IPHONE" in u or re.search(r"\bIP\s*1[1-7]\b", u):
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
    for kw in PRODUCT_KEYWORDS:
        if kw in u:
            return True
    return False


def parse_supplier_message(text, supplier_name, supplier_phone, group_name="", date_str=None):
    """
    Recebe o texto bruto de uma mensagem de lista de fornecedor no WhatsApp
    e retorna uma lista de dicionários de produtos estruturados.
    """
    if not text or len(text.strip()) < 6:
        return []

    if not date_str:
        date_str = time.strftime("%d/%m/%Y")
    time_str = time.strftime("%H:%M:%S")

    lines = [clean_line(l) for l in text.splitlines()]
    lines = [l for l in lines if len(l) >= 3]

    parsed_products = []
    current_header_model = ""
    current_header_capacity = "-"
    section_seminovo = False

    for line in lines:
        u = line.upper()

        # Detectar mudança de seção para Seminovos / Swap / Vitrine vs Lacrados / Novos
        if any(k in u for k in ["SEMINOVO", "SEMI NOVO", "SEMI-NOVO", "VITRINE", "SWAP", "AS IS", "USADOS"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = True
                continue
        if any(k in u for k in ["LACRADO", "LACRADOS", "NOVOS", "APPLE NOVO", "LINHA LACRADA"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = False
                continue

        price = extract_price_from_line(line)
        has_product_kw = looks_like_product_header(line)

        # Caso 1: Linha tem nome de produto mas NÃO tem preço -> é um cabeçalho de bloco (ex: "📱 IPHONE 16 PRO MAX 256GB")
        if has_product_kw and price is None:
            current_header_model = normalize_model_name(line)
            cap = extract_capacity(line)
            if cap != "-":
                current_header_capacity = cap
            if is_seminovo(line):
                section_seminovo = True
            continue

        # Caso 2: Linha tem nome de produto E tem preço na mesma linha (ex: "IPHONE 15 128GB PRETO R$ 4.350")
        if has_product_kw and price is not None:
            model = normalize_model_name(line)
            if not model or len(model) < 3:
                continue
            cap = extract_capacity(line)
            color = extract_color(line)
            sem = is_seminovo(line, section_seminovo)
            cat = classify_category(model, line, sem)

            # Atualizar cabeçalho atual caso as linhas seguintes sejam apenas cores
            current_header_model = model
            if cap != "-":
                current_header_capacity = cap

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

        # Caso 3: Linha de cor + preço abaixo de um cabeçalho de produto (ex: "⚫ PRETO R$ 7.150")
        if not has_product_kw and price is not None and current_header_model:
            color = extract_color(line)
            cap_line = extract_capacity(line)
            cap = cap_line if cap_line != "-" else current_header_capacity
            # Se a linha tiver pelo menos uma cor reconhecida ou texto curto de variação
            if color != "-" or len(line) <= 35:
                sem = is_seminovo(line, section_seminovo)
                cat = classify_category(current_header_model, line, sem)
                parsed_products.append({
                    "modelo": current_header_model,
                    "capacidade": cap,
                    "cor": color if color != "-" else normalize_model_name(line) or "PADRÃO",
                    "categoria": cat,
                    "condicao": "Seminovo" if sem else "Novo / Lacrado",
                    "preco": price,
                    "fornecedor": supplier_name,
                    "telefone": supplier_phone,
                    "origem": group_name or supplier_name,
                    "data": date_str,
                    "hora": time_str,
                    "linhaOriginal": f"{current_header_model} {cap} - {line}",
                })

    return parsed_products
