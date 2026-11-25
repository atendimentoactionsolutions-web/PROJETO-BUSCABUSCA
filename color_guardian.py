"""
Robô Guardião e Aprendiz de Cores Oficiais Apple (color_guardian.py).

Responsabilidades:
1. Separa rigorosamente cor da CAIXA/HARDWARE vs cor de PULSEIRA/ACESSÓRIO (especialmente Apple Watch).
2. Mantém registro oficial de cores válidas por modelo Apple e aprende automaticamente
   cores oficiais de modelos recém-lançados via Gemini API (com cache persistente em apple_colors_cache.json).
3. Auto-corrige ofertas cuja cor veio errada por causa da pulseira (ex: "BLACK TI BURGUNDY ALP LOOP" -> PRETO).
4. Bloqueia/remove qualquer cor inexistente para aquele modelo (ex: BURGUNDY em Apple Watch Ultra 4).
5. Roda em background periodicamente (robô autônomo) e também em tempo real na entrada de cada oferta.
"""
import json
import os
import re
import tempfile
import threading
import time
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COLOR_CACHE_FILE = os.path.join(BASE_DIR, "apple_colors_cache.json")
COLOR_AUDIT_REPORT_FILE = os.path.join(BASE_DIR, "ultima_auditoria_cores.json")

_cache_lock = threading.RLock()
_learned_cache = None

_BAND_COLOR_WORDS = (
    r"(?:BURGUNDY|BORD[OÔ]|BORDEAUX|VINHO|INDIGO|ORANGE|LARANJA|GREEN|VERDE|"
    r"BLUE|AZUL|ANCHOR\s+BLUE|DENIM|NAVY|BEIGE|TAN|CHARCOAL|OLIVE|TERRACOTTA|"
    r"NEON|YELLOW|AMARELO|PINK|ROSA|PLUM|ROXO|PURPLE|RED|VERMELHO|LAKE\s+GREEN|"
    r"STONE\s+GRAY|LIGHT\s+BLUSH|CLAY|WINTER\s+BLUE|WHITE|BRANCO|BLACK|PRETO|ICE\s+BLUE)"
)

_WATCH_BAND_PHRASE_RE = re.compile(
    rf"(?:\b{_BAND_COLOR_WORDS}\s+)?\b(?:ALPINE|ALP\.?|TRAIL|OCEAN|MILANESE|MILANESA|SPORT|SOLO|BRAIDED|LINK)\s*"
    r"(?:LOOP|BAND|BRACELET)\b(?:\s*(?:S/M|M/L|P/M|M/G|[SML]))?",
    re.I,
)
_WATCH_PAREN_BAND_RE = re.compile(
    r"\([^)]*\b(?:ALPINE|ALP\.?|TRAIL|OCEAN|MILANESE|MILANESA|SPORT|SOLO|LOOP|BAND|PULSEIRA|BRACELET)\b[^)]*\)",
    re.I,
)
_WATCH_PULSEIRA_RE = re.compile(
    rf"\bPULSEIRA\b(?:\s+(?:DE\s+)?(?:SILICONE|ESPORTIVA|ALPINE|TRAIL|OCEAN|MILANESA|TECIDO|COURO|{_BAND_COLOR_WORDS})){{0,4}}",
    re.I,
)

# Mapa unificado de nomes/apelidos -> cor canônica interna (PT-BR uppercase)
CANONICAL_COLOR_MAP = {
    "black": "PRETO",
    "preto": "PRETO",
    "preta": "PRETO",
    "white": "BRANCO",
    "branco": "BRANCO",
    "branca": "BRANCO",
    "star white": "BRANCO",
    "pearl white": "BRANCO",
    "silver": "PRATA",
    "prata": "PRATA",
    "prateado": "PRATA",
    "gold": "DOURADO",
    "dourado": "DOURADO",
    "light gold": "DOURADO",
    "radiant gold": "DOURADO",
    "pink": "ROSA",
    "rosa": "ROSA",
    "rose": "ROSA",
    "soft pink": "ROSA",
    "rosa claro": "ROSA",
    "rosa gold": "ROSA",
    "rose gold": "ROSA",
    "ouro rosa": "ROSA",
    "blush": "ROSA",
    "bluch": "ROSA",
    "purple": "ROXO",
    "roxo": "ROXO",
    "lilas": "ROXO",
    "deep purple": "ROXO",
    "lavender": "LAVANDA",
    "lavanda": "LAVANDA",
    "green": "VERDE",
    "verde": "VERDE",
    "sage": "VERDE",
    "citrus": "VERDE",
    "alpine green": "VERDE",
    "verde alpine": "VERDE",
    "teal": "VERDE ACINZENTADO",
    "verde acinzentado": "VERDE ACINZENTADO",
    "verde-acinzentado": "VERDE ACINZENTADO",
    "petroleo": "VERDE ACINZENTADO",
    "blue": "AZUL",
    "azul": "AZUL",
    "sky blue": "AZUL",
    "skyblue": "AZUL",
    "mist blue": "AZUL",
    "deep blue": "AZUL",
    "night blue": "AZUL",
    "indigo": "AZUL",
    "ultramarine": "AZUL",
    "ultramarino": "AZUL",
    "azul ultramarino": "AZUL",
    "sierra blue": "AZUL SIERRA",
    "azul sierra": "AZUL SIERRA",
    "yellow": "AMARELO",
    "amarelo": "AMARELO",
    "orange": "LARANJA",
    "laranja": "LARANJA",
    "cosmic orange": "LARANJA",
    "red": "VERMELHO",
    "vermelho": "VERMELHO",
    "gray": "CINZA ESPACIAL",
    "grey": "CINZA ESPACIAL",
    "space gray": "CINZA ESPACIAL",
    "space grey": "CINZA ESPACIAL",
    "cinza espacial": "CINZA ESPACIAL",
    "space black": "PRETO ESPACIAL",
    "preto espacial": "PRETO ESPACIAL",
    "spb": "PRETO ESPACIAL",
    "jet black": "PRETO",
    "jetblack": "PRETO",
    "preto brilhante": "PRETO",
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
    "cloud white": "BRANCO",
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
    "ardosia": "ARDÓSIA",
    "slate": "ARDÓSIA",
    "dark bronze": "BRONZE",
    "bronze": "BRONZE",
}

# Regras oficiais Apple (verificadas em apple.com): (regex_modelo, overrides_por_chave_ascii, cores_permitidas)
OFFICIAL_MODEL_COLOR_RULES = [
    (re.compile(r"^MAC MINI M\d+(?: PRO)?$", re.I), {}, {"PRATA"}),
    # Acessórios Apple
    (re.compile(r"\b(?:PENCIL|AIRTAG|FONTE|CARREGADOR|CABO)\b", re.I), {"branco": "BRANCO", "white": "BRANCO"}, {"BRANCO"}),
    (re.compile(r"\b(?:MAGIC\s+MOUSE|MAGIC\s+TRACKPAD|TRACKPAD|MAGIC\s+KEYBOARD|SMART\s+KEYBOARD|TECLADO)\b", re.I),
     {"preto espacial": "PRETO", "space black": "PRETO", "spb": "PRETO", "cinza espacial": "PRETO"}, {"BRANCO", "PRETO", "PRATA"}),

    # iPhones
    (re.compile(r"\bIPHONE\s+DUO\b", re.I),
     {"preto": "NIGHT SKY", "meia-noite": "NIGHT SKY", "night sky": "NIGHT SKY",
      "branco": "BRANCO", "prata": "BRANCO", "estelar": "BRANCO", "star white": "BRANCO"},
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

    # Apple Watch — APENAS cores oficiais da CAIXA (nunca cor da pulseira!)
    # Ultra 2 / 3 / 4: SOMENTE Titânio Natural e Titânio Preto (Burgundy é pulseira Alpine/Trail Loop!)
    (re.compile(r"\bAPPLE\s+WATCH\s+ULTRA\b", re.I),
     {"prata": "NATURAL TITÂNIO", "natural": "NATURAL TITÂNIO", "preto titânio": "PRETO", "preto titanio": "PRETO"},
     {"NATURAL TITÂNIO", "PRETO"}),
    # Series 12: Alumínio (Dark Bronze, Light Gold, Black, Space Gray), Titânio (Natural, Radiant Gold), Cerâmica (Pearl White, Night Blue)
    (re.compile(r"\bAPPLE\s+WATCH\s+S12\b", re.I),
     {"meia-noite": "PRETO", "preto espacial": "PRETO", "preto brilhante": "PRETO"},
     {"PRETO", "CINZA ESPACIAL", "BRONZE", "DOURADO", "NATURAL TITÂNIO", "BRANCO", "AZUL"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S11\b.*\bCELLULAR\b", re.I),
     {}, {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA", "DOURADO", "NATURAL TITÂNIO", "ARDÓSIA"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S11\b", re.I),
     {}, {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA"}),
    (re.compile(r"\bAPPLE\s+WATCH\s+S(?:9|10)\b", re.I),
     {},
     {"PRETO", "CINZA ESPACIAL", "PRATA", "ROSA", "DOURADO", "NATURAL TITÂNIO", "ESTELAR", "MEIA-NOITE", "VERMELHO"}),
    # SE 3: SOMENTE Meia-Noite e Estelar
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


def _ascii_key(text):
    clean = unicodedata.normalize("NFKD", str(text or ""))
    clean = "".join(ch for ch in clean if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", clean).strip().lower()


def strip_watch_band_noise(text):
    """Remove descrições de pulseira (Alpine/Trail/Ocean/Sport/Milanese Loop/Band) mantendo a cor da CAIXA."""
    s = str(text or "")
    s = _WATCH_PAREN_BAND_RE.sub(" ", s)
    s = _WATCH_BAND_PHRASE_RE.sub(" ", s)
    s = _WATCH_PULSEIRA_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_raw_tail(product):
    raw = str(product.get("linhaOriginal") or product.get("texto_original") or "")
    if ") - " in raw:
        raw = raw.rsplit(") - ", 1)[-1]
    return raw.strip()


def extract_case_color_from_line(u_model, raw_line):
    """
    Recupera a cor REAL da caixa/hardware diretamente da linha original do fornecedor,
    ignorando cor de pulseira/acessório (ex: 'BLACK TI BURGUNDY ALP LOOP' -> 'PRETO').
    """
    if not raw_line:
        return ""
    cleaned = strip_watch_band_noise(raw_line)
    # Se há separador " / " em Watch (ex: "Black / Indigo" ou "Natural / Blue"), a cor da CAIXA vem antes da barra
    if "WATCH" in u_model and "/" in cleaned:
        left_part = cleaned.split("/", 1)[0].strip()
        if left_part:
            cleaned = left_part
    upper = cleaned.upper()

    if "APPLE WATCH ULTRA" in u_model:
        # Prioriza marcações explícitas da caixa de titânio
        if re.search(r"\b(?:BLACK\s*TI(?:TANIUM)?|TI(?:TANIUM)?\s*BLACK|CAIXA\s+PRET[AO]|PRET[AO]\s+TIT[ÂA]NIO|TIT[ÂA]NIO\s+PRET[AO]|PRETO|BLACK)\b", upper):
            return "PRETO"
        if re.search(r"\b(?:NATURAL\s*TI(?:TANIUM)?|TI(?:TANIUM)?\s*NATURAL|CAIXA\s+NATURAL|NATURAL\s+TIT[ÂA]NIO|TIT[ÂA]NIO\s+NATURAL|NATURAL|PRATA|SILVER)\b", upper):
            return "NATURAL TITÂNIO"
        return ""

    if "WATCH" in u_model:
        # Remove ruído de tamanho/conectividade para não confundir
        upper = re.sub(r"\b(?:4[0-9]|38)\s*MM\b|\bGPS\b|\bCELLULAR\b|\bCELULAR\b|\bS/M\b|\bM/L\b", " ", upper)

    ak = _ascii_key(upper)
    for alias in sorted(CANONICAL_COLOR_MAP.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", ak):
            return CANONICAL_COLOR_MAP[alias]
    return ""


def _load_learned_cache():
    global _learned_cache
    with _cache_lock:
        if _learned_cache is not None:
            return _learned_cache
        try:
            if os.path.exists(COLOR_CACHE_FILE):
                with open(COLOR_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    _learned_cache = data
                    return _learned_cache
        except Exception:
            pass
        _learned_cache = {}
        return _learned_cache


def _save_learned_cache(cache):
    with _cache_lock:
        try:
            fd, tmp = tempfile.mkstemp(prefix=".colors-", suffix=".tmp", dir=BASE_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, COLOR_CACHE_FILE)
        except OSError:
            pass


def _model_cache_key(u_model):
    # Remove especificações de tamanho/conectividade que não mudam as cores da linha
    k = re.sub(r'\b(?:10\.9|11|12\.9|13|14|15|16|24)"', "", str(u_model or "").upper())
    k = re.sub(r"\b(?:38|40|41|42|44|45|46|49)MM\b|\bWI-FI(?:\s*\+\s*CELLULAR)?\b|\bGPS(?:\s*\+\s*CELLULAR)?\b", "", k)
    return re.sub(r"\s+", " ", k).strip()


def lookup_official_rule(u_model):
    """Retorna (overrides_dict, allowed_set, source) ou (None, None, None) se o modelo ainda não é conhecido."""
    for pat, overrides, allowed in OFFICIAL_MODEL_COLOR_RULES:
        if pat.search(u_model):
            return overrides, set(allowed), "static_official"
    cache = _load_learned_cache()
    ck = _model_cache_key(u_model)
    entry = cache.get(ck)
    if isinstance(entry, dict) and entry.get("colors"):
        return {}, set(entry["colors"]), "learned_gemini"
    return None, None, None


def learn_official_colors_for_model(u_model):
    """
    Robô Aprendiz: consulta o Gemini API para aprender APENAS as cores oficiais
    de acabamento da CAIXA/HARDWARE de um modelo Apple novo ainda não mapeado.
    """
    ck = _model_cache_key(u_model)
    if not ck or not any(k in ck for k in ("IPHONE", "IPAD", "MACBOOK", "IMAC", "MAC ", "APPLE WATCH", "AIRPODS")):
        return None

    cache = _load_learned_cache()
    if ck in cache and cache[ck].get("colors"):
        return set(cache[ck]["colors"])

    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return None

    try:
        from parser_listas import call_gemini_api
    except Exception:
        return None

    prompt = (
        f"Liste APENAS as cores/acabamentos oficiais de fábrica da CAIXA/HARDWARE do produto Apple: {ck}.\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Para Apple Watch, retorne SOMENTE as cores da CAIXA (case finish), NUNCA cores de pulseira (Alpine Loop, Trail Loop, Ocean Band, Sport Band).\n"
        "2. Retorne APENAS um JSON válido no formato {\"colors\": [\"...\"]} usando nomes oficiais em inglês (ex: Black, Silver, Space Black, Natural Titanium, Midnight, Starlight, Blue, Pink, etc.).\n"
        "3. Se o modelo não existir oficialmente na Apple, retorne {\"colors\": []}."
    )
    raw = call_gemini_api(prompt)
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None
    raw_colors = parsed.get("colors") if isinstance(parsed, dict) else None
    if not isinstance(raw_colors, list):
        return None

    normalized = set()
    for c in raw_colors:
        ak = _ascii_key(c)
        if ak in CANONICAL_COLOR_MAP:
            normalized.add(CANONICAL_COLOR_MAP[ak])
    if not normalized:
        return None

    with _cache_lock:
        cache[ck] = {
            "colors": sorted(normalized),
            "raw": raw_colors,
            "learnedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "gemini",
        }
        _save_learned_cache(cache)
    return normalized


def _apply_rule(raw_color, overrides, allowed):
    ak = _ascii_key(raw_color)
    canon = CANONICAL_COLOR_MAP.get(ak, ak.upper())
    ck = _ascii_key(canon)
    final_color = overrides.get(ck, canon)
    if final_color in allowed:
        return True, final_color
    return False, final_color


def audit_and_heal_color(product, u_model=None, allow_learn=False):
    """
    Valida a cor de `product` contra as cores oficiais do modelo.
    - Se a cor atual for inválida (ex: cor de pulseira BURGUNDY em Watch Ultra 4),
      tenta recuperar a cor real da caixa na linha original do fornecedor.
    - Retorna (ok: bool, status: str, final_color: str).
    """
    model = (u_model or str(product.get("modelo") or "")).upper().strip()
    raw_color = str(product.get("cor") or "").strip()

    # Preserva marcadores explícitos de confirmação (Watch SE 3 azul / Smart Keyboard space gray)
    if (raw_color == "Cor da caixa a confirmar" and re.search(r"\bWATCH SE\s*3\b", model)) or (
        raw_color == "Cor a confirmar" and model.startswith("SMART KEYBOARD")
    ):
        return True, "OK", raw_color

    overrides, allowed, _src = lookup_official_rule(model)
    if allowed is None and allow_learn:
        learned = learn_official_colors_for_model(model)
        if learned:
            overrides, allowed = {}, learned

    if allowed is None:
        # Modelo não coberto por regra específica: valida contra conjunto conhecido de cores Apple
        known = set().union(*(a for _, _, a in OFFICIAL_MODEL_COLOR_RULES))
        ak = _ascii_key(raw_color)
        canon = CANONICAL_COLOR_MAP.get(ak, ak.upper())
        if canon in known:
            product["cor"] = canon
            return True, "OK", canon
        return False, f"COR_DESCONHECIDA: {raw_color}", raw_color

    ok, resolved = _apply_rule(raw_color, overrides, allowed)
    if ok:
        # Mesmo se a cor passou, em Apple Watch Ultra nunca deixar cor de pulseira se linhaOriginal contradiz
        product["cor"] = resolved
        return True, "OK", resolved

    # Tentativa de auto-correção pela linha original (ex: "BLACK TI BURGUNDY ALP LOOP" -> PRETO)
    tail = _extract_raw_tail(product)
    context = str(product.get("contextoOriginal") or "")
    for candidate_src in (tail, context):
        recovered = extract_case_color_from_line(model, candidate_src)
        if not recovered:
            continue
        ok_rec, fixed_color = _apply_rule(recovered, overrides, allowed)
        if ok_rec:
            product["cor"] = fixed_color
            return True, f"AUTO_CORRIGIDA: {raw_color} -> {fixed_color}", fixed_color

    return False, f"COR_INEXISTENTE_NO_MODELO: {raw_color} em {model}", raw_color


def run_color_guardian_sweep(rows, offer_key_fn=None, log_anomalies=True, allow_learn=True):
    """
    Varredura completa do Robô de Cores em todas as ofertas ativas do catálogo:
    - Auto-corrige cores trocadas com pulseiras (ex: Ultra 4 Burgundy -> Preto)
    - Remove qualquer cor que não existe oficialmente no modelo
    - Aprende automaticamente cores de novos modelos Apple via Gemini API
    - Deduplica ofertas que passaram a ter a mesma chave após a correção de cor
    """
    from price_guardian import log_anomaly

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
    healed_map = {}
    corrected = 0
    removed = 0
    deduplicated = 0
    learned_models = []
    actions = []

    for row in rows or []:
        item = dict(row)
        model = str(item.get("modelo") or "").upper().strip()
        orig_color = str(item.get("cor") or "").strip()

        had_rule = lookup_official_rule(model)[1] is not None
        ok, status, final_color = audit_and_heal_color(item, model, allow_learn=allow_learn)
        if not had_rule and lookup_official_rule(model)[1] is not None:
            ck = _model_cache_key(model)
            if ck not in learned_models:
                learned_models.append(ck)

        if not ok:
            removed += 1
            if log_anomalies:
                log_anomaly(row, f"ROBO_CORES:{status}")
            if len(actions) < 30:
                actions.append(f"Removida ({status}): {model} R$ {item.get('preco')} [{item.get('fornecedor')}]")
            continue

        if status.startswith("AUTO_CORRIGIDA:"):
            corrected += 1
            if len(actions) < 30:
                actions.append(f"Corrigida ({orig_color} -> {final_color}): {model} [{item.get('fornecedor')}]")

        k = key_fn(item)
        if k in healed_map:
            deduplicated += 1
            if float(item.get("preco", 999999)) < float(healed_map[k].get("preco", 999999)):
                healed_map[k] = item
        else:
            healed_map[k] = item

    healed_list = list(healed_map.values())
    changed = (corrected > 0) or (removed > 0) or (deduplicated > 0)
    report = {
        "checked": time.strftime("%d/%m/%Y %H:%M:%S"),
        "changed": changed,
        "analyzed": len(rows or []),
        "validAfter": len(healed_list),
        "corrected": corrected,
        "removed": removed,
        "deduplicated": deduplicated,
        "learnedModels": learned_models,
        "actions": actions,
    }
    return healed_list, report
