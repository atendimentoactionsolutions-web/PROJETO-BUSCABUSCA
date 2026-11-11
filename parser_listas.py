import base64
import os
import re
import subprocess
import tempfile
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
    "SEMINOVO", "SEMINOVOS", "SEMI NOVO", "SEMI-NOVO", "VITRINE", "MOSTRUARIO", "MOSTRUÁRIO", "SWAP",
    "USADO", "USADOS", "AS IS", "ASIS", "GRADE A", "GRADE A+", "GRADE B", "RECONDICIONADO",
    "ABERTO", "ATIVADO", "SEM CAIXA", "S/ CAIXA", "C/ CAIXA", "COM CAIXA", "DETALHE", "MARCA DE USO",
    "SAUDE DA BATERIA", "SAÚDE DA BATERIA", "CAPACIDADE MAXIMA", "CAPACIDADE MÁXIMA",
    "BAT 8", "BAT 9", "BAT 100", "BATERIA 8", "BATERIA 9", "BATERIA 100",
    "80%", "81%", "82%", "83%", "84%", "85%", "86%", "87%", "88%", "89%",
    "90%", "91%", "92%", "93%", "94%", "95%", "96%", "97%", "98%", "99%", "100% BAT"
]

NOVO_LACRADO_KEYWORDS = [
    "NOVO, LACRADO", "NOVO LACRADO", "NOVOS LACRADOS", "100% LACRADO", "LACRADO DE FABRICA",
    "LACRADO DE FÁBRICA", "GARANTIA DE 1 ANO APPLE", "1 ANO DE GARANTIA", "GARANTIA APPLE 1 ANO",
    "LINHA LACRADA", "TABELA LACRADOS", "APARELHO LACRADO", "CAIXA LACRADA", "LACRADO"
]

PRODUCT_KEYWORDS = [
    "IPHONE", "IPH ", "IP ", "MACBOOK", "MAC MINI", "IMAC", "MAC STUDIO",
    "IPAD", "APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10", "S10 ", "S11 ", "S12 ",
    "AIRPODS", "EARPODS", "AIRTAG", "APPLE PENCIL", "PENCIL", "MAGIC MOUSE", "MAGIC KEYBOARD",
    "REDMI", "POCO", "XIAOMI", "REALME", "GALAXY", "SAMSUNG", "MOTO ", "MOTOROLA", "INFINIX", "HONOR", "TECNO",
    "PS5", "PLAYSTATION", "XBOX", "NINTENDO", "SWITCH", "JBL", "STARLINK", "GARMIN", "GOPRO", "DJI"
]


def strip_tradein_disclaimers(text):
    """
    Remove frases de rodapé como 'Aceitamos seu iPhone usado como parte do pagamento'
    para nunca confundir a condição do produto anunciado com o aviso de troca.
    """
    if not text:
        return ""
    cleaned_lines = []
    for line in text.splitlines():
        u = line.upper()
        if any(
            phrase in u
            for phrase in [
                "ACEITAMOS SEU",
                "SEU IPHONE USADO",
                "SEU USADO",
                "PARTE DO PAGAMENTO",
                "MEDIANTE AVALIA",
                "PEGAMOS SEU",
                "NA TROCA",
            ]
        ):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def analyze_product_image(image_base64):
    """
    Analisa a foto enviada pelo fornecedor (via OCR Tesseract + inspeção visual)
    para extrair texto da imagem e identificar se a foto mostra um produto
    Novo/Lacrado ou Seminovo (ex: print de Saúde da Bateria, aparelho fora da caixa, tabela de seminovos).
    """
    if not image_base64:
        return {"ocr_text": "", "image_condition": None}

    try:
        raw_b64 = re.sub(r"^data:image/\w+;base64,", "", image_base64.strip())
        img_bytes = base64.b64decode(raw_b64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        ocr_text = ""
        try:
            proc = subprocess.run(
                ["tesseract", tmp_path, "stdout", "-l", "por+eng", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            ocr_text = (proc.stdout or "").strip()
        except Exception:
            ocr_text = ""
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        u_ocr = strip_tradein_disclaimers(ocr_text).upper()
        img_cond = None
        if any(k in u_ocr for k in SEMINOVO_KEYWORDS):
            img_cond = "Seminovo"
        elif any(k in u_ocr for k in NOVO_LACRADO_KEYWORDS):
            img_cond = "Novo / Lacrado"

        return {"ocr_text": ocr_text, "image_condition": img_cond}
    except Exception:
        return {"ocr_text": "", "image_condition": None}


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


def is_seminovo(text, section_is_seminovo=False, model_name="", price=None, explicit_novo_in_msg=False):
    """
    Determina com precisão se o item é Seminovo ou Novo/Lacrado analisando:
    1) Palavras-chave na linha (Seminovo, Vitrine, Swap, % bateria, As Is, Grade A/B)
    2) Contexto da seção ou mensagem completa (e OCR da foto se houver)
    3) Verificação de sanidade para modelos descontinuados (iPhone 11, 12, 13, 14 Pro) ou preços típicos de seminovo
    """
    upper = strip_tradein_disclaimers(text).upper()
    for kw in SEMINOVO_KEYWORDS:
        if kw in upper:
            return True

    if any(kw in upper for kw in NOVO_LACRADO_KEYWORDS):
        return False

    if section_is_seminovo:
        return True

    # Verificação de segurança para modelos de iPhone que só circulam como Seminovo/Vitrine/Swap se não estiver explícito "LACRADO"
    m_up = (model_name or "").upper()
    if "IPHONE" in m_up and not explicit_novo_in_msg:
        if any(x in m_up for x in ["IPHONE 11", "IPHONE 12", "IPHONE 13 PRO", "IPHONE 14 PRO", "IPHONE XR", "IPHONE XS"]):
            return True
        if price is not None:
            if "IPHONE 13" in m_up and price <= 2800:
                return True
            if "IPHONE 14" in m_up and price <= 3100:
                return True
            if "IPHONE 15 PRO MAX" in m_up and price <= 5200:
                return True
            if "IPHONE 15" in m_up and "PRO" not in m_up and price <= 3300:
                return True

    return False


def is_shorthand_iphone_line(line):
    """
    Detecta cabeçalhos curtos de iPhone onde o fornecedor omite a palavra IPHONE ou GB,
    ex: '15 128GB', '15 128', '15 PRO MAX 256GB', '16 PRO 128', '17 AIR 256GB', '16E 128'
    """
    u = line.upper().strip()
    if re.match(
        r"^(?:IPHONE\s+|IP\s+|IPH\s+)?(1[1-8](?:E)?)\s*(?:(AIR|MINI|PLUS|PRO\s*MAX|PRO|PM|DUO))?\s*(?:(64|128|256|512|1024)\s*(?:GB|G)?|([124])\s*TB)?(?:\s+(?:LL/A|HN/A|CH/A|JA/A|BR/A|ZP/A|AH/A|E-SIM|ESIM|ANATEL))?\s*$",
        u,
    ):
        if any(x in u for x in ["PRO", "MAX", "PLUS", "AIR", "MINI", "16E", "17E", "GB", "TB", "64", "128", "256", "512"]):
            return True
    return False


def normalize_model_name(raw_text):
    upper = raw_text.upper().strip()
    upper = re.sub(r"^\bLINHA\s+", "", upper)

    upper = re.sub(r"(?:R\$|\$)\s*\d{1,2}\.\d{3}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"(?:R\$|\$)\s*\d{3,5}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"\b\d{1,2}\.\d{3}(?:,\d{2})?\b", " ", upper)
    upper = re.sub(r"(?:[\-\:\=])\s*\d{3,5}(?:,\d{2})?\s*$", " ", upper)
    upper = re.sub(r"\s+\d{3,5}\s*$", " ", upper)

    if re.match(r"^(1[1-8](?:E)?)\b", upper):
        upper = "IPHONE " + upper

    upper = re.sub(r"\b(?:IPH|IP)\s*(1[1-8](?:E)?|[78]|X[RS]?|SE)\b", r"IPHONE \1", upper)
    upper = re.sub(r"\b(IPHONE\s+1[1-8])\s+PM\b", r"\1 PRO MAX", upper)

    for key in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        upper = re.sub(r"\b" + re.escape(key) + r"\b", " ", upper)

    for kw in SEMINOVO_KEYWORDS:
        if len(kw) >= 4:
            upper = re.sub(r"\b" + re.escape(kw) + r"\b", " ", upper)

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


def parse_supplier_message(text, supplier_name, supplier_phone, group_name="", date_str=None, image_base64=None):
    """
    Lê a mensagem completa (e analisa a foto via OCR se houver) para separar com precisão absoluta
    o que é Novo / Lacrado e o que é Seminovo, em todas as categorias.
    """
    img_info = analyze_product_image(image_base64) if image_base64 else {"ocr_text": "", "image_condition": None}
    combined_raw = (text or "").strip()
    if img_info.get("ocr_text"):
        combined_raw = combined_raw + "\n" + img_info["ocr_text"]

    if not combined_raw or len(combined_raw.strip()) < 6:
        return []

    if not date_str:
        date_str = time.strftime("%d/%m/%Y")
    time_str = time.strftime("%H:%M:%S")

    # Remover avisos de troca ("Aceitamos seu iPhone usado como parte do pagamento...")
    clean_full_text = strip_tradein_disclaimers(combined_raw)
    upper_all = clean_full_text.upper()

    # Analisar condição global da mensagem + foto
    has_explicit_seminovo = any(k in upper_all for k in SEMINOVO_KEYWORDS) or (img_info.get("image_condition") == "Seminovo")
    has_explicit_novo = any(k in upper_all for k in NOVO_LACRADO_KEYWORDS) or (img_info.get("image_condition") == "Novo / Lacrado")

    # Se a mensagem inteira é sobre Seminovo (e não é uma lista mista com seção "LACRADOS")
    section_seminovo = bool(has_explicit_seminovo and not has_explicit_novo)

    lines = [clean_line(l) for l in clean_full_text.splitlines()]
    lines = [l for l in lines if len(l) >= 2]

    parsed_products = []
    current_header_model = ""
    current_header_capacity = "-"
    current_header_color = "-"

    for line in lines:
        u = line.upper()

        # Mudança de seção explícita dentro de uma lista grande
        if any(k in u for k in ["SEMINOVO", "SEMINOVOS", "SEMI NOVO", "SEMI-NOVO", "VITRINE", "SWAP", "AS IS", "USADOS", "TABELA SEMINOVOS", "LINHA SEMINOVOS"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = True
                continue
        if any(k in u for k in ["LACRADO", "LACRADOS", "NOVOS", "APPLE NOVO", "LINHA LACRADA", "GARANTIA 1 ANO", "NOVO LACRADO"]):
            if extract_price_from_line(line) is None and not looks_like_product_header(line):
                section_seminovo = False
                continue

        if re.match(r"^LINHA\s+IPHONE\s+\d+\s*$", u):
            current_header_model = normalize_model_name(u)
            current_header_capacity = "-"
            current_header_color = "-"
            continue

        price = extract_price_from_line(line)
        has_product_kw = looks_like_product_header(line)

        # Caso 1: Cabeçalho de modelo sem preço (ex: "IPHONE 16 PRO MAX 256GB" ou "15 128" ou "iPhone 15 Pro Max 512GB Natural")
        if has_product_kw and price is None:
            current_header_model = normalize_model_name(line)
            current_header_capacity = extract_capacity(line)
            current_header_color = extract_color(line)
            if any(k in u for k in SEMINOVO_KEYWORDS):
                section_seminovo = True
            elif any(k in u for k in NOVO_LACRADO_KEYWORDS):
                section_seminovo = False
            continue

        # Caso 2: Linha tem nome de produto E tem preço na mesma linha
        if has_product_kw and price is not None:
            model = normalize_model_name(line)
            if not model or len(model) < 3:
                continue
            cap = extract_capacity(line)
            color = extract_color(line)
            sem = is_seminovo(line, section_seminovo, model, price, has_explicit_novo)
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
                sem = is_seminovo(line, section_seminovo, current_header_model, price, has_explicit_novo)
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
