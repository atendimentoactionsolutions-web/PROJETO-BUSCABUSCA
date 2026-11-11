/**
 * price_guardian.cjs - Guardião de Integridade de Preços e Detector de Anomalias
 * 
 * Regras:
 * 1. Somente produtos Apple Oficiais (Novos/Lacrados).
 * 2. Bloqueio absoluto de Seminovos e Androids.
 * 3. Correção de nomenclaturas (ex: NUNCA permitir "AirPods 5").
 * 4. Faixas realistas de preços mínimos e máximos por família de produtos.
 * 5. Rejeição de outliers estatísticos (> 25% abaixo da mediana ou > 35% acima).
 * 6. Registro de anomalias em anomalias_detectadas.json para auditoria.
 */

const fs = require('fs');
const path = require('path');

const ANOMALY_LOG_FILE = path.join(__dirname, 'anomalias_detectadas.json');

// Faixas de Sanidade de Preço Mínimo e Máximo por Modelo (em R$)
const SANITY_BOUNDS = [
  // iPhones
  { pattern: /IPHONE\s+13\b/i, min: 2800, max: 4800 },
  { pattern: /IPHONE\s+14\s+PLUS/i, min: 3600, max: 5500 },
  { pattern: /IPHONE\s+14\b/i, min: 3200, max: 5000 },
  { pattern: /IPHONE\s+15\s+PLUS/i, min: 4200, max: 6200 },
  { pattern: /IPHONE\s+15\s+PRO\s+MAX/i, min: 5800, max: 8800 },
  { pattern: /IPHONE\s+15\s+PRO\b/i, min: 5200, max: 7800 },
  { pattern: /IPHONE\s+15\b/i, min: 3800, max: 5600 },
  { pattern: /IPHONE\s+16\s+PLUS/i, min: 5000, max: 7600 },
  { pattern: /IPHONE\s+16\s+PRO\s+MAX/i, min: 6800, max: 12500 },
  { pattern: /IPHONE\s+16\s+PRO\b/i, min: 5900, max: 9800 },
  { pattern: /IPHONE\s+16E\b/i, min: 3500, max: 5500 },
  { pattern: /IPHONE\s+16\b/i, min: 4400, max: 6800 },
  { pattern: /IPHONE\s+17/i, min: 5500, max: 13500 },
  { pattern: /IPHONE\s+18\s+PRO\s+MAX/i, min: 7800, max: 14500 },
  { pattern: /IPHONE\s+18\s+PRO\b/i, min: 6800, max: 12500 },
  { pattern: /IPHONE\s+18\b/i, min: 5800, max: 11000 },

  // MacBooks e Macs
  { pattern: /MACBOOK\s+AIR/i, min: 4800, max: 13500 },
  { pattern: /MACBOOK\s+PRO/i, min: 7500, max: 35000 },
  { pattern: /MAC\s+MINI/i, min: 3900, max: 16000 },
  { pattern: /MAC\s+STUDIO/i, min: 12000, max: 38000 },
  { pattern: /IMAC/i, min: 6500, max: 19000 },

  // iPads
  { pattern: /IPAD\s+(?:9|10|11)\b/i, min: 1900, max: 4800 },
  { pattern: /IPAD\s+MINI/i, min: 2800, max: 6500 },
  { pattern: /IPAD\s+AIR/i, min: 3800, max: 8800 },
  { pattern: /IPAD\s+PRO/i, min: 5500, max: 16000 },
  { pattern: /IPAD/i, min: 1900, max: 16000 },

  // Apple Watch
  { pattern: /WATCH\s+SE/i, min: 1400, max: 2800 },
  { pattern: /WATCH\s+(?:S9|SERIES\s*9|S10|SERIES\s*10|S11|S12)/i, min: 2200, max: 5200 },
  { pattern: /WATCH\s+ULTRA/i, min: 4200, max: 7200 },
  { pattern: /APPLE\s+WATCH/i, min: 1400, max: 7200 },

  // AirPods
  { pattern: /AIRPODS\s+PRO/i, min: 1200, max: 2200 },
  { pattern: /AIRPODS\s+MAX/i, min: 3000, max: 5500 },
  { pattern: /AIRPODS\s+(?:2|3|4)\b/i, min: 650, max: 1650 },
  { pattern: /AIRPODS/i, min: 650, max: 5500 },

  // Acessórios
  { pattern: /APPLE\s+PENCIL/i, min: 400, max: 1200 },
  { pattern: /PENCIL/i, min: 400, max: 1200 },
  { pattern: /MAGIC\s+KEYBOARD/i, min: 600, max: 2500 },
  { pattern: /MAGIC\s+MOUSE/i, min: 450, max: 950 },
  { pattern: /MAGIC\s+TRACKPAD/i, min: 650, max: 1250 },
  { pattern: /AIRTAG/i, min: 180, max: 850 },
  { pattern: /FONTE|CARREGADOR/i, min: 120, max: 650 },
  { pattern: /CABO/i, min: 80, max: 350 }
];

// Nomes de produtos proibidos ou corrompidos
const FORBIDDEN_MODEL_PATTERNS = [
  /AIRPODS\s+5/i,
  /AIRPODS\s+6/i,
  /IPHONE\s+DUO/i,
  /IPHONE\s+19/i,
  /IPHONE\s+20/i,
  /REDMI|XIAOMI|POCO|REALME|SAMSUNG|GALAXY|MOTOROLA|MOTO\s|INFINIX|TECNO|HONOR/i,
  /PS5|PLAYSTATION|XBOX|NINTENDO|SWITCH|JBL|STARLINK|GARMIN|GOPRO|DJI/i
];

/**
 * Valida um produto individual extraído da mensagem do fornecedor.
 * Retorna { valid: boolean, reason?: string, sanitizedProduct?: object }
 */
function validateProduct(product, existingDayProducts = []) {
  if (!product) return { valid: false, reason: 'PRODUTO_NULO' };

  const rawModel = String(product.modelo || '').trim();
  const rawPrice = Number(product.preco || 0);

  // 1. Verificar se modelo é válido e não contém termos proibidos
  if (!rawModel || rawModel.length < 3) {
    return { valid: false, reason: 'MODELO_MUITO_CURTO' };
  }

  for (const forbidden of FORBIDDEN_MODEL_PATTERNS) {
    if (forbidden.test(rawModel)) {
      logAnomaly(product, `MODELO_INVALIDO_OU_PROIBIDO: ${rawModel}`);
      return { valid: false, reason: `MODELO_PROIBIDO (${rawModel})` };
    }
  }

  // 2. Condição deve ser Novo/Lacrado (rejeição absoluta de seminovos)
  const cond = String(product.condicao || '').toLowerCase();
  if (cond.includes('semi') || cond.includes('usado') || cond.includes('vitrine') || cond.includes('swap') || cond.includes('as is')) {
    logAnomaly(product, `CONDICAO_SEMINOVO_REJEITADA: ${product.condicao}`);
    return { valid: false, reason: 'PRODUTO_SEMINOVO_BLOQUEADO' };
  }

  // 3. Preço numérico básico
  if (isNaN(rawPrice) || rawPrice <= 50 || rawPrice > 45000) {
    logAnomaly(product, `PRECO_FORA_DA_FAIXA_GLOBAL: R$ ${rawPrice}`);
    return { valid: false, reason: `PRECO_INVALIDO: R$ ${rawPrice}` };
  }

  // 4. Verificação de faixa de sanidade realista por família
  let matchedBound = null;
  for (const b of SANITY_BOUNDS) {
    if (b.pattern.test(rawModel)) {
      matchedBound = b;
      break;
    }
  }

  if (matchedBound) {
    if (rawPrice < matchedBound.min || rawPrice > matchedBound.max) {
      logAnomaly(product, `PRECO_FORA_DA_FAIXA_DO_MODELO: R$ ${rawPrice} (esperado entre R$ ${matchedBound.min} e R$ ${matchedBound.max})`);
      return { valid: false, reason: `PRECO_FORA_DA_FAIXA_DO_MODELO (R$ ${rawPrice})` };
    }
  }

  // 5. Verificação estatística de desvio contra mediana dos demais fornecedores de hoje
  if (Array.isArray(existingDayProducts) && existingDayProducts.length > 0) {
    const sameModelQuotes = existingDayProducts.filter(p => {
      if (!p || !p.modelo) return false;
      return p.modelo.toUpperCase() === rawModel.toUpperCase() &&
             (p.capacidade || '-') === (product.capacidade || '-') &&
             Number(p.preco) > 0;
    }).map(p => Number(p.preco));

    if (sameModelQuotes.length >= 2) {
      const sorted = [...sameModelQuotes].sort((a, b) => a - b);
      const mid = Math.floor(sorted.length / 2);
      const median = sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;

      // Rejeitar se preço for > 25% abaixo da mediana (erro de digitação do fornecedor)
      if (rawPrice < median * 0.75) {
        logAnomaly(product, `OUTLIER_ABAIXO_DA_MEDIANA: R$ ${rawPrice} vs mediana R$ ${median.toFixed(2)} (-${Math.round((1 - rawPrice/median)*100)}%)`);
        return { valid: false, reason: 'OUTLIER_ABAIXO_DA_MEDIANA' };
      }

      // Rejeitar se preço for > 35% acima da mediana (digito extra)
      if (rawPrice > median * 1.35) {
        logAnomaly(product, `OUTLIER_ACIMA_DA_MEDIANA: R$ ${rawPrice} vs mediana R$ ${median.toFixed(2)} (+${Math.round((rawPrice/median - 1)*100)}%)`);
        return { valid: false, reason: 'OUTLIER_ACIMA_DA_MEDIANA' };
      }
    }
  }

  // Produto 100% aprovado pelo Guardião
  return {
    valid: true,
    sanitizedProduct: {
      ...product,
      preco: rawPrice,
      condicao: 'Novo / Lacrado'
    }
  };
}

/**
 * Registra anomalias em anomalias_detectadas.json para transparência e auditoria.
 */
function logAnomaly(product, reason) {
  try {
    let list = [];
    if (fs.existsSync(ANOMALY_LOG_FILE)) {
      try {
        list = JSON.parse(fs.readFileSync(ANOMALY_LOG_FILE, 'utf8'));
      } catch {
        list = [];
      }
    }

    list.unshift({
      timestamp: new Date().toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' }),
      modelo: product.modelo,
      preco: product.preco,
      fornecedor: product.fornecedor,
      telefone: product.telefone,
      origem: product.origem,
      linhaOriginal: product.linhaOriginal,
      motivoRejeicao: reason
    });

    if (list.length > 200) list = list.slice(0, 200);
    fs.writeFileSync(ANOMALY_LOG_FILE, JSON.stringify(list, null, 2));
    console.log(`[GUARDIÃO ANTI-ERROS] 🛡️ Anomalia bloqueada: "${product.modelo}" R$ ${product.preco} de ${product.fornecedor} -> ${reason}`);
  } catch (err) {
    console.error('[GUARDIÃO] Erro ao gravar anomalia:', err.message);
  }
}

module.exports = {
  validateProduct,
  SANITY_BOUNDS,
  FORBIDDEN_MODEL_PATTERNS
};
