import base64
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata

os.environ.setdefault("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):
    time.tzset()

# Mapeamento de cores comuns nas listas da Santa Efigênia / Shopping Oriental
COLOR_ALIASES = {
    "BLACK": "PRETO",
    "PRETO": "PRETO",
    "PRETA": "PRETO",
    "CAIXA PRETA": "PRETO",
    "JETBLACK": "PRETO",
    "JET BLACK": "PRETO",
    "BLACK TITANIUM": "PRETO TITÂNIO",
    "TITANIO PRETO": "PRETO TITÂNIO",
    "MIDNIGHT": "MEIA-NOITE",
    "MINDNIGHT": "MEIA-NOITE",
    "MIDNGHT": "MEIA-NOITE",
    "MEIA NOITE": "MEIA-NOITE",
    "MEIA-NOITE": "MEIA-NOITE",
    "NIGHT SKY": "NIGHT SKY",
    "SPACE GRAY": "CINZA ESPACIAL",
    "SPACE GREY": "CINZA ESPACIAL",
    "SPACEGRAY": "CINZA ESPACIAL",
    "SPACEGREY": "CINZA ESPACIAL",
    "SPACE BLACK": "PRETO ESPACIAL",
    "SPACEBLACK": "PRETO ESPACIAL",
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
    "NATURAL CARBON": "PRATA",
    "NATURAL": "NATURAL TITÂNIO",
    "NATURAL TITANIUM": "NATURAL TITÂNIO",
    "TITANIO NATURAL": "NATURAL TITÂNIO",
    "DESERT": "DESERT TITÂNIO",
    "DESSERT": "DESERT TITÂNIO",
    "DESERTO": "DESERT TITÂNIO",
    "DESERT TITANIUM": "DESERT TITÂNIO",
    "TITANIO DESERT": "DESERT TITÂNIO",
    "TITÂNIO DESERTO": "DESERT TITÂNIO",
    "TITANIO DESERTO": "DESERT TITÂNIO",
    "DARK BRONZE": "BRONZE",
    "BRONZE": "BRONZE",
    "LIGHT GOLD": "DOURADO",
    "WHITE GOLD": "DOURADO",
    "RADIANT GOLD": "DOURADO",
    "GOLD": "DOURADO",
    "DOURADO": "DOURADO",
    "SOFT PINK": "ROSA",
    "ROSE": "ROSA",
    "ROSA GOLD": "ROSA",
    "OURO ROSA": "ROSA",
    "SLATE": "ARDÓSIA",
    "ARDÓSIA": "ARDÓSIA",
    "ROSE GOLD": "ROSA",
    "PINK": "ROSA",
    "ROSA": "ROSA",
    "BLUSH": "ROSA",
    "LAVANDA": "LAVANDA",
    "LAVENDER": "LAVANDA",
    "GLACIER": "GLACIER",
    "BURGUNDY": "BURGUNDY",
    "DEEP BLUE": "AZUL",
    "BLUE": "AZUL",
    "AZUL": "AZUL",
    "INDIGO": "AZUL",
    "ÍNDIGO": "AZUL",
    "ULTRAMARINE": "AZUL ULTRAMARINO",
    "PACIFIC BLUE": "AZUL PACÍFICO",
    "SIERRA BLUE": "AZUL SIERRA",
    "BLUE TITANIUM": "AZUL TITÂNIO",
    "SKY BLUE": "AZUL",
    "SKYBLUE": "AZUL",
    "GREEN": "VERDE",
    "VERDE": "VERDE",
    "CITRUS": "VERDE",
    "CÍTRUS": "VERDE",
    "ALPINE GREEN": "VERDE ALPINO",
    "MINT": "VERDE MENTA",
    "TEAL": "VERDE ACINZENTADO",
    "PURPLE": "ROXO",
    "ROXO": "ROXO",
    "LILAS": "ROXO",
    "DEEP PURPLE": "ROXO PROFUNDO",
    "ROXO PROFUNDO": "ROXO PROFUNDO",
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
    "SEMINOVO", "SEMINOVOS", "SEMI NOVO", "SEMI-NOVO", "SEMI NOVOS", "SEMI-NOVOS",
    "VITRINE", "MOSTRUARIO", "MOSTRUÁRIO", "SWAP",
    "USADO", "USADOS", "AS IS", "ASIS", "ÁSIS", "GRADE A", "GRADE A+", "GRADE B", "RECONDICIONADO",
    "ABERTO", "CAIXA ABERTA", "OPEN BOX", "DESLACRADO", "DESLACRADOS", "SEM LACRE",
    "ATIVADO", "ATIVADOS", "SEM CAIXA", "S/ CAIXA", "C/ CAIXA", "COM CAIXA", "DETALHE", "MARCA DE USO",
    "COM MENSAGEM GENU", "MENSAGEM GENUINA", "MENSAGEM GENUÍNA",
    "30 DIAS GARANTIA", "30 DIAS DE GARANTIA", "60 DIAS DE GARANTIA", "GARANTIA DE 60 DIAS",
    "GARANTIA 1MES", "GARANTIA 1 MES", "GARANTIA 1 MÊS", "GARANTIA PELA LOJA", "GARANTIA COM FORNECEDOR",
    "PREMIUM 1", "1ª LINHA", "1A LINHA", "PRIMEIRA LINHA", "ORIGINAL CHINA", "LINHA PREMIUM",
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
    "IPHONE", "IPH ", "IP ", "MACBOOK", "MAC BOOK", "MAC AIR", "MAC PRO", "MAC NEO", "MAC MINI", "IMAC", "MAC STUDIO",
    "IPAD", "APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "APPLE SE", "APPLE S1", "APPLE ULTRA", "ULTRA 2", "ULTRA 3", "ULTRA 4", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10", "S10 ", "S11 ", "S12 ", "T-REX", "AMAZFIT",
    "AIRPODS", "EARPODS", "AIRTAG", "APPLE PENCIL", "PENCIL", "APPLE MOUSE", "MAGIC MOUSE", "MAGIC KEYBOARD", "SMART KEYBOARD", "TECLADO", "MAGIC TRACKPAD", "TRACKPAD", "FONTE ORIGINAL", "CABO ORIGINAL",
    "REDMI", "POCO", "XIAOMI", "REALME", "GALAXY", "SAMSUNG", "MOTO ", "MOTOROLA", "INFINIX", "HONOR", "TECNO", "HOTWAV", "ITEL ", "OUKITEL",
    "PS5", "PLAYSTATION", "XBOX", "NINTENDO", "SWITCH", "JBL", "STARLINK", "GARMIN", "GOPRO", "DJI",
    "RAY-BAN", "RAYBAN", "SMART BRACELET", "BRACELET", "DYSON", "ALEXA", "ECHO"
]


def strip_tradein_disclaimers(text):
    """
    Remove frases de rodapé como 'Aceitamos seu iPhone usado como parte do pagamento'
    para nunca confundir a condição do produto anunciado com o aviso de troca.
    Normaliza fontes Unicode matemáticas (ex: 𝗕𝗔𝗧𝗘𝗥𝗜𝗔 𝟵𝟮%) via NFKC.
    """
    if not text:
        return ""
    norm_text = unicodedata.normalize("NFKC", text)
    cleaned_lines = []
    for line in norm_text.splitlines():
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


NON_ORIGINAL = re.compile(
    r"\b(?:[12]\s*[ªºa°]?\s*linha|primeira\s*linha|segunda\s*linha|linha\s+(?:premium|a{1,3}\+?)|"
    r"premium\s+1|top\s*line|qualidade\s+original|tipo\s+original|igual\s+(?:ao\s+)?original|"
    r"id[eê]ntic[oa]|original\s+china|super\s*clone|clones?|aaa\+?|r[eé]plicas?|paralel[oa]s?|"
    r"similares?|similar|gen[eé]ric[oa]s?|oem|c[oó]pias?|fakes?|fals[oa]s?|foxconn|"
    r"n[aã]o\s+origina(?:l|is))\b",
    re.I,
)
SECTION_BREAK = "FIM DE SECAO EXCLUIDA"


def exclude_non_original_sections(text):
    """Descarta blocos declarados não originais antes da extração local ou por IA."""
    kept = []
    blocked = False
    for line in text.splitlines():
        title = clean_line(line)
        if NON_ORIGINAL.search(title):
            has_price = bool(re.search(r"(?:R\$|\$)\s*\d|\b\d{2,5}(?:[.,]\d{2})?\s*$", title))
            if not has_price or (
                re.match(r"^(?:acess[oó]rios|produtos)\s+", title, re.I)
                and NON_ORIGINAL.fullmatch(re.sub(r"^\S+\s+", "", title).strip(" -:"))
            ):
                blocked = True
            kept.append(SECTION_BREAK)
            continue
        if blocked and re.fullmatch(r"(?:acess[oó]rios?\s+(?:apple\s+)?originais|produtos\s+(?:apple\s+)?originais|originais(?:\s+apple)?|lacrados?)", title, re.I):
            blocked = False
        if not blocked:
            kept.append(line)
    return "\n".join(kept)


GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


def call_gemini_api(prompt, image_base64=None):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return ""

    parts = [{"text": prompt}]
    if image_base64:
        raw_b64 = re.sub(r"^data:image/\w+;base64,", "", image_base64.strip())
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": raw_b64}})

    payload_str = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1}
    })

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        curl_timeout = "25" if image_base64 else "12"
        sub_timeout = 28 if image_base64 else 14
        try:
            proc = subprocess.run(
                [
                    "curl", "-4", "-s", "-m", curl_timeout, url,
                    "-H", "Content-Type: application/json",
                    "-H", f"x-goog-api-key: {api_key}",
                    "-d", "@-"
                ],
                input=payload_str,
                capture_output=True,
                text=True,
                timeout=sub_timeout,
            )
            if proc.returncode != 0 or not proc.stdout:
                continue
            data = json.loads(proc.stdout)
            candidates = data.get("candidates") or []
            if candidates:
                out_parts = candidates[0].get("content", {}).get("parts") or []
                for p in out_parts:
                    if p.get("text"):
                        return p["text"].strip()
        except Exception:
            continue
    return ""


def _is_clear_ocr_text(text):
    """
    Trava anti-confusão para fallback OCR (Tesseract):
    descarta qualquer texto confuso, borrado, com ruído excessivo, não-original (1ª linha/réplica)
    ou ambíguo entre Novo/Lacrado e Seminovo para nunca gerar bug de preço, cor ou condição.
    """
    raw = (text or "").strip()
    if len(raw) < 12:
        return False
    if NON_ORIGINAL.search(raw):
        return False
    visible = [ch for ch in raw if not ch.isspace()]
    if not visible:
        return False
    good = sum(1 for ch in visible if ch.isalnum() or ch in ".,:/-$%()+\"'")
    if (good / len(visible)) < 0.72:
        return False
    u = strip_tradein_disclaimers(raw).upper()
    has_apple = any(k in u for k in ("IPHONE", "MACBOOK", "IPAD", "WATCH", "AIRPODS", "AIRTAG", "PENCIL", "IMAC", "MAC MINI", "MAGIC"))
    has_price = bool(re.search(r"(?:R\$|\$)\s*\d{2,5}|\b[1-9]\d{2,4}(?:[.,]\d{2})?\b", u))
    if not (has_apple and has_price):
        return False
    has_semi = any(k in u for k in SEMINOVO_KEYWORDS)
    has_new = any(k in u for k in NOVO_LACRADO_KEYWORDS)
    # Se a imagem mistura rótulos conflitantes (Novo e Seminovo juntos no OCR), descarta por segurança
    if has_semi and has_new:
        return False
    return True


def analyze_product_image(image_base64):
    """
    Analisa a foto enviada pelo fornecedor (via Gemini Vision API + fallback OCR Tesseract)
    para extrair texto da imagem e identificar se a foto mostra um produto
    Novo/Lacrado, CPO, Caixa Aberta/Deslacrado ou Seminovo.
    Regra de ouro: se a imagem for confusa, borrada, ambígua ou suspeita de 1ª linha/réplica, DESCARTA.
    """
    if not image_base64:
        return {"ocr_text": "", "image_condition": None}

    try:
        raw_b64 = re.sub(r"^data:image/\w+;base64,", "", image_base64.strip())
        if os.environ.get("GEMINI_API_KEY", "").strip():
            prompt = (
                "Analise esta imagem enviada por um fornecedor Apple no atacado.\n"
                "TRAVA DE SEGURANÇA CANÔNICA E ANTI-CONFUSÃO (OBRIGATÓRIO):\n"
                "1. Identifique a condição exata na primeira linha: escreva exatamente 'LACRADOS' se forem produtos novos lacrados originais Apple, "
                "'CPO' se forem Apple CPO, 'DESLACRADO' se for caixa aberta/open box/sem lacre, ou 'SEMINOVO' se for usado/vitrine/as-is/bateria.\n"
                "2. Ignore e DESCARTE totalmente: Android, Xiaomi, Realme, Garmin, videogames, capas, películas ('pelle'), "
                "qualquer item 1ª linha / réplica / paralelo / similar / OEM / genérico / Foxconn / original china, e itens ativados/seminovos/open box.\n"
                "3. REGRA NA DÚVIDA DESCARTE: se a imagem estiver borrada, cortada, confusa, ambígua, misturar novo com seminovo sem separação clara, "
                "não deixar claro se o produto é Apple 100% original, ou se modelo/cor/armazenamento/preço não estiverem 100% legíveis, "
                "NUNCA chute nem adivinhe — descarte a linha confusa (ou responda apenas VAZIO se a imagem inteira for confusa).\n"
                "4. Use EXATAMENTE os nomes canônicos oficiais sem sufixos (NUNCA escreva LL/A, HN/A, FÍSICO, chip, Anatel, '18 Duo', cores ou códigos A3xxx no modelo):\n"
                "   - Dobrável Apple chama-se SEMPRE 'IPHONE DUO' (NUNCA 'IPHONE 18 DUO').\n"
                "   - Exemplos válidos:\n"
                "     LACRADOS\n"
                "     IPHONE DUO 256GB PRETO R$ 13100\n"
                "     IPHONE 18 PRO MAX 256GB PRATA R$ 7800\n"
                "     IPHONE 17 PRO MAX 512GB LARANJA R$ 8350\n"
                "     MACBOOK AIR M5 13 16/512GB MEIA-NOITE R$ 8100\n"
                "     APPLE WATCH ULTRA 4 49MM PRETO R$ 5050\n"
                "5. Liste apenas UMA oferta por linha no formato: MODELO CAPACIDADE COR R$ PRECO.\n"
                "6. Se não houver tabela de preços Apple Original Novo/CPO 100% legível com valores reais, responda apenas VAZIO."
            )
            gemini_txt = call_gemini_api(prompt, raw_b64)
            if gemini_txt:
                if "VAZIO" in gemini_txt.upper() or NON_ORIGINAL.search(gemini_txt):
                    return {"ocr_text": "", "image_condition": None}
                u_gem = strip_tradein_disclaimers(gemini_txt).upper()
                img_cond = None
                if any(k in u_gem for k in SEMINOVO_KEYWORDS):
                    img_cond = "Seminovo"
                elif "CPO" in u_gem:
                    img_cond = "CPO"
                elif any(k in u_gem for k in NOVO_LACRADO_KEYWORDS):
                    img_cond = "Novo / Lacrado"
                return {"ocr_text": gemini_txt, "image_condition": img_cond}

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

        if not _is_clear_ocr_text(ocr_text):
            return {"ocr_text": "", "image_condition": None}

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
    norm = (
        unicodedata.normalize("NFKC", line or "")
        .replace("”", '"')
        .replace("“", '"')
        .replace("″", '"')
        .replace("_", " ")
        .replace("*", " ")
        .replace("💲", "$")
        .replace("💰", "$")
        .replace("💵", "$")
    )
    cleaned = re.sub(r'[^\w\s\$\.,/\-\+\(\)%"]', " ", norm, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# O limite final impede que "4.999,90" seja aceito parcialmente como "4.999".
MONEY_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:[.,]\d{2})?)(?!\d|[.,]\d)"
CURRENCY_PRICE = re.compile(r"\$\.?\s*(" + MONEY_NUMBER + r")")
PROMOTION = re.compile(r"\bde\s*(?:R?\$\s*)?(" + MONEY_NUMBER +
                       r")\s*[,;–-]?\s*por\s*(?:R?\$\s*)?(" + MONEY_NUMBER + r")", re.I)


def extract_price_from_line(line):
    upper = line.upper()
    scrubbed = re.sub(r"\b\d{1,2}\s*/\s*\d{2,4}\s*(?:GB|TB)?\b", " ", upper)
    scrubbed = re.sub(r"\b(?:16|32|64|128|256|512|1024)\s*(?:GB|G)\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:1|2|4)\s*TB\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{2,3}\s*%\b", " ", scrubbed)
    scrubbed = re.sub(r"\b(?:MM|40MM|41MM|42MM|44MM|45MM|46MM|49MM)\b", " ", scrubbed)

    promotion = PROMOTION.search(scrubbed)
    amounts = CURRENCY_PRICE.findall(scrubbed)
    if promotion or amounts:
        val = parse_num_br(promotion[2] if promotion else amounts[-1])
        return val if 50 <= val <= 45000 else None

    m_mil = re.findall(r"(?:\b|\()(\d{1,2}[\.,]\d{3}(?:,\d{2})?)(?:\b|\))", scrubbed)
    if m_mil:
        val = parse_num_br(m_mil[-1])
        if 100 <= val <= 45000:
            return val

    m_end = re.search(r"(?:[\-\:\=\(]|(?:\s))(\d{3,5}(?:,\d{2})?)\)?\s*$", scrubbed)
    if m_end:
        val = parse_num_br(m_end.group(1))
        if val not in (64, 128, 256, 512, 1024, 2023, 2024, 2025, 2026) and 120 <= val <= 45000:
            return val

    return None


def parse_num_br(s):
    s = str(s).strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?", s):
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        if re.match(r"^\d{1,2}\.\d{3}$", s):
            s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


def extract_capacity(text):
    upper = CURRENCY_PRICE.sub(" ", text.upper())
    ram = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:GB)?[\s-]*RAM\b", upper)
    if ram:
        remaining = upper[:ram.start()] + " " + upper[ram.end():]
        disk = re.findall(r"\b(128|256|512|1024)\s*GB\b|\b([1248])\s*TB\b", remaining)
        if len(set(disk)) == 1:
            gb, tb = disk[0]
            return f"{ram[1]}/{gb}GB" if gb else f"{ram[1]}/{tb}TB"
    m_plus_tb = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:GB|G|RAM)?\s*[\+/]\s*([124])\s*TB?\b", upper)
    if m_plus_tb:
        return f"{m_plus_tb.group(1)}/{m_plus_tb.group(2)}TB"

    m_plus_gb = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*(?:GB|G|RAM)?\s*[\+/]\s*(64|128|256|512|1024)\s*(?:GB|G)?\b", upper)
    if m_plus_gb:
        return f"{m_plus_gb.group(1)}/{m_plus_gb.group(2)}GB"

    m_ram_rom = re.search(r"\b(\d{1,2}\s*/\s*(?:64|128|256|512|1024))\s*(?:GB|G)?\b", upper)
    if m_ram_rom:
        return m_ram_rom.group(1).replace(" ", "") + "GB"

    m_tb = re.search(r"\b([124])\s*TB?\b", upper)
    if m_tb:
        return f"{m_tb.group(1)}TB"

    m_gb_all = re.findall(r"\b(32|64|128|256|512|1024)\s*(?:GB|G)?\b", upper)
    if m_gb_all:
        return f"{m_gb_all[-1]}GB"

    return "-"


def mac_mini_capacity(line):
    text = CURRENCY_PRICE.sub(" ", line.upper())
    # Fornecedores também escrevem SSD antes da RAM em linhas de variantes.
    reverse = re.search(r"\b(256|512|1024)\s*GB(?:\s*/\s*|\s+)(8|16|24|32|48|64)(?:\s*GB|\s*RAM)\b", text)
    if reverse:
        return f"{reverse[2]}/{reverse[1]}GB"
    pair = re.search(r"\b(8|16|24|32|48|64)\s*G(?:B)?\s+(256|512|1024)\s*GB\b", text)
    if pair:
        return f"{pair[1]}/{pair[2]}GB"
    return extract_capacity(line)


def extract_color(text):
    upper = text.upper()
    has_band_words = bool(re.search(
        r"\b(?:ALPINE|ALP\.?|TRAIL|OCEAN|MILANESE|MILANESA|SPORT|SOLO|LINK)\s*(?:LOOP|BAND|BRACELET)\b|\bPULSEIRA\b",
        upper,
    ))
    if "WATCH" in upper or "ULTRA" in upper or has_band_words:
        # Remove descrições de pulseira (incluindo parenteses tipo "(BURGUNDY TRAIL LOOP M/L)") para nunca confundir pulseira com cor da caixa
        upper = re.sub(
            r"\([^)]*\b(?:ALPINE|ALP\.?|TRAIL|OCEAN|MILANESE|MILANESA|SPORT|SOLO|LOOP|BAND|PULSEIRA|BRACELET)\b[^)]*\)",
            " ",
            upper,
        )
        upper = re.sub(
            r"\b(?:BURGUNDY|BORD[OÔ]|BORDEAUX|VINHO|INDIGO|ORANGE|LARANJA|GREEN|VERDE|BLUE|AZUL|ANCHOR\s+BLUE|DENIM|NAVY|BEIGE|TAN|CHARCOAL|OLIVE|TERRACOTTA|YELLOW|AMARELO|PINK|ROSA|PLUM|ROXO|PURPLE|RED|VERMELHO|WHITE|BRANCO|BLACK|PRETO)?\s*"
            r"(?:ALPINE|ALP\.?|TRAIL|OCEAN|MILANESE|MILANESA|SPORT|SOLO|LINK)\s*(?:LOOP|BAND|BRACELET)\b(?:\s*(?:S/M|M/L|P/M|M/G|[SML]))?",
            " ",
            upper,
        )
        upper = re.sub(r"\bPULSEIRA\b(?:\s+(?:DE\s+)?(?:SILICONE|ESPORTIVA|ALPINE|TRAIL|OCEAN|MILANESA|TECIDO|COURO|\w+)){0,3}", " ", upper)
        # Se há "BLACK TI" / "TI BLACK" / "NATURAL TI" / "CAIXA PRETA" após limpar pulseira, prioriza a cor da CAIXA
        if re.search(r"\b(?:BLACK\s*TI(?:TANIUM)?|TI(?:TANIUM)?\s*BLACK|CAIXA\s+PRET[AO])\b", upper):
            return "PRETO"
        if re.search(r"\b(?:NATURAL\s*TI(?:TANIUM)?|TI(?:TANIUM)?\s*NATURAL|CAIXA\s+NATURAL)\b", upper):
            return "NATURAL TITÂNIO"
    for key in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", upper):
            return COLOR_ALIASES[key]
    return "-"


def strip_warranty_activation_disclaimer(text):
    """Remove frases explicativas de garantia Apple onde 'ativado' não significa aparelho usado."""
    u = (text or "").upper().replace("_", " ")
    u = re.sub(r"N[ÃA]O\s+(?:A\s+PARTIR\s+DO\s+MOMENTO\s+EM\s+QUE\s+FOI\s+)?ATIVAD[OA]S?", "", u)
    u = re.sub(r"AP[ÓO]S\s+A\s+ATIVA[ÇC][ÃA]O(?:\s+DO\s+APARELHO)?", "", u)
    u = re.sub(r"CONTAR\s+(?:DO|A\s+PARTIR\s+DO)\s+MOMENTO\s+EM\s+QUE\s+SAI\s+DA\s+F[ÁA]BRICA", "", u)
    return u


def _has_used_battery_or_partial_warranty(text):
    u = (text or "").upper()
    if "🔋" in u:
        return True
    if re.search(r"\bGARANTIA\s+(?:DA\s+)?(?:APPLE\s+)?AT[EÉ]\b|\bGARANTIA\s+APPLE\s+\d{2}/\d{2}", u):
        return True
    if re.search(r"\bCICLOS?\b|\bBATERIA\s*(?:\d{2,3}\s*%?|DE\s+\d{2,3})|\bBAT\.?\s*\d{2,3}", u):
        return True
    if re.search(r"(?:^|\s)(?:8\d|9\d)\s*%", u):
        return True
    if re.search(r"(?:^|\s)100\s*%(?!\s*(?:LACRAD|NOVO|ORIGINAL|GARANTIA))", u):
        return True
    return False


def condition_from_text(text):
    """Only explicit supplier statements establish condition; price is not evidence."""
    u = strip_warranty_activation_disclaimer(unicodedata.normalize("NFKC", text or ""))
    # Títulos combinados de abertura (ex: "LISTA IPHONE LACRADO & SEMI NOVOS") iniciam na seção Lacrados
    if re.search(r"(?<!N[ÃA]O\s)\bLACRAD[OA]S?\b", u) and re.search(r"\b(?:SEMI[ -]?NOV[OA]S?|VITRINE|USAD[OA]S?)\b", u):
        return "Novo / Lacrado"
    if _has_used_battery_or_partial_warranty(u):
        return "Seminovo"
    checks = [
        (r"\b[AÁ]S\s*IS\b", "ASIS"),
        (r"(?<![A-Z])CPO\b", "CPO"),
        (r"\b(?:DESLACRAD[OA]S?|ABERT[OA]S?|CAIXA\s+ABERTA|OPEN\s*BOX|SEM\s+LACRE|N[ÃA]O\s+LACRAD[OA]S?)\b", "Deslacrado"),
        (r"\bATIVAD[OA]S?\b", "Ativado"),
        (r"\b(?:SEMI[ -]?NOV[OA]S?|USAD[OA]S?|VITRINE|MOSTRU[AÁ]RIO|SWAP|GRADE\s*[AB]\+?|RECONDICIONAD[OA]|COM\s+MENSAGEM\s+GENU[IÍ]NA|(?:30|60|90)\s*DIAS\s*(?:DE\s*)?GARANTIA|GARANTIA\s*(?:DE\s*)?(?:1\s*M[ÊE]S|(?:30|60|90)\s*DIAS|PELA\s+LOJA|COM\s+(?:A\s+)?LOJA|COM\s+FORNECEDOR)|PREMIUM\s+1|1[ªA]\s+LINHA|PRIMEIRA\s+LINHA|LINHA\s+PREMIUM|ORIGINAL\s+CHINA)\b", "Seminovo"),
        (r"\b(?:LACRAD[OA]S?|LINHA\s+LACRADA)\b", "Novo / Lacrado"),
        (r"\bNOV[OA]S?\b", "Novo — lacre não informado"),
    ]
    for pattern, condition in checks:
        if re.search(pattern, u):
            return condition
    return None


def is_seminovo(text, section_is_seminovo=False, model_name="", price=None, explicit_novo_in_msg=False):
    """
    Determina com precisão se o item é Seminovo ou Novo/Lacrado analisando:
    1) Palavras-chave na linha (Seminovo, Vitrine, Swap, % bateria, As Is, Grade A/B, Open Box, Ativado, garantia até)
    2) Contexto da seção ou mensagem completa (e OCR da foto se houver)
    3) Verificação de sanidade para modelos descontinuados (iPhone 11, 12, 13, 14 Plus/Pro/Pro Max) ou preços típicos de seminovo
    """
    upper = strip_warranty_activation_disclaimer(strip_tradein_disclaimers(text))
    if re.search(r"(?<![A-Z])CPO\b", upper) or "CPO" in (model_name or "").upper():
        return False
    if _has_used_battery_or_partial_warranty(upper):
        return True
    for kw in SEMINOVO_KEYWORDS:
        if kw in upper:
            return True

    if section_is_seminovo:
        return True

    m_up = (model_name or "").upper()
    # Modelos de iPhone que não existem mais novos/lacrados (apenas Seminovo ou CPO)
    if "IPHONE" in m_up and re.search(r"\bIPHONE\s+(?:[78]|X[RS]?|SE(?:\s*[23])?|11|12|13|14\s+(?:PLUS|PRO))\b", m_up):
        return True

    if any(kw in upper for kw in NOVO_LACRADO_KEYWORDS) or re.search(r"\bGARANTIA(?:\s+DE)?\s+(?:1\s+ANO\s+(?:DA\s+)?)?APPLE(?!\s+AT[EÉ])", upper):
        return False

    # Verificação de segurança para modelos de iPhone com preço abaixo da faixa de Novo/Lacrado (sem declaração explícita)
    if "IPHONE" in m_up and not explicit_novo_in_msg:
        if price is not None:
            if "IPHONE 14" in m_up and price <= 3100:
                return True
            if "IPHONE 15 PRO MAX" in m_up and price <= 5200:
                return True
            if "IPHONE 15 PRO" in m_up and price <= 4400:
                return True
            if "IPHONE 15" in m_up and "PRO" not in m_up and price <= 3450:
                return True
            if "IPHONE 16 PRO MAX" in m_up and price <= 5700:
                return True
            if "IPHONE 16 PRO" in m_up and price <= 4900:
                return True
            if "IPHONE 16" in m_up and "PRO" not in m_up and "16E" not in m_up and price <= 3950:
                return True
            if "IPHONE 17 PRO MAX" in m_up and price <= 7100:
                return True
            if "IPHONE 17 PRO" in m_up and price <= 6700:
                return True
            if "IPHONE 17" in m_up and "PRO" not in m_up and "17E" not in m_up and "AIR" not in m_up and price <= 4700:
                return True

    return False


def is_shorthand_iphone_line(line):
    """
    Detecta cabeçalhos curtos de iPhone onde o fornecedor omite a palavra IPHONE ou GB,
    incluindo múltiplos sufixos regionais/SIM (ex: '18 PRO 256 LL', '17 PRO MAX 256GB LL E-SIM',
    '17 256GB HN COM CHIP', '16E 128', 'DOU 512GB ESIM').
    """
    u = clean_line(line).upper()
    u = re.sub(r"(?:R\$|\$)\s*\d{1,2}[\.,]\d{3}(?:,\d{2})?", " ", u)
    u = re.sub(r"(?:R\$|\$)\s*\d{3,5}(?:,\d{2})?", " ", u)
    u = re.sub(r"\b\d{1,2}[\.,]\d{3}(?:,\d{2})?\b", " ", u)
    u = re.sub(r"[\(\)\[\]]", " ", u)
    for alias in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        u = re.sub(r"\b" + re.escape(alias) + r"\b", " ", u)
    u = re.sub(
        r"\b(?:(?:LL|HN|AH|CH|ZA|ZP|JA|BR|BZ|BE|VC|QL|ZD|LZ|KH|TH|TA|AE|AA)(?:/A)?|E-SIM|ESIM|ANATEL|VIRTUAL|F[IÍ]SICO|FISICO|NANO[\s-]*SIM|DUAL[\s-]*SIM|COM\s+CHIP|SEM\s+CHIP|1\s*CHIP(?:\s*\+\s*1)?|CHIP|LACRAD[OA]S?|NOV[OA]S?|ATIVAD[OA]S?|CPO|202[0-9])\b",
        " ",
        u,
    )
    u = re.sub(r"\s+", " ", u).strip(" -/+")
    if re.match(
        r"^(?:IPHONE\s+|IP\s+|IPH\s+)?(?:1[1-8]E?|SE(?:\s*[23])?|X[RS]?|[78]|DUO|DOU|FOLD)\s*(?:(?:AIR|MINI|PLUS|PRO\s*MAX|PRO|PM|DUO|DOU|FOLD))?\s*(?:(?:64|128|256|512|1024)\s*(?:GB|G)?|[124]\s*TB?)?$",
        u,
    ):
        if any(x in u for x in ["PRO", "MAX", "PLUS", "AIR", "MINI", "DUO", "DOU", "FOLD", "16E", "17E", "PM", "GB", "G", "TB", "T", "64", "128", "256", "512", "1024"]):
            return True
    return False


POWER_ACCESSORY = re.compile(
    r"^(?:(?:KIT|COMBO|APPLE|ORIGINAL|USB[- ]?C|LIGHTNING|MAGSAFE|\d+\s*W)\s+)*"
    r"(?:CABOS?|FONTES?|CARREGADOR(?:ES)?|CABLES?|CHARGERS?|POWER ADAPTERS?|"
    r"ADAPTADOR(?:ES)?\s+(?:DE\s+)?(?:ENERGIA|TOMADA|CORRENTE|ALIMENTA[ÇC][ÃA]O))\b", re.I)


def excluded_power_accessory(text):
    return bool(POWER_ACCESSORY.match(clean_line(text).lstrip(" .-()/+")))


def canonicalize_apple_model(model_name, full_line=""):
    """
    Trava de Seguranca Canonica: converte qualquer variacao ou ruido de lista
    no nome canonico exato da Apple para impedir duplicidade de cards.
    Retorna "" caso o texto contenha lixo nao-Apple / OCR corrompido.
    """
    u = re.sub(r"\s+", " ", (model_name or "").upper()).strip()
    if not u:
        return ""
    if any(bad in u for bad in ("PELLE", "CAPINHA", "PELICULA", "OURA RING", "GARMIN", "AMAZFIT", "T-REX", "HAYLOU", "HUAWEI", "HOTWAV", "ITEL ", "OUKITEL", "XBOX", "PLAYSTATION", "NINTENDO", "RAY-BAN", "RAYBAN", "BRACELET", "TABLET GO")):
        return ""

    if excluded_power_accessory(u):
        return ""
    # 1. Acessorios Apple (antes de iPad/Mac/Watch)
    if any(k in u for k in ["TECLADO", "MAGIC KEYBOARD", "SMART KEYBOARD", "MAGIC MOUSE", "APPLE MOUSE", "MAGIC TRACKPAD", "TRACKPAD", "APPLE PENCIL", "CANETA PENCIL", "PENCIL", "AIRTAG"]):
        acc = u
        acc = re.sub(r"\bM\s+GICO\b", "MAGICO", acc)
        acc = re.sub(r"\bNUM(?:ERA\s+O|\s*RICO|ERICO)\b", "NUMERICO", acc)
        acc = re.sub(r"\bTRAN\s+ADO\b", "TRANCADO", acc)
        acc = re.sub(r"\bCANETA\s+PENCIL\b", "APPLE PENCIL", acc)
        acc = re.sub(r"^PENCIL\b", "APPLE PENCIL", acc)
        acc = re.sub(r"\bUSBC\b|\bUSB\s+C\b", "USB-C", acc)
        acc = re.sub(r"\b(?:00|WI-?FI)\b", " ", acc)
        if "TECLADO MAGICO" in acc or "MAGIC KEYBOARD" in acc:
            if "MACBOOK" in acc or "TOUCH ID" in acc:
                return "MAGIC KEYBOARD TOUCH ID NUMERICO" if "NUMERICO" in acc else "MAGIC KEYBOARD TOUCH ID"
            if "FOLIO" in acc:
                return "MAGIC KEYBOARD FOLIO"
            if "12.9" in acc or "12 9" in acc:
                return 'MAGIC KEYBOARD IPAD 12.9"'
            if "AIR" in acc:
                if "13" in acc:
                    return 'MAGIC KEYBOARD IPAD AIR 13" M2/M3/M4'
                if "11" in acc:
                    return 'MAGIC KEYBOARD IPAD AIR 11" M2/M3/M4'
            if "13" in acc:
                return 'MAGIC KEYBOARD IPAD PRO 13" M4/M5'
            if "11" in acc:
                return 'MAGIC KEYBOARD IPAD PRO 11" M4/M5'
        if "SMART KEYBOARD" in acc:
            return "SMART KEYBOARD IPAD"
        if "MAGIC TRACKPAD" in acc or "TRACKPAD" in acc:
            return "MAGIC TRACKPAD"
        if "MAGIC MOUSE" in acc:
            return "MAGIC MOUSE 2 USB-C" if "USB-C" in acc else "MAGIC MOUSE 2"
        if "APPLE PENCIL" in acc:
            if "PRO" in acc:
                return "APPLE PENCIL PRO"
            if "USB" in acc:
                return "APPLE PENCIL USB-C"
            if re.search(r"\b2G?\b", acc):
                return "APPLE PENCIL 2"
            if re.search(r"\b1\b|ADAPTADOR", acc):
                return "APPLE PENCIL 1"
        if "AIRTAG" in acc:
            is_gen2 = bool(re.search(r"\b(?:2\s*G(?:ERACAO|ER)?|GEN\s*2)\b|^AIRTAG\s+2\b", acc))
            is_4p = bool(re.search(r"\b4\s*PACK\b|\b4PACK\b|\bCOM\s*4\b|\bPACK\s*COM\s*4\b", acc))
            if is_gen2 and is_4p:
                return "AIRTAG 2 GERACAO 4 PACK"
            if is_4p:
                return "AIRTAG 4 PACK"
            if is_gen2:
                return "AIRTAG 2"
            return "AIRTAG 1 PACK"
        return re.sub(r"\s+", " ", acc).strip(" -/+")

    # 2. iPhones (inclusive IPHONE DUO — NUNCA IPHONE 18 DUO)
    if "IPHONE" in u or re.search(r"\b(?:(?:1[1-9]|2\d)(?:E)?|DUO|DOU)\b", u) and "IPAD" not in u and "WATCH" not in u and "MAC" not in u and "AIRPODS" not in u:
        u_ip = u
        u_ip = re.sub(r"\bIPHONE\s*/\s*(1[1-9]|2\d)\b", r"IPHONE \1", u_ip)
        u_ip = re.sub(r"\bIPHONE\s*(1[6-9]|2\d)\s+E\b", r"IPHONE \1E", u_ip)
        u_ip = re.sub(r"\b(\d{2})\s*PROMAX\b", r"\1 PRO MAX", u_ip)
        u_ip = re.sub(r"\b(\d{2})PRO\b", r"\1 PRO", u_ip)
        u_ip = re.sub(r"\b(\d{2})PM\b", r"\1 PRO MAX", u_ip)
        if re.search(r"\b(?:DUO|DOU|FOLD)\b", u_ip) and re.search(r"\b(?:PRO|MAX|PLUS|MINI|AIR)\b", u_ip):
            return ""
        if re.search(r"\b(?:IPHONE\s*(?:18\s+)?)?(?:DUO|DOU|FOLD)\b", u_ip):
            return "IPHONE DUO"
        matches = re.findall(
            r"\bIPHONE\s*((?:1[1-9]|2\d)E?|SE|X[RS]?|[78])(?:\s*(PRO\s*MAX|PM|PRO|PLUS|AIR|MINI))?\b",
            u_ip,
        )
        if matches:
            gen, tier = matches[-1]
            tier = re.sub(r"\s+", " ", (tier or "")).strip()
            if tier == "PM":
                tier = "PRO MAX"
            return f"IPHONE {gen} {tier}".strip()

    # 3. MacBook / iMac / Mac Mini / Mac Studio
    if any(k in u for k in ("MACBOOK", "IMAC", "MAC MINI", "MAC STUDIO")):
        m = u
        m = re.sub(r"\b13(?:\.|\s+)6\b", "13", m)
        m = re.sub(r"\b14(?:\.|\s+)2\b", "14", m)
        m = re.sub(r"\b15(?:\.|\s+)3\b", "15", m)
        m = re.sub(r"\b16(?:\.|\s+)2\b", "16", m)
        m = re.sub(r"\bMACBOOK\s+(M\d(?:\s+(?:PRO|MAX|ULTRA))?)\s+(AIR|PRO)\b", r"MACBOOK \2 \1", m)
        m = re.sub(r"\b(M\d)(PRO|MAX|ULTRA)\b", r"\1 \2", m)
        m = re.sub(r"\bCHIP[\s-]*(PRO|MAX|ULTRA)\b", r" \1 ", m)
        m = re.sub(r"\b(?:\d+C\s*(?:CPU|GPU)|\d+[-]CORES?\s*(?:CPU|GPU)?|CPU|GPU)\b", " ", m)
        m = re.sub(r"\bA\d{4}\b|\bLLA\b|\b202[0-9]\b", " ", m)
        if "MAC STUDIO" in m:
            chip = re.search(r"\b(M\d(?:\s+(?:MAX|ULTRA|PRO))?)\b", m)
            return f"MAC STUDIO {chip.group(1)}" if chip else "MAC STUDIO"
        if "MAC MINI" in m:
            chip = re.search(r"\b(M\d(?:\s+(?:PRO|MAX))?)\b", m.replace("/", " "))
            return f"MAC MINI {chip.group(1)}" if chip else "MAC MINI"
        if "IMAC" in m:
            chip = re.search(r"\b(M\d)\b", m)
            return f"IMAC {chip.group(1)} 24\"" if chip else "IMAC 24\""
        if "MACBOOK NEO" in m:
            return 'MACBOOK NEO 13"'
        if not re.search(r"\bMACBOOK (AIR|PRO)\b", m):
            return ""
        line_name = "MACBOOK AIR" if "MACBOOK AIR" in m else "MACBOOK PRO"
        chip_m = re.search(r"\b(M[1-9](?:\s+(?:PRO|MAX))?)\b", m)
        chip_str = chip_m.group(1) if chip_m else ""
        if line_name == "MACBOOK PRO" and re.search(r"\bM\d\b", chip_str) and not re.search(r"\b(?:PRO|MAX)\b", chip_str):
            if re.search(r"\bMAX\b", re.sub(r"\bMACBOOK\s+PRO\b", "", m)):
                chip_str = f"{chip_str} MAX"
            elif re.search(r"\bPRO\b", re.sub(r"\bMACBOOK\s+PRO\b", "", m)):
                chip_str = f"{chip_str} PRO"
        clean_for_inch = re.sub(r"\b\d+\s*/\s*(?:128|256|512|1024|1T|2T|4T|[124]TB)(?:GB)?\b", " ", m)
        clean_for_inch = re.sub(r"\b(?:8|16|18|24|32|36|48|64)\s*(?:GB)?[\s-]*RAM\b|\b\d+\s*GB\b|/\s*(?:8|16|18|24|32|36|48|64)\b", " ", clean_for_inch)
        allowed = ("13", "15") if line_name == "MACBOOK AIR" else ("13", "14", "15", "16")
        inch_m = re.search(r"(?:^|\s|T|/)(" + "|".join(allowed) + r")(?:\s*(?:-?INCH|POLEGADAS?|\"|\s|$|/))", clean_for_inch)
        if not inch_m and full_line:
            fl = re.sub(r"\b\d+\s*/\s*(?:128|256|512|1024|1T|2T|[124]TB)(?:GB)?\b", " ", full_line.upper())
            fl = re.sub(r"\b\d+\s*(?:GB)?[\s-]*RAM\b|\bRAM\s*\d+\s*(?:GB)?\b", " ", fl)
            inch_m = re.search(r"(?:^|\s|T|/)(" + "|".join(allowed) + r")(?:\s*(?:-?INCH|POLEGADAS?|\"|\s|$))", fl)
        inch = f'{inch_m.group(1)}"' if inch_m else ""
        return " ".join(p for p in (line_name, chip_str, inch) if p)

    # 4. Apple Watch
    if "WATCH" in u:
        w = re.sub(r"(?:\bAPPLE\s+WATCH\s+)+", "APPLE WATCH ", u)
        w = re.sub(r"\b(?:SERIES?|S)\s*(\d{1,2})\b", r"S\1", w)
        w = re.sub(r"\b(SE)\s*(\d)\s*G(?:ERACAO|ER)?\b", r"\1 \2", w)
        mm_m = re.search(r"\b(38|40|41|42|44|45|46|49)\s*(?:MM|M\b)?", w)
        mm = f"{mm_m.group(1)}MM" if mm_m else ("49MM" if "ULTRA" in w else "")
        base_m = re.search(r"\bAPPLE\s+WATCH\s+(ULTRA\s*[2-9]|S(?:1[0-9]|9)|SE(?:\s*[2-9])?)\b", w)
        if base_m:
            series = re.sub(r"\s+", " ", base_m.group(1)).strip()
            has_cell = "ULTRA" in series or bool(re.search(r"\b(?:CELL|CELLULAR|CELULA[R]?|5G|LTE)\b", f"{w} {full_line.upper()}"))
            conn = "GPS + CELLULAR" if has_cell else "GPS"
            return " ".join(p for p in ("APPLE WATCH", series, mm, conn) if p)

    # 5. AirPods / EarPods
    if "AIRPODS" in u or "EARPODS" in u:
        if "EARPODS" in u:
            return "EARPODS LIGHTNING" if "LIGHTNING" in u else "EARPODS USB-C"
        if "MAX" in u:
            m_max = re.search(r"\bMAX\s*([1-9])\b", u)
            return f"AIRPODS MAX {m_max.group(1)} USB-C" if m_max else "AIRPODS MAX 2 USB-C"
        if re.search(r"\bPRO(?:\b|\d)", u):
            # Famílias Pro confirmadas em support.apple.com/en-us/109525.
            pro = re.search(r"\bPRO\s*([1-3])(?:GER(?:ACAO)?|G)?\b|\b([1-3])(?:GER(?:ACAO)?|G)?\s*PRO\b", u)
            return f"AIRPODS PRO {pro[1] or pro[2]}" if pro else ""
        m_gen = re.search(r"\b([2-9])(?:GER(?:A[CÇ][AÃ]O)?|G)?\b", u)
        if m_gen:
            gen = m_gen.group(1)
            if gen == "4":
                return "AIRPODS 4 ANC" if re.search(r"\b(?:ANC|NOISE)\b", u) else "AIRPODS 4"
            if int(gen) >= 5:
                is_anc = bool(re.search(r"\b(?:ANC|NOISE|CANCELAMENTO|WIRELESS|INDU[CÇ]|SEM\s*FIO)\b", f"{u} {full_line.upper()}"))
                return f"AIRPODS {gen} ANC" if is_anc else f"AIRPODS {gen}"
            return f"AIRPODS {gen}"

    # 6. iPads (separa explicitamente WI-FI vs WI-FI + CELLULAR em todos os modelos)
    if "IPAD" in u:
        src = f"{u} {full_line.upper()}"
        has_cpo = bool(re.search(r"\bCPO\b", u))
        is_cellular = bool(re.search(
            r"(?:\+|WI-?FI\s*\+?)\s*CEL(?:L(?:ULAR)?|ULAR)?\b|\b(?:CELLULAR|CELULAR|5G|4G|LTE|E-?SIM|COM\s+CHIP)\b",
            src,
        ))
        conn = "WI-FI + CELLULAR" if is_cellular else "WI-FI"
        p = u
        p = re.sub(r"\bWIFI\b", "WI-FI", p)
        p = re.sub(r"\bWI-FI\s*(?:\+?\s*(?:CEL(?:L(?:ULAR)?|ULAR)?|5G|4G|LTE))?\b", " ", p)
        p = re.sub(r"\b(?:CELLULAR|CELULAR|5G|4G|LTE|E-?SIM|COM\s+CHIP|202[0-9]|CPO|LLA|LL/A|A\d{4})\b", " ", p)
        p = re.sub(r"\+\s*CEL(?:L)?\b", " ", p)
        p = re.sub(r"\+", " ", p)
        p = re.sub(r"\b(12)\s+9(?=\s*-?\s*INCH\b)", r"\1.9", p)
        p = re.sub(r"\b(\d+)(?:ST|ND|RD|TH)\b(?:\s+(?:GENERATION|GEN)\b)?", r"\1", p)
        p = re.sub(r"\b(\d+)\s*(?:GERA[CÇ][AÃ]O|GERACAO|GER)\b", r"\1", p)
        p = re.sub(r"\s+", " ", p).strip()

        def _extract_ipad_inch(text):
            tm = re.search(r"\bT(11|13)\b", text)
            if tm:
                return tm.group(1)
            em = re.search(r'\b(8\.3|10\.9|11|12\.9|13)(?:\s*-?\s*INCH\b|")', text)
            if em:
                return em.group(1)
            bm = re.search(r"\b(10\.9|11|12\.9|13)\b", text)
            return bm.group(1) if bm else ""

        cpo_suffix = " CPO" if has_cpo else ""
        if re.search(r"\bIPAD\s+PRO\b", p):
            chip_m = re.search(r"\b(M[1-9])\b", p)
            inch = _extract_ipad_inch(re.sub(r"\bIPAD\s+PRO\b", " ", p))
            parts = ["IPAD PRO", chip_m.group(1) if chip_m else "", f'{inch}"' if inch else "", conn]
            return " ".join(x for x in parts if x) + cpo_suffix
        if re.search(r"\bIPAD\s+AIR\b", p):
            chip_m = re.search(r"\b(M[1-9])\b", p)
            if chip_m:
                inch = _extract_ipad_inch(re.sub(r"\bIPAD\s+AIR\b", " ", p))
                parts = ["IPAD AIR", chip_m.group(1), f'{inch}"' if inch else "", conn]
                return " ".join(x for x in parts if x) + cpo_suffix
            gen_m = re.search(r"\bIPAD\s+AIR\s+([1-9])\b", p)
            if gen_m:
                return f"IPAD AIR {gen_m.group(1)} {conn}{cpo_suffix}"
            return f"{p} {conn}{cpo_suffix}"
        if re.search(r"\bIPAD\s+MINI\b", p):
            if re.search(r"\b(?:7|A17)\b", p):
                return f"IPAD MINI 7 {conn}{cpo_suffix}"
            gen_m = re.search(r"\bIPAD\s+MINI\s+([1-9])\b", p)
            if gen_m:
                return f"IPAD MINI {gen_m.group(1)} {conn}{cpo_suffix}"
            return f"{p} {conn}{cpo_suffix}"
        if re.search(r"\bIPAD\s+(?:11\b|A16\b)", p) and not re.search(r"\b\d+GB\b", p):
            return f"IPAD 11 A16 {conn}{cpo_suffix}"
        gen_m = re.search(r"\bIPAD\s+(9|10|12)\b", p)
        if gen_m:
            return f"IPAD {gen_m.group(1)} {conn}{cpo_suffix}"
        return f"{p} {conn}{cpo_suffix}"

    return u


def normalize_model_name(raw_text):
    upper = raw_text.upper().strip()
    upper = re.sub(r"^\bLINHA\s+", "", upper)
    upper = re.sub(r"\bMAC\s+BOOK\b", "MACBOOK", upper)
    upper = re.sub(r"\bPROMAX\b", "PRO MAX", upper)
    upper = re.sub(r"\bMAC\s+(AIR|PRO|NEO)\b", r"MACBOOK \1", upper)
    upper = re.sub(r"\bAPPLE\s+(SE\s*[23]?|S1[012]|ULTRA\s*[234]?)\b", r"APPLE WATCH \1", upper)
    upper = re.sub(r"^(S1[012]|ULTRA\s*[234])\b", r"APPLE WATCH \1", upper)
    upper = re.sub(r"\b(ULTRA\s*[234])\s+(49\s*MM)\b", r"APPLE WATCH \1 \2", upper)
    if re.search(r"\bAPPLE\s+WATCH\s+ULTRA\s*[234]?\b", upper) and "49MM" not in upper.replace(" ", ""):
        upper = re.sub(r"\b(APPLE\s+WATCH\s+ULTRA(?:\s*[234])?)\b", r"\1 49MM", upper, count=1)
    upper = re.sub(r"\bAPPLE\s+MOUSE\b", "MAGIC MOUSE", upper)
    upper = re.sub(r"\bIPHONE\s*(?:18\s+)?(?:DUO|DOU|FOLD)\b", "IPHONE DUO", upper)
    upper = re.sub(r"\b(?:LL|HN|AH|CH|ZA|ZP|JA|J|BR|BZ|BE|VC|QL|ZD|LZ|KH|TH|TA|TU|RK|MY|SG|AE|AA|AB|BA|DN)/A\b", " ", upper)
    upper = re.sub(r"\b(?:LLA|HNA|AHA|CHA)\b", " ", upper)
    upper = re.sub(r"\b\d+\s*PCS?\b", " ", upper)
    upper = re.sub(r"(?:\bAPPLE\s+WATCH\s+)+", "APPLE WATCH ", upper)
    upper = re.sub(r"\b13\.6\b", "13", upper)
    upper = re.sub(r"\b14\.2\b", "14", upper)
    upper = re.sub(r"\b15\.3\b", "15", upper)
    upper = re.sub(r"\b16\.2\b", "16", upper)

    upper = re.sub(r"(?:R\$|\$)\s*\d{1,2}[\.,]\d{3}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"(?:R\$|\$)\s*\d{3,5}(?:,\d{2})?", " ", upper)
    upper = re.sub(r"\b\d{1,2}[\.,]\d{3}(?:,\d{2})?\b", " ", upper)
    upper = re.sub(r"(?:[\-\:\=])\s*\d{3,5}(?:,\d{2})?\s*$", " ", upper)
    upper = re.sub(r"\s+\d{3,5}\s*$", " ", upper)

    if re.match(r"^(1[6-9]|2\d)\s+E\b", upper):
        upper = re.sub(r"^(1[6-9]|2\d)\s+E\b", r"IPHONE \1E", upper)
    elif re.match(r"^(1[1-8](?:E)?)\b", upper):
        upper = "IPHONE " + upper

    upper = re.sub(r"\b(?:IPH|IP)\s*(1[1-8](?:E)?|[78]|X[RS]?|SE)\b", r"IPHONE \1", upper)
    upper = re.sub(r"\bIPHONE\s*(1[6-9]|2\d)\s+E\b", r"IPHONE \1E", upper)
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
    return canonicalize_apple_model(upper, raw_text)


def classify_category(model_name, full_text):
    u = (model_name + " " + full_text).upper()
    if model_name.upper().startswith("CABO ") or "EARPODS" in u or any(k in u for k in ["APPLE PENCIL", "PENCIL", "MAGIC KEYBOARD", "SMART KEYBOARD", "TECLADO", "MAGIC MOUSE", "APPLE MOUSE", "MAGIC TRACKPAD", "TRACKPAD", "AIRTAG", "FONTE ORIGINAL", "CABO ORIGINAL", "FONTE APPLE", "CABO APPLE", "CARREGADOR APPLE"]):
        return "Acessórios Apple"
    if "IPHONE" in u or re.search(r"\bIP\s*1[1-8]\b", u):
        return "iPhones"
    if any(k in u for k in ["MACBOOK", "MAC AIR", "MAC PRO", "MAC NEO", "MAC MINI", "IMAC", "MAC STUDIO"]):
        return "MacBook & iMac"
    if "IPAD" in u:
        return "iPad"
    if any(k in u for k in ["APPLE WATCH", "WATCH S", "WATCH SE", "WATCH ULTRA", "ULTRA 2", "ULTRA 3", "ULTRA 4", "SERIE 9", "SERIE 10", "SERIES 9", "SERIES 10"]):
        return "Apple Watch"
    if "AIRPODS" in u:
        return "AirPods"
    return "Outros Apple"


def is_allowed_apple_product(model_name, category, is_semi, allow_seminovo=True):
    """
    Regra Estrita:
    1. Bloqueia Android, videogames e marcas que não sejam Apple.
    2. Bloqueia seminovos se allow_seminovo for False. Se allow_seminovo=True, permite Apple seminovo.
    3. Permite apenas produtos Apple Oficiais: iPhones, MacBook, iPad, Apple Watch, AirPods, iMac, Mac Mini, Acessórios Apple.
    """
    if excluded_power_accessory(model_name):
        return False
    if is_semi and not allow_seminovo:
        return False
    if category not in ("iPhones", "MacBook & iMac", "iPad", "Apple Watch", "AirPods", "Acessórios Apple"):
        return False
    u = model_name.upper()
    # Bloqueia Android, videogames e marcas que não sejam Apple.
    if any(k in u for k in ["REDMI", "XIAOMI", "POCO", "REALME", "SAMSUNG", "GALAXY", "MOTOROLA", "MOTO ", "INFINIX", "TECNO", "HONOR", "AMAZFIT", "T-REX", "HAYLOU", "HUAWEI", "HOTWAV", "ITEL ", "OUKITEL", "PS5", "PLAYSTATION", "XBOX", "NINTENDO", "PRIMEIRA LINHA", "1 LINHA", "1A LINHA", "ORIGINAL CHINA", "TABLET", "KIT GAMER", "GAMER", "LOGITECH", "MULTILASER", "REDRAGON", "RAY-BAN", "RAYBAN", "BRACELET", "DYSON", "ALEXA", "ECHO"]):
        return False
    # Permite estritamente produtos Apple
    if not any(k in u for k in ["IPHONE", "MACBOOK", "MAC MINI", "IMAC", "MAC STUDIO", "IPAD", "APPLE WATCH", "WATCH", "AIRPODS", "EARPODS", "APPLE PENCIL", "PENCIL", "MAGIC KEYBOARD", "SMART KEYBOARD", "TECLADO", "MAGIC MOUSE", "MAGIC TRACKPAD", "TRACKPAD", "AIRTAG", "CARREGADOR", "CABO", "FONTE"]):
        return False
    return True


WATCH_SHORT_HEADER = re.compile(r"^(SE\s*[1-9]|S\d{1,2}|ULTRA\s*[1-9])\s*[º°ª]?\s+(?=\d{2}\s*MM\b)", re.I)


def expand_watch_price_blocks(lines):
    """Explicita o preço das cores em listas no formato relógio, preço, cores."""
    result = []
    index = 0
    while index < len(lines):
        header = WATCH_SHORT_HEADER.sub(r"APPLE WATCH \1 ", lines[index])
        result.append(header)
        index += 1
        if "WATCH" not in header.upper() or extract_price_from_line(header) is not None:
            continue
        while index < len(lines) and re.fullmatch(r"(?:R?\$\.?\s*)?" + MONEY_NUMBER, lines[index], re.I):
            price_line = lines[index]
            start = index + 1
            end = start
            while end < len(lines):
                color_line = lines[end]
                if (looks_like_product_header(color_line) or extract_color(color_line) == "-" or
                        extract_price_from_line(color_line) is not None or WATCH_SHORT_HEADER.match(color_line)):
                    break
                end += 1
            if end == start:
                break
            result.extend(f"{color} {price_line}" for color in lines[start:end])
            index = end
    return result


def looks_like_product_header(line):
    u = line.upper()
    if excluded_power_accessory(u) or is_shorthand_iphone_line(u):
        return True
    for kw in PRODUCT_KEYWORDS:
        if kw in u:
            return True
    return False


def parse_supplier_message(text, supplier_name, supplier_phone, group_name="", date_str=None, image_base64=None, msg_timestamp=None, include_non_new=False, _gemini_retry=False):
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

    # Regra Estrita: Apenas produtos do dia de HOJE a partir das 06:00 da manhã
    now = time.localtime()
    today_str = time.strftime("%d/%m/%Y", now)

    if msg_timestamp and isinstance(msg_timestamp, (int, float)) and msg_timestamp > 0:
        ts_struct = time.localtime(msg_timestamp)
        date_str = time.strftime("%d/%m/%Y", ts_struct)
        hour = ts_struct.tm_hour
        time_str = time.strftime("%H:%M:%S", ts_struct)
        if date_str != today_str or hour < 6:
            return []
    else:
        if now.tm_hour < 6:
            return []
        date_str = today_str
        time_str = time.strftime("%H:%M:%S", now)

    # Bloqueia listas antigas reenviadas/reproduzidas pelo histórico com data explícita de dias anteriores no cabeçalho
    try:
        cur_d, cur_m, cur_y = [int(x) for x in date_str.split("/")]
        header_lines = [ln.strip() for ln in combined_raw.splitlines() if ln.strip()][:8]
        for hln in header_lines:
            hu = hln.upper()
            if any(w in hu for w in ("GARANTIA", "ATÉ", "ATE ", "VALID", "ATIV")):
                continue
            dm = re.search(r"\b([0-3]?\d)/([01]?\d)/(20\d{2})\b", hu)
            if dm:
                hd, hm, hy = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                if 1 <= hd <= 31 and 1 <= hm <= 12 and (hy, hm, hd) < (cur_y, cur_m, cur_d):
                    return []
    except Exception:
        pass

    # Remover avisos de troca ("Aceitamos seu iPhone usado como parte do pagamento...")
    clean_full_text = exclude_non_original_sections(strip_tradein_disclaimers(combined_raw))
    upper_all = strip_warranty_activation_disclaimer(clean_full_text)

    # Analisar condição global da mensagem + foto
    has_explicit_seminovo = any(k in upper_all for k in SEMINOVO_KEYWORDS) or (img_info.get("image_condition") in ("Seminovo", "Deslacrado", "Ativado"))
    has_explicit_novo = any(k in upper_all for k in NOVO_LACRADO_KEYWORDS) or (img_info.get("image_condition") == "Novo / Lacrado")

    def is_cpo_eligible_model(mod_name):
        mu = (mod_name or "").upper()
        if any(x in mu for x in ("IPHONE 17", "IPHONE 18", "IPHONE DUO")):
            return False
        return "IPHONE" in mu

    # Se a mensagem inteira é sobre Seminovo (e não é uma lista mista com seção "LACRADOS")
    section_seminovo = bool(has_explicit_seminovo and not has_explicit_novo)

    lines = [clean_line(l) for l in clean_full_text.splitlines()]
    lines = expand_watch_price_blocks([l for l in lines if len(l) >= 2])

    parsed_products = []
    current_header_model = ""
    current_header_capacity = "-"
    current_header_color = "-"
    current_header_price = None
    current_header_cat = ""
    header_placeholder_idx = None
    section_condition = "Novo / Lacrado" if (has_explicit_novo and not has_explicit_seminovo) else "Condição não informada"
    header_condition = None
    evidence_lines = []

    for line in lines:
        u = line.upper()

        if u == SECTION_BREAK or excluded_power_accessory(line):
            current_header_model = ""
            header_condition = None
            current_header_price = None
            header_placeholder_idx = None
            evidence_lines = []
            continue
        explicit_condition = condition_from_text(line)
        is_specific_model_line = False
        if explicit_condition and extract_price_from_line(line) is None and looks_like_product_header(line):
            cand_m = normalize_model_name(line)
            if cand_m and cand_m not in {"IPHONE", "IPHONES", "IPAD", "IPADS", "MACBOOK", "MACBOOKS", "APPLE WATCH", "WATCH", "AIRPODS", "IMAC", "MAC MINI", "MAC STUDIO"}:
                if "IPHONE" in cand_m:
                    is_specific_model_line = bool(re.search(r"\b(?:1[1-9]|2\d|SE|X[RS]?|[78]|DUO|DOU|FOLD)\b", cand_m))
                elif "IPAD" in cand_m:
                    is_specific_model_line = bool(re.search(r"\b(?:PRO|AIR|MINI|9|10|11|A16|M[1-9])\b", cand_m))
                elif "MAC" in cand_m:
                    is_specific_model_line = bool(re.search(r"\b(?:M[1-9]|NEO)\b", cand_m))
                elif "WATCH" in cand_m:
                    is_specific_model_line = bool(re.search(r"\b(?:ULTRA|S\d|SE)\b", cand_m))
                elif "AIRPODS" in cand_m or "EARPODS" in cand_m:
                    is_specific_model_line = bool(re.search(r"\b(?:PRO|MAX|[2-9]|USB-C|LIGHTNING)\b", cand_m))
                else:
                    is_specific_model_line = True
        if explicit_condition and extract_price_from_line(line) is None and not is_specific_model_line:
            if any(non_apple in u for non_apple in ("XIAOMI", "REDMI", "REALME", "SAMSUNG", "INFINIX", "MOTOROLA", "POCO", "GARMIN")):
                continue
            battery_detail = re.match(r"^(?:BAT(?:ERIA)?[. :]*)?(?:\d{1,3}\s*%|\d+\s*(?:CICLOS?|CYCLES?|CYCLUS)\b)", u)
            if current_header_model and (re.match(r"^\(", line) or battery_detail):
                header_condition = explicit_condition
                evidence_lines.append(line)
            else:
                section_condition = explicit_condition
                current_header_model = ""
                current_header_color = "-"
                current_header_price = None
                header_placeholder_idx = None
                header_condition = None
                evidence_lines = [line]
                section_seminovo = explicit_condition not in ("Novo / Lacrado", "Novo — lacre não informado", "CPO")
            continue

        if re.match(r"^(?:LINHA\s+IPHONE\s+\d+|MACBOOKS?\s+IPADS?.*|IMAC|IPAD|AIRPODS(?:\s+MAX)?|APPLE\s+WATCH|WATCHES|MACBOOK)\s*$", u):
            if "IPHONE" in u:
                current_header_model = normalize_model_name(u)
            else:
                current_header_model = ""
                if section_condition == "CPO":
                    section_condition = "Novo / Lacrado" if has_explicit_novo else "Condição não informada"
                    section_seminovo = False
            current_header_capacity = "-"
            current_header_color = "-"
            current_header_price = None
            header_placeholder_idx = None
            continue

        price = extract_price_from_line(line)
        has_product_kw = looks_like_product_header(line)

        # Caso 1: Cabeçalho de modelo sem preço (ex: "IPHONE 16 PRO MAX 256GB" ou "15 128" ou "18 PRO 256 LL")
        if has_product_kw and price is None:
            cand_model = normalize_model_name(line)
            cand_cat = classify_category(cand_model, line) if cand_model else ""
            if not cand_model or not is_allowed_apple_product(cand_model, cand_cat, False):
                current_header_model = ""
                current_header_price = None
                header_placeholder_idx = None
                continue
            current_header_model = cand_model
            current_header_cat = cand_cat
            if section_condition == "CPO" and explicit_condition != "CPO" and not is_cpo_eligible_model(current_header_model):
                section_condition = "Novo / Lacrado" if has_explicit_novo else "Condição não informada"
                section_seminovo = False
            header_condition = explicit_condition
            evidence_lines = [line]
            current_header_capacity = mac_mini_capacity(line) if cand_model.startswith("MAC MINI") else extract_capacity(line)
            current_header_color = extract_color(line)
            current_header_price = None
            header_placeholder_idx = None
            if explicit_condition == "CPO":
                section_seminovo = False
            elif any(k in u for k in SEMINOVO_KEYWORDS):
                section_seminovo = True
            elif any(k in u for k in NOVO_LACRADO_KEYWORDS):
                section_seminovo = False
            continue

        if not has_product_kw and price is None and current_header_model:
            if current_header_model.startswith("MAC MINI"):
                spec = mac_mini_capacity(line)
                if re.fullmatch(r"(?:[0-9A-Z/+.() -]+)", u) and re.fullmatch(
                    r"(?:[0-9]+C\s*(?:CPU|GPU)[/ ]*)*(?:[0-9]+\s*(?:GB|G|RAM)?\s*[/+ ]\s*[0-9]+\s*(?:GB|TB))", u
                ) and "/" in spec:
                    current_header_capacity = spec
                    evidence_lines = [evidence_lines[0], line]
                    continue
                if re.fullmatch(r"\d+C CPU[/ ]+\d+C GPU(?: \(A\d{4}\))?", u):
                    evidence_lines.append(line)
                    continue
            standalone_color = extract_color(line)
            if standalone_color != "-":
                if current_header_price is not None:
                    cond_val = explicit_condition or header_condition or section_condition
                    if header_placeholder_idx is not None and 0 <= header_placeholder_idx < len(parsed_products):
                        parsed_products[header_placeholder_idx]["cor"] = standalone_color
                        parsed_products[header_placeholder_idx]["linhaOriginal"] = f"{parsed_products[header_placeholder_idx]['linhaOriginal']} ({standalone_color})"
                        parsed_products[header_placeholder_idx]["contextoOriginal"] += "\n" + line
                        header_placeholder_idx = None
                    else:
                        parsed_products.append({
                            "modelo": current_header_model,
                            "capacidade": current_header_capacity,
                            "cor": standalone_color,
                            "categoria": current_header_cat,
                            "condicao": cond_val,
                            "condicaoVerificada": True,
                            "contextoOriginal": "\n".join([section_condition] + evidence_lines + [line]),
                            "preco": current_header_price,
                            "fornecedor": supplier_name,
                            "telefone": supplier_phone,
                            "origem": group_name or supplier_name,
                            "data": date_str,
                            "hora": time_str,
                            "linhaOriginal": f"{current_header_model} {current_header_capacity} ({standalone_color}) - {line}",
                        })
                else:
                    current_header_color = standalone_color
                    evidence_lines = evidence_lines[:1] + [line]
            elif re.search(r"[A-Z]{2}", u) and not re.fullmatch(r"(?:VALOR|PRE[CÇ]O|POR|R\$)[ .:]*", u):
                # Um título desconhecido encerra a oferta. O próximo preço isolado
                # não pode herdar o modelo da seção anterior.
                current_header_model = ""
                current_header_price = None
                header_placeholder_idx = None
            continue

        # Caso 2: Linha tem nome de produto E tem preço na mesma linha
        if has_product_kw and price is not None:
            model = normalize_model_name(line)
            if not model or len(model) < 3:
                current_header_model = ""
                current_header_price = None
                header_placeholder_idx = None
                continue
            if section_condition == "CPO" and explicit_condition != "CPO" and not is_cpo_eligible_model(model):
                section_condition = "Novo / Lacrado" if has_explicit_novo else "Condição não informada"
                section_seminovo = False
            cap = mac_mini_capacity(line) if model.startswith("MAC MINI") else extract_capacity(line)
            color = extract_color(line)
            cond_val = explicit_condition or section_condition
            sem = is_seminovo(line, section_seminovo, model, price, has_explicit_novo)
            if sem and cond_val in ("Condição não informada", "Novo / Lacrado", "Novo — lacre não informado") and cond_val != "CPO":
                cond_val = "Seminovo"
            if cond_val in ("Ativado", "Deslacrado", "Seminovo", "ASIS"):
                sem = True
            cat = classify_category(model, line)

            # Filtrar estritamente: apenas produtos Apple permitidos
            if not is_allowed_apple_product(model, cat, sem, allow_seminovo=include_non_new):
                if not is_allowed_apple_product(model, cat, False, allow_seminovo=True):
                    current_header_model = ""
                    current_header_price = None
                    header_placeholder_idx = None
                continue

            header_condition = cond_val if cond_val != "Condição não informada" else explicit_condition
            evidence_lines = [line]
            current_header_model = model
            current_header_capacity = cap
            current_header_color = color
            current_header_cat = cat
            if color == "-":
                current_header_price = price
                header_placeholder_idx = len(parsed_products)
            else:
                current_header_price = None
                header_placeholder_idx = None

            parsed_products.append({
                "modelo": model,
                "capacidade": cap,
                "cor": color,
                "categoria": cat,
                "condicao": cond_val,
                "condicaoVerificada": True,
                "contextoOriginal": "\n".join([section_condition, line]),
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
            cap_line = mac_mini_capacity(line) if current_header_model.startswith("MAC MINI") else extract_capacity(line)
            cap = cap_line if cap_line != "-" else current_header_capacity
            if color != "-" or len(line) <= 35:
                cond_val = explicit_condition or header_condition or section_condition
                sem = is_seminovo(line, section_seminovo, current_header_model, price, has_explicit_novo)
                if sem and cond_val in ("Condição não informada", "Novo / Lacrado", "Novo — lacre não informado") and cond_val != "CPO":
                    cond_val = "Seminovo"
                if cond_val in ("Ativado", "Deslacrado", "Seminovo", "ASIS"):
                    sem = True
                cat = classify_category(current_header_model, line)

                # Filtrar estritamente: apenas produtos Apple e sem marcas intrusas na linha
                if any(bad in u for bad in ("AMAZFIT", "T-REX", "HAYLOU", "HUAWEI", "HOTWAV", "ITEL ", "OUKITEL", "GARMIN", "XIAOMI", "REDMI", "SAMSUNG", "PELLE", "CAPINHA")):
                    continue
                if not is_allowed_apple_product(current_header_model, cat, sem, allow_seminovo=include_non_new):
                    continue

                final_color = color
                parsed_products.append({
                    "modelo": current_header_model,
                    "capacidade": cap,
                    "cor": final_color,
                    "categoria": cat,
                    "condicao": cond_val,
                "condicaoVerificada": True,
                "contextoOriginal": "\n".join([section_condition] + evidence_lines + [line]),
                    "preco": price,
                    "fornecedor": supplier_name,
                    "telefone": supplier_phone,
                    "origem": group_name or supplier_name,
                    "data": date_str,
                    "hora": time_str,
                    "linhaOriginal": f"{current_header_model} {cap} ({final_color}) - {line}",
                })

    if (
        not parsed_products
        and not _gemini_retry
        and not section_seminovo
        and os.environ.get("GEMINI_API_KEY", "").strip()
        and any(k in upper_all for k in ["IPHONE", "IPAD", "MACBOOK", "IMAC", "MAC MINI", "MAC STUDIO", "APPLE WATCH", "WATCH ULTRA", "AIRPODS", "APPLE PENCIL", "MAGIC KEYBOARD", "MAGIC MOUSE", "AIRTAG"])
    ):
        prompt = (
            "Extraia apenas produtos Apple NOVOS LACRADOS ou CPO desta mensagem de fornecedor.\n"
            "TRAVA DE SEGURANÇA CANÔNICA (OBRIGATÓRIO):\n"
            "- Descarte cabos, fontes e carregadores, inclusive kits desses itens.\n"
            "- Ignore totalmente Seminovos, Usados, Vitrine, Swap, As Is, Caixa Aberta, Open Box, Deslacrados, Ativados, 1ª linha, películas ('pelle'), Android e Videogames.\n"
            "- Use EXATAMENTE nomes oficiais sem sufixos (NUNCA escreva LL/A, HN/A, FÍSICO, chip, Anatel, '18 Duo', cores ou códigos A3xxx no modelo):\n"
            "  * O dobrável Apple chama-se SEMPRE 'IPHONE DUO' (NUNCA 'IPHONE 18 DUO').\n"
            "- Escreva na primeira linha 'LACRADOS' (ou 'CPO' apenas para modelos antigos explicitamente CPO) e depois uma linha por oferta:\n"
            "MODELO CAPACIDADE COR R$ PRECO\n"
            "- Se todos os itens forem seminovos/ativados/usados ou não houver Apple Novo/CPO com preço, responda apenas VAZIO.\n\n"
            f"Mensagem:\n{clean_full_text[:4000]}"
        )
        gem_out = call_gemini_api(prompt)
        if gem_out and "VAZIO" not in gem_out.upper():
            return parse_supplier_message(
                gem_out,
                supplier_name,
                supplier_phone,
                group_name=group_name,
                date_str=date_str,
                image_base64=None,
                msg_timestamp=msg_timestamp,
                include_non_new=include_non_new,
                _gemini_retry=True,
            )

    return parsed_products
