"""
price_guardian.py - Guardião de Preços e Detector de Anomalias em Segundo Plano

Regras:
1. Rejeição de produtos proibidos/corrompidos (ex: AIRPODS 5, IPHONE DUO, Androids, Seminovos).
2. Faixas de sanidade realistas por modelo Apple (mínimo e máximo).
3. Detecção de desvio da mediana dos fornecedores de hoje (outliers de digitação).
4. Registro de todas as anomalias em anomalias_detectadas.json.
"""

import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANOMALY_LOG_FILE = os.path.join(BASE_DIR, "anomalias_detectadas.json")

# Faixas de sanidade por modelo (em R$)
SANITY_BOUNDS = [
    (re.compile(r"IPHONE\s+13\b", re.I), 2800, 4800),
    (re.compile(r"IPHONE\s+14\s+PLUS", re.I), 3600, 5500),
    (re.compile(r"IPHONE\s+14\b", re.I), 3200, 5000),
    (re.compile(r"IPHONE\s+15\s+PLUS", re.I), 4200, 6200),
    (re.compile(r"IPHONE\s+15\s+PRO\s+MAX", re.I), 5800, 8800),
    (re.compile(r"IPHONE\s+15\s+PRO\b", re.I), 5200, 7800),
    (re.compile(r"IPHONE\s+15\b", re.I), 3800, 5600),
    (re.compile(r"IPHONE\s+16\s+PLUS", re.I), 5000, 7600),
    (re.compile(r"IPHONE\s+16\s+PRO\s+MAX", re.I), 6800, 12500),
    (re.compile(r"IPHONE\s+16\s+PRO\b", re.I), 5900, 9800),
    (re.compile(r"IPHONE\s+16E\b", re.I), 3500, 5500),
    (re.compile(r"IPHONE\s+16\b", re.I), 4400, 6800),
    (re.compile(r"IPHONE\s+17", re.I), 5500, 13500),
    (re.compile(r"IPHONE\s+18\s+PRO\s+MAX", re.I), 7800, 14500),
    (re.compile(r"IPHONE\s+18\s+PRO\b", re.I), 6800, 12500),
    (re.compile(r"IPHONE\s+18\b", re.I), 5800, 11000),
    (re.compile(r"MACBOOK\s+AIR", re.I), 4800, 13500),
    (re.compile(r"MACBOOK\s+PRO", re.I), 7500, 35000),
    (re.compile(r"MAC\s+MINI", re.I), 3900, 16000),
    (re.compile(r"MAC\s+STUDIO", re.I), 12000, 38000),
    (re.compile(r"IMAC", re.I), 6500, 19000),
    (re.compile(r"IPAD\s+(?:9|10|11)\b", re.I), 1900, 4800),
    (re.compile(r"IPAD\s+MINI", re.I), 2800, 6500),
    (re.compile(r"IPAD\s+AIR", re.I), 3800, 8800),
    (re.compile(r"IPAD\s+PRO", re.I), 5500, 16000),
    (re.compile(r"IPAD", re.I), 1900, 16000),
    (re.compile(r"WATCH\s+SE", re.I), 1400, 2800),
    (re.compile(r"WATCH\s+(?:S9|SERIES\s*9|S10|SERIES\s*10|S11|S12)", re.I), 2200, 5200),
    (re.compile(r"WATCH\s+ULTRA", re.I), 4200, 7200),
    (re.compile(r"APPLE\s+WATCH", re.I), 1400, 7200),
    (re.compile(r"AIRPODS\s+PRO", re.I), 1200, 2200),
    (re.compile(r"AIRPODS\s+MAX", re.I), 3000, 5500),
    (re.compile(r"AIRPODS\s+(?:2|3|4)\b", re.I), 650, 1650),
    (re.compile(r"AIRPODS", re.I), 650, 5500),
    (re.compile(r"APPLE\s+PENCIL|PENCIL", re.I), 400, 1200),
    (re.compile(r"MAGIC\s+KEYBOARD", re.I), 600, 2500),
    (re.compile(r"MAGIC\s+MOUSE", re.I), 450, 950),
    (re.compile(r"MAGIC\s+TRACKPAD", re.I), 650, 1250),
    (re.compile(r"AIRTAG", re.I), 180, 850),
    (re.compile(r"FONTE|CARREGADOR", re.I), 120, 650),
    (re.compile(r"CABO", re.I), 80, 350),
]

FORBIDDEN_PATTERNS = [
    re.compile(r"AIRPODS\s+[5-9]", re.I),
    re.compile(r"IPHONE\s+DUO", re.I),
    re.compile(r"IPHONE\s+(?:19|20)", re.I),
    re.compile(r"REDMI|XIAOMI|POCO|REALME|SAMSUNG|GALAXY|MOTOROLA|MOTO\s|INFINIX|TECNO|HONOR", re.I),
    re.compile(r"PS5|PLAYSTATION|XBOX|NINTENDO|SWITCH|JBL|STARLINK|GARMIN|GOPRO|DJI", re.I),
]


def log_anomaly(product, reason):
    try:
        data = []
        if os.path.exists(ANOMALY_LOG_FILE):
            try:
                with open(ANOMALY_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = []

        entry = {
            "timestamp": time.strftime("%d/%m/%Y, %H:%M:%S"),
            "modelo": product.get("modelo"),
            "preco": product.get("preco"),
            "fornecedor": product.get("fornecedor"),
            "telefone": product.get("telefone"),
            "origem": product.get("origem"),
            "linhaOriginal": product.get("linhaOriginal"),
            "motivoRejeicao": reason,
        }
        data.insert(0, entry)
        if len(data) > 200:
            data = data[:200]
        with open(ANOMALY_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[GUARDIÃO ANTI-ERROS] 🛡️ Anomalia bloqueada: {product.get('modelo')} R$ {product.get('preco')} -> {reason}")
    except Exception as e:
        print(f"[GUARDIÃO] Erro ao registrar anomalia: {e}")


def validate_product(product, existing_day_products=None):
    if not product:
        return False, "PRODUTO_NULO", None

    raw_model = str(product.get("modelo", "")).strip()
    try:
        raw_price = float(product.get("preco", 0))
    except (ValueError, TypeError):
        return False, "PRECO_NAO_NUMERICO", None

    if not raw_model or len(raw_model) < 3:
        return False, "MODELO_INVALIDO", None

    # 1. Checar termos proibidos
    for pat in FORBIDDEN_PATTERNS:
        if pat.search(raw_model):
            log_anomaly(product, f"MODELO_PROIBIDO: {raw_model}")
            return False, f"MODELO_PROIBIDO: {raw_model}", None

    # 2. Bloqueio absoluto de seminovos
    cond = str(product.get("condicao", "")).lower()
    cat = str(product.get("categoria", "")).lower()
    if any(k in cond or k in cat for k in ["semi", "usado", "vitrine", "swap", "as is"]):
        log_anomaly(product, f"SEMINOVO_REJEITADO: {cond}")
        return False, "SEMINOVO_BLOQUEADO", None

    # 3. Faixa global razoável
    if raw_price <= 50 or raw_price > 45000:
        log_anomaly(product, f"PRECO_FORA_DA_FAIXA_GLOBAL: R$ {raw_price}")
        return False, f"PRECO_INVALIDO: R$ {raw_price}", None

    # 4. Faixa por modelo
    matched_bound = None
    for pattern, min_p, max_p in SANITY_BOUNDS:
        if pattern.search(raw_model):
            matched_bound = (min_p, max_p)
            break

    if matched_bound:
        min_p, max_p = matched_bound
        if raw_price < min_p or raw_price > max_p:
            log_anomaly(product, f"PRECO_FORA_DA_FAIXA_DO_MODELO: R$ {raw_price} (esperado R$ {min_p} - R$ {max_p})")
            return False, f"PRECO_FORA_DA_FAIXA (R$ {raw_price})", None

    # 5. Desvio estatístico da mediana do dia
    if existing_day_products and isinstance(existing_day_products, list):
        same_quotes = [
            float(p.get("preco", 0))
            for p in existing_day_products
            if str(p.get("modelo", "")).upper() == raw_model.upper()
            and str(p.get("capacidade", "-")) == str(product.get("capacidade", "-"))
            and float(p.get("preco", 0)) > 0
        ]
        if len(same_quotes) >= 2:
            same_quotes.sort()
            mid = len(same_quotes) // 2
            median = same_quotes[mid] if len(same_quotes) % 2 != 0 else (same_quotes[mid - 1] + same_quotes[mid]) / 2.0

            if raw_price < median * 0.75:
                log_anomaly(product, f"OUTLIER_ABAIXO_DA_MEDIANA: R$ {raw_price} vs mediana R$ {median:.2f}")
                return False, "OUTLIER_ABAIXO_DA_MEDIANA", None

            if raw_price > median * 1.35:
                log_anomaly(product, f"OUTLIER_ACIMA_DA_MEDIANA: R$ {raw_price} vs mediana R$ {median:.2f}")
                return False, "OUTLIER_ACIMA_DA_MEDIANA", None

    sanitized = dict(product)
    sanitized["preco"] = raw_price
    sanitized["condicao"] = "Novo / Lacrado"
    return True, "OK", sanitized
