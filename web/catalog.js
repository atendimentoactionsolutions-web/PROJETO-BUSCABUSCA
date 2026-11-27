export const CATEGORIES = [
  ['iphone', 'iPhones Novos'], ['seminovos', 'iPhones Seminovos'], ['mac', 'Mac'], ['ipad', 'iPad'], ['watch', 'Apple Watch'],
  ['airpods', 'AirPods'], ['accessories', 'Acessórios'], ['imac', 'iMac'], ['cpo', 'CPO'],
];
export const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
export const money = value => Number(value).toLocaleString('pt-BR', {style:'currency', currency:'BRL', maximumFractionDigits:Number.isInteger(Number(value)) ? 0 : 2});
export function isSeminovoProduct(product) {
  if (!product || typeof product !== 'object') return false;
  if (product.is_seminovo) return true;
  const rawCond = String(product.condicao || '').trim().toLowerCase();
  if (['seminovo', 'usado', 'vitrine', 'recondicionado', 'ativado', 'deslacrado', 'open box', 'swap', 'asis', 'as is'].includes(rawCond)) return true;
  const model = normalize(product.modelo);
  const category = normalize(product.categoria);
  const cond = normalize(product.condicao);
  const orig = normalize(product.linhaOriginal);
  const obs = normalize(product.observacoes);
  const flags = `${model} ${cond} ${category} ${orig} ${obs}`;
  if (/\b(?:semi[\s-]*nov[oa]s?|vitrine|swap|usad[oa]s?|grade\s*[a-d]\+?|as\s*is|asis|ativad[oa]s?|deslacrad[oa]s?|open\s*box|recondicionad[oa]s?|remanufaturad[oa]s?|segunda\s*m[aã]o|bateria|ciclos?|impecavel|marcas?|riscos?)\b/i.test(flags)) return true;
  if (/%/.test(`${model} ${orig} ${obs}`)) return true;
  return false;
}

export function categoryOf(product) {
  const model = normalize(product.modelo).replace(/(\d)(cpo)\b/g, '$1 $2');
  const category = normalize(product.categoria);
  const cond = normalize(product.condicao);
  if (isSeminovoProduct(product)) return 'seminovos';
  if (/\bcpo\b/.test(`${model} ${cond}`)) return 'cpo';
  if (/\b(?:smart|magic) keyboard\b/.test(model)) return 'accessories';
  if (/\b(earpods|teclado|keyboard|magic\s*mouse|magic\s*trackpad|trackpad|pencil|airtag|fonte|carregador|cabo|magsafe|adapter|adaptador)\b/.test(model) || category.includes('acessorio')) return 'accessories';
  if (/\bimac\b/.test(model)) return 'imac';
  if (category.includes('iphone') || model.includes('iphone')) {
    if (/\biphone\s+(?:[78]|x[rs]?|se(?:\s*[23])?|11|12|13|14\s+(?:plus|pro))\b/.test(model)) return 'seminovos';
    return 'iphone';
  }
  if (category.includes('mac') || /macbook|mac mini|mac studio/.test(model)) return 'mac';
  if (category.includes('ipad') || model.includes('ipad')) return 'ipad';
  if (category.includes('watch') || model.includes('watch')) return 'watch';
  if (category.includes('airpods') || model.includes('airpods')) return 'airpods';
  return 'accessories';
}
export function getDfuDelta(product, category = null) {
  if (!product || typeof product !== 'object') return 0;
  if (isSeminovoProduct(product)) return 0;
  const rawCond = normalize(product.condicao);
  if (['seminovo', 'usado', 'vitrine', 'recondicionado', 'ativado', 'deslacrado', 'open box', 'swap', 'cpo'].includes(rawCond)) return 0;
  if (/\bcpo\b/.test(rawCond) || /\bcpo\b/.test(normalize(product.modelo))) return 0;

  const cat = category || categoryOf(product);
  const m = normalize(product.modelo);

  // Isenções totais (+ R$ 0,00)
  if (cat === 'airpods' || /\bairpods\b/.test(m)) return 0;
  if (cat === 'accessories' || /\b(teclado|keyboard|magic\s*mouse|magic\s*trackpad|trackpad|pencil|airtag|fonte|carregador|cabo|magsafe|adapter|adaptador|folio|capa)\b/.test(m)) return 0;
  if (cat === 'seminovos' || cat === 'cpo') return 0;

  // 1. iPhones (Novos / Lacrados): + R$ 100,00
  if (cat === 'iphone' || /\biphone\b/.test(m)) return 100;

  // 2. MacBooks & Macs (Novos / Lacrados): + R$ 200,00
  if (cat === 'mac' || cat === 'imac' || /\b(macbook|imac|mac\s*(?:mini|studio|pro))\b/.test(m)) return 200;

  // 3. iPads (Novos / Lacrados): + R$ 50,00
  if (cat === 'ipad' || /\bipad\b/.test(m)) return 50;

  // 4. Apple Watch (Novos / Lacrados): + R$ 100,00
  if (cat === 'watch' || /\b(?:apple\s*watch|watch\s*(?:s\d+|ultra|se))\b/.test(m)) return 100;

  return 0;
}

export function effectivePrice(offer, category = null) {
  const base = Number(offer?.preco || 0);
  if (!Number.isFinite(base) || base <= 0) return 0;
  return base + getDfuDelta(offer, category);
}

export function capacityLabel(value) {
  const raw = String(value === '-' ? '' : (value || '')).trim();
  if (!raw) return 'Capacidade não informada';
  if (raw.includes('·')) return raw.toUpperCase();
  return raw.replace(/\s*(GB|TB)\b/gi, ' $1').trim().toUpperCase() || 'Capacidade não informada';
}
export function modelLabel(value) {
  return String(value || 'Produto').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    .replace(/\bIphone\b/g,'iPhone').replace(/\bIpad\b/g,'iPad').replace(/\bImac\b/g,'iMac')
    .replace(/\bMacbook\b/g,'MacBook').replace(/\bAirpods\b/g,'AirPods').replace(/\bUsb\b/g,'USB')
    .replace(/\bGps\b/g,'GPS');
}
export function colorLabel(value) {
  if (!value || value === '-') return 'Cor não informada';
  return String(value).toLowerCase().replace(/(^|[\s-])\S/g, c => c.toUpperCase());
}
// Normalize only known aliases; keep unknown specifications and original offers intact.
export function colorKey(value, modelName = '', rawLine = '') {
  let key = normalize(value).replace(/^(?:0+\s+)+(?=[a-z])/, '').replace(/\b\d+\s*un(?:idades)?\b/g, '').replace(/\b\d{4}\s+00\b/g, '').replace(/\s+/g, ' ').trim();
  if (key === 'cor a confirmar' || key === 'cor da caixa a confirmar') return key;
  const isMissing = !key || key === '-' || /^(padrao|0[\s0]*|q e [\d ]+|r|iphone.*)$/.test(key) || /^mod a\d+/.test(key) || /\b\d+c\s*(?:cpu|gpu)\b/.test(key) || /^\d+gb\b/.test(key);
  if (isMissing) {
    const src = `${normalize(modelName)} ${normalize(rawLine)}`;
    const found = src.match(/\b(space black|spaceblack|space gray|spacegray|spacegrey|jet black|jetblack|rose gold|natural titanium|black titanium|desert titanium|white titanium|sky blue|skyblue|deep blue|mist blue|cosmic orange|light gold|cloud white|dark bronze|bronze|natural carbon|preto espacial|cinza espacial|meia-noite|midnight|mindnight|estelar|starlight|burgundy|bordo|vinho|glacier|glacial|lavender|lavanda|sage|blush|indigo|citrus|slate|dessert|preto|branco|prata|azul|rosa|roxo|amarelo|verde|laranja|dourado)\b/);
    if (found) key = found[1];
    else {
      const m0 = normalize(modelName);
      if ((/\bairpods\b/.test(m0) && !/\bmax\b/.test(m0)) || /\b(earpods|pencil|airtag|fonte|carregador|cabo|adapter|adaptador)\b/.test(m0)) return 'white';
      return 'cor nao informada';
    }
  }
  const aliases = {
    black:'preto', white:'branco', silver:'prata',
    blue:'azul', green:'verde', yellow:'amarelo', pink:'rosa', rose:'rosa', purple:'roxo', red:'vermelho', orange:'laranja', gold:'dourado',
    bordo:'burgundy', bord:'burgundy', bordeaux:'burgundy', vinho:'burgundy', burgamdy:'burgundy',
    glacial:'glacier', glarceir:'glacier', glacie:'glacier', lavanda:'lavender',
    'preto espacial':'space black', spaceblack:'space black', 'cinza espacial':'space gray', spacegray:'space gray', spacegrey:'space gray',
    jetblack:'jet black', skyblue:'sky blue', 'meia-noite':'midnight', mindnight:'midnight', estelar:'starlight',
    stl:'starlight', mdn:'midnight', bluch:'blush', grey:'gray', cinza:'gray',
    'preto titanio':'black titanium', 'branco titanio':'white titanium',
    dessert:'desert titanium', 'dark bronze':'bronze', 'natural carbon':'silver', carbon:'silver',
  };
  if (/^(glacial e bord|bord e glacial|bordeaux\s*\/\s*glace|burgundy\s*\/\s*glacier)$/.test(key)) return 'cor nao informada';
  const base = aliases[key] || key;

  const m = normalize(modelName);
  const pickAllowed = (resolved, allowed) => (allowed.includes(resolved) ? resolved : 'cor nao informada');

  if (/\b(airpods|earpods)\b/.test(m)) {
    if (/\bmax\b/.test(m)) {
      let c = base;
      if (['preto', 'black', 'meia-noite', 'midnight'].includes(base)) c = 'midnight';
      else if (['branco', 'white', 'estelar', 'starlight'].includes(base)) c = 'starlight';
      else if (['azul', 'blue'].includes(base)) c = 'blue';
      else if (['roxo', 'purple'].includes(base)) c = 'purple';
      else if (['laranja', 'orange'].includes(base)) c = 'orange';
      return pickAllowed(c, ['midnight', 'starlight', 'blue', 'purple', 'orange', 'silver', 'space gray', 'green', 'pink']);
    }
    return ['branco', 'white'].includes(base) ? 'white' : 'cor nao informada';
  }
  if (/\b(magic mouse|magic keyboard|smart keyboard|magic trackpad|trackpad|teclado|apple pencil|pencil|airtag|fonte|carregador|cabo)\b/.test(m)) {
    if (/\b(pencil|airtag|fonte|carregador|cabo)\b/.test(m)) return ['branco', 'white'].includes(base) ? 'white' : 'cor nao informada';
    if (['branco', 'white'].includes(base)) return 'white';
    if (['preto', 'black', 'space black', 'preto espacial', 'spb'].includes(base)) return 'black';
    if (['prata', 'silver'].includes(base)) return 'silver';
    return 'cor nao informada';
  }
  if (/\b(macbook|imac|mac\s*mini|mac\s*studio)\b/.test(m)) {
    if (/\bmac\s*(?:mini|studio)\b/.test(m)) {
      return ['prata', 'silver'].includes(base) ? 'silver' : 'cor nao informada';
    }
    if (/\bmacbook\s*neo\b/.test(m)) {
      let c = base;
      if (['rosa', 'pink', 'blush', 'bluch'].includes(base)) c = 'rosa';
      else if (['verde', 'green', 'citrus'].includes(base)) c = 'verde';
      else if (['azul', 'blue', 'indigo', 'sky blue', 'skyblue'].includes(base)) c = 'sky blue';
      else if (['prata', 'silver'].includes(base)) c = 'silver';
      return pickAllowed(c, ['rosa', 'verde', 'sky blue', 'silver']);
    }
    if (/\bmacbook\s*air\b/.test(m)) {
      let c = base;
      if (['midnight', 'meia-noite', 'preto', 'black'].includes(base)) c = 'midnight';
      else if (['starlight', 'estelar', 'branco', 'white'].includes(base)) c = 'starlight';
      else if (['prata', 'silver'].includes(base)) c = 'silver';
      else if (['sky blue', 'skyblue', 'azul', 'blue'].includes(base)) c = 'sky blue';
      const allowedAir = /\bm[1-3]\b/.test(m)
        ? ['midnight', 'starlight', 'silver', 'space gray', 'dourado']
        : ['midnight', 'starlight', 'silver', 'sky blue'];
      return pickAllowed(c, allowedAir);
    }
    if (/\bmacbook\s*pro\b/.test(m)) {
      let c = base;
      if (/^spb\b/.test(base) || ['space black', 'preto espacial', 'preto', 'black'].includes(base)) c = 'space black';
      else if (['prata', 'silver', 'branco', 'white'].includes(base)) c = 'silver';
      return pickAllowed(c, /\bm[1-3]\b/.test(m) ? ['space black', 'silver', 'space gray'] : ['space black', 'silver']);
    }
    if (/\bimac\b/.test(m)) {
      let c = base;
      if (['prata', 'silver'].includes(base)) c = 'silver';
      else if (['sky blue', 'skyblue', 'azul', 'blue'].includes(base)) c = 'sky blue';
      return pickAllowed(c, ['sky blue', 'silver', 'rosa', 'verde', 'amarelo', 'laranja', 'roxo']);
    }
  }
  if (/\bipad\b/.test(m)) {
    if (/\bipad\s*pro\b/.test(m)) {
      const c = ['space black', 'preto espacial', 'preto', 'black'].includes(base) ? 'space black' : (['prata', 'silver', 'branco', 'white'].includes(base) ? 'silver' : base);
      return pickAllowed(c, ['space black', 'silver']);
    }
    if (/\bipad\s*air\b/.test(m)) {
      let c = base;
      if (['space gray', 'space', 'gray', 'cinza espacial', 'preto', 'black'].includes(base)) c = 'space gray';
      else if (['starlight', 'estelar', 'branco', 'white'].includes(base)) c = 'starlight';
      else if (['azul', 'blue'].includes(base)) c = 'blue';
      else if (['roxo', 'purple'].includes(base)) c = 'purple';
      else if (['prata', 'silver'].includes(base)) c = 'silver';
      return pickAllowed(c, /\bm[2-5]\b/.test(m) ? ['space gray', 'starlight', 'blue', 'purple'] : ['space gray', 'starlight', 'silver', 'dourado', 'rosa', 'blue', 'verde']);
    }
    if (/\bipad\s*(?:10|11|a16)\b/.test(m)) {
      let c = base;
      if (['prata', 'silver', 'branco', 'white'].includes(base)) c = 'silver';
      else if (['azul', 'blue'].includes(base)) c = 'blue';
      else if (['rosa', 'pink'].includes(base)) c = 'pink';
      else if (['amarelo', 'yellow'].includes(base)) c = 'yellow';
      return pickAllowed(c, ['blue', 'pink', 'silver', 'yellow']);
    }
    if (/\bipad\s*mini\b/.test(m)) {
      let c = base;
      if (['space gray', 'space', 'gray', 'cinza espacial', 'preto', 'black'].includes(base)) c = 'space gray';
      else if (['starlight', 'estelar', 'branco', 'white'].includes(base)) c = 'starlight';
      else if (['azul', 'blue'].includes(base)) c = 'blue';
      else if (['roxo', 'purple'].includes(base)) c = 'purple';
      else if (['rosa', 'pink'].includes(base)) c = 'pink';
      return pickAllowed(c, ['space gray', 'starlight', 'purple', 'blue', 'pink', 'silver']);
    }
  }
  if (/\bwatch\b/.test(m)) {
    if (/\bultra\b/.test(m)) {
      let c = base;
      if (['preto', 'black', 'black titanium', 'titanio preto'].includes(base)) c = 'black titanium';
      else if (/^natural/.test(base) || ['natural titanium', 'titanio natural', 'prata', 'silver'].includes(base)) c = 'natural titanium';
      else {
        // Nunca exibir cor de pulseira (ex: Burgundy Alpine Loop) como cor da caixa do Watch Ultra; recupera da linha original se houver caixa explícita
        const raw = normalize(rawLine).replace(/\([^)]*(?:loop|band|pulseira)[^)]*\)/g, ' ')
          .replace(/\b(?:burgundy|bordo|indigo|orange|green|blue|beige|tan|charcoal|terracotta|yellow|pink|purple|red)?\s*(?:alpine|alp|trail|ocean|milanese|sport)\s*(?:loop|band)\b/g, ' ');
        if (/\b(?:black\s*ti|ti\s*black|caixa\s+pret[ao]|black\s+titanium|preto\s+titanio|black|preto)\b/.test(raw)) c = 'black titanium';
        else if (/\b(?:natural\s*ti|ti\s*natural|caixa\s+natural|natural\s+titanium|natural)\b/.test(raw)) c = 'natural titanium';
      }
      return pickAllowed(c, ['natural titanium', 'black titanium']);
    }
    if (/\bse\s*3\b/.test(m)) {
      let c = base;
      if (['midnight', 'meia-noite', 'preto', 'black'].includes(base)) c = 'midnight';
      else if (['starlight', 'estelar', 'branco', 'white'].includes(base)) c = 'starlight';
      return pickAllowed(c, ['midnight', 'starlight']);
    }
    if (/\bse\b/.test(m)) {
      let c = base;
      if (['midnight', 'meia-noite', 'preto', 'black'].includes(base)) c = 'midnight';
      else if (['starlight', 'estelar', 'branco', 'white'].includes(base)) c = 'starlight';
      else if (['prata', 'silver'].includes(base)) c = 'silver';
      return pickAllowed(c, ['midnight', 'starlight', 'silver']);
    }
    if (/\b(?:s|series?\s*)12\b/.test(m)) {
      let c = base;
      if (['jet black', 'preto', 'black', 'midnight', 'meia-noite', 'space black', 'preto espacial'].includes(base)) c = 'jet black';
      else if (['space gray', 'gray', 'cinza espacial'].includes(base)) c = 'space gray';
      else if (['dark bronze', 'bronze'].includes(base)) c = 'bronze';
      else if (['light gold', 'white gold', 'radiant gold', 'gold', 'dourado'].includes(base)) c = 'dourado';
      else if (/^natural/.test(base) || ['natural titanium', 'titanio natural'].includes(base)) c = 'natural titanium';
      else if (['pearl white', 'branco', 'white'].includes(base)) c = 'branco';
      else if (['night blue', 'azul', 'blue'].includes(base)) c = 'azul';
      const allowedS12 = /\bcellular\b/.test(m)
        ? ['jet black', 'space gray', 'bronze', 'dourado', 'natural titanium', 'branco', 'azul']
        : ['jet black', 'space gray', 'bronze', 'dourado'];
      return pickAllowed(c, allowedS12);
    }
    if (/\b(?:s|series?\s*)11\b/.test(m)) {
      const finishes = {'rosa':'rose gold','rosa gold':'rose gold','ouro rosa':'rose gold','preto':'jet black',
        'prata':'silver','cinza espacial':'space gray','gray':'space gray','gold':'dourado',
        'natural titanio':'natural titanium','ardosia':'slate'};
      const allowed = ['jet black','silver','rose gold','space gray'];
      if (/\bcellular\b/.test(m)) allowed.push('dourado','natural titanium','slate');
      return pickAllowed(finishes[base] || base, allowed);
    }
    let c = base;
    if (['jet black'].includes(base) || (/\b(?:s\s*rie|serie|s)\s*(?:10|11|12)\b/.test(m) && ['preto', 'black', 'midnight', 'meia-noite'].includes(base))) c = 'jet black';
    else if (['space gray', 'gray', 'cinza espacial'].includes(base)) c = 'space gray';
    else if (['prata', 'silver'].includes(base)) c = 'silver';
    else if (['rose gold', 'ouro rosa', 'rosa', 'pink'].includes(base)) c = 'rose gold';
    else if (['light gold', 'gold', 'dourado'].includes(base)) c = 'dourado';
    else if (['dark bronze', 'bronze'].includes(base)) c = 'bronze';
    else if (/^natural/.test(base) || ['natural titanium', 'titanio natural'].includes(base)) c = 'natural titanium';
    return pickAllowed(c, ['jet black', 'space gray', 'silver', 'rose gold', 'dourado', 'bronze', 'natural titanium', 'midnight', 'starlight', 'vermelho']);
  }
  if (/\biphone\s*(?:1[78]\s+)?(?:duo|dou|fold)\b/.test(m)) {
    if (['preto', 'black', 'midnight', 'meia-noite', 'night sky'].includes(base)) return 'night sky';
    if (['branco', 'white', 'star white', 'estelar', 'starlight', 'prata', 'silver'].includes(base)) return 'branco';
    return 'cor nao informada';
  }
  if (/\biphone\s*18\s*pro(?:\s*max)?\b/.test(m)) {
    if (['azul', 'blue', 'glacier'].includes(base)) return 'glacier';
    if (['burgundy', 'vermelho', 'red'].includes(base)) return 'burgundy';
    if (['prata', 'branco', 'silver', 'white'].includes(base)) return 'silver';
    if (['preto', 'black', 'space black'].includes(base)) return 'black';
    return 'cor nao informada';
  }
  if (/\biphone\s*17\s*pro(?:\s*max)?\b/.test(m)) {
    if (['laranja', 'orange', 'cosmic orange'].includes(base)) return 'cosmic orange';
    if (['azul', 'blue', 'deep blue'].includes(base)) return 'deep blue';
    if (['prata', 'branco', 'silver', 'white'].includes(base)) return 'silver';
    return 'cor nao informada';
  }
  if (/\biphone\s*17\s*air\b/.test(m)) {
    if (['branco', 'white', 'cloud white'].includes(base)) return 'cloud white';
    if (['dourado', 'gold', 'light gold'].includes(base)) return 'light gold';
    if (['preto', 'black', 'space black'].includes(base)) return 'space black';
    if (['azul', 'blue', 'sky blue'].includes(base)) return 'sky blue';
    return 'cor nao informada';
  }
  if (/\biphone\s*17e\b/.test(m)) {
    return pickAllowed(base, ['branco', 'preto', 'rosa']);
  }
  if (/\biphone\s*17\b/.test(m)) {
    if (['verde', 'green', 'sage'].includes(base)) return 'sage';
    if (['azul', 'blue', 'mist blue'].includes(base)) return 'mist blue';
    if (['roxo', 'purple', 'lavender'].includes(base)) return 'lavender';
    if (['branco', 'white'].includes(base)) return 'white';
    if (['preto', 'black'].includes(base)) return 'black';
    return 'cor nao informada';
  }
  if (/\biphone\s*16\s*pro(?:\s*max)?\b/.test(m)) {
    if (['preto', 'black', 'black titanium', 'black titanio', 'titanio preto'].includes(base)) return 'black titanium';
    if (['desert', 'dourado', 'gold', 'areia', 'desert titanium', 'desert titanio', 'titanio deserto'].includes(base)) return 'desert titanium';
    if (['natural', 'cinza', 'prata', 'silver', 'natural titanium', 'natural titanio', 'titanio natural'].includes(base)) return 'natural titanium';
    if (['branco', 'white', 'white titanium', 'white titanio', 'titanio branco'].includes(base)) return 'white titanium';
    return 'cor nao informada';
  }
  if (/\biphone\s*16e\b/.test(m)) {
    return pickAllowed(base, ['branco', 'preto']);
  }
  if (/\biphone\s*16(?:\s*plus)?\b/.test(m)) {
    let c = base;
    if (['verde', 'green', 'teal', 'verde acinzentado', 'petroleo'].includes(base)) c = 'verde acinzentado';
    else if (['azul', 'blue', 'ultramarine', 'ultramarino', 'azul ultramarino'].includes(base)) c = 'azul';
    return pickAllowed(c, ['azul', 'verde acinzentado', 'rosa', 'branco', 'preto']);
  }
  if (/\biphone\s*15\s*pro(?:\s*max)?\b/.test(m)) {
    if (['preto', 'black', 'black titanium', 'black titanio', 'titanio preto'].includes(base)) return 'black titanium';
    if (['branco', 'white', 'white titanium', 'white titanio', 'titanio branco'].includes(base)) return 'white titanium';
    if (['natural', 'cinza', 'prata', 'silver', 'natural titanium', 'natural titanio', 'titanio natural'].includes(base)) return 'natural titanium';
    if (['azul', 'blue', 'blue titanium', 'blue titanio', 'titanio azul', 'azul titanio'].includes(base)) return 'blue titanium';
    return 'cor nao informada';
  }
  if (/\biphone\s*15(?:\s*plus)?\b/.test(m)) {
    return pickAllowed(base, ['preto', 'azul', 'verde', 'amarelo', 'rosa']);
  }
  if (/\biphone\s*14\s*pro(?:\s*max)?\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'space black', 'grafite'].includes(base)) c = 'space black';
    else if (['prata', 'silver', 'branco', 'white'].includes(base)) c = 'silver';
    else if (['dourado', 'gold'].includes(base)) c = 'gold';
    else if (['roxo', 'purple', 'deep purple'].includes(base)) c = 'deep purple';
    return pickAllowed(c, ['space black', 'silver', 'gold', 'deep purple']);
  }
  if (/\biphone\s*14(?:\s*plus)?\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'midnight', 'meia-noite'].includes(base)) c = 'midnight';
    else if (['branco', 'white', 'starlight', 'estelar'].includes(base)) c = 'starlight';
    return pickAllowed(c, ['midnight', 'starlight', 'azul', 'roxo', 'vermelho', 'amarelo']);
  }
  if (/\biphone\s*13\s*pro(?:\s*max)?\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'grafite', 'graphite', 'cinza espacial'].includes(base)) c = 'grafite';
    else if (['branco', 'white', 'prata', 'silver'].includes(base)) c = 'prata';
    else if (['azul', 'blue', 'azul sierra', 'sierra blue'].includes(base)) c = 'azul sierra';
    else if (['verde', 'green', 'alpine green', 'verde alpine'].includes(base)) c = 'verde alpine';
    return pickAllowed(c, ['grafite', 'prata', 'dourado', 'azul sierra', 'verde alpine']);
  }
  if (/\biphone\s*13(?:\s*mini)?\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'midnight', 'meia-noite'].includes(base)) c = 'midnight';
    else if (['branco', 'white', 'starlight', 'estelar'].includes(base)) c = 'starlight';
    return pickAllowed(c, ['midnight', 'starlight', 'azul', 'rosa', 'verde', 'vermelho']);
  }
  if (/\biphone\s*12\s*pro(?:\s*max)?\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'grafite'].includes(base)) c = 'grafite';
    else if (['branco', 'white', 'prata'].includes(base)) c = 'prata';
    return pickAllowed(c, ['grafite', 'prata', 'dourado', 'azul']);
  }
  if (/\biphone\s*(?:12(?:\s*mini)?|11)\b/.test(m)) {
    return pickAllowed(base, ['preto', 'branco', 'azul', 'verde', 'roxo', 'vermelho', 'amarelo']);
  }
  if (/\biphone\s*se\b/.test(m)) {
    let c = base;
    if (['preto', 'black', 'midnight', 'meia-noite'].includes(base)) c = 'midnight';
    else if (['branco', 'white', 'starlight', 'estelar'].includes(base)) c = 'starlight';
    return pickAllowed(c, ['midnight', 'starlight', 'vermelho']);
  }

  return base;
}
function identityOf(offer) {
  let model = normalize(offer.modelo).replace(/\s+/g, ' ')
    .replace(/\bpromax\b/g, 'pro max').replace(/(\d)(cpo)\b/g, '$1 $2')
    .replace(/\b(\d*)gera\s+o\b/g, (_, d) => (d ? `${d} geracao` : 'geracao'))
    .replace(/\bs\s+rie\b/g, 'serie');
  let capacity = normalize(offer.capacidade).replace(/\s+/g, '').replace(/(\d+)t\b/g, '$1tb');
  model = model.replace(/\b(\d+)\s*t\b/g, '$1tb');
  const embedded = model.match(/\b(\d+)\s*(gb|tb)\b/);
  if ((!capacity || capacity === '-') && embedded) capacity = embedded[1] + embedded[2];
  if (embedded && capacity === embedded[1] + embedded[2]) model = model.replace(embedded[0], ' ');
  let condition = normalize(offer.condicao || 'Novo / Lacrado');
  const flags = `${model} ${condition}`;
  if (/\bcpo\b/.test(flags)) condition = 'CPO';
  else if (/\bativad[oa]\b/.test(flags)) condition = 'Ativado';
  else if (/\bsemi[\s-]*nov|vitrine|swap|usad[oa]|as\s*is\b/.test(flags)) condition = 'Seminovo';
  else if (/\bdeslacrad[oa]|abert[oa]|open\s*box\b/.test(flags)) condition = 'Deslacrado';
  else condition = 'Novo / Lacrado';
  if (/\b(pencil|airtag|magic\s*mouse|magic\s*trackpad|trackpad|teclado|magic\s*keyboard|smart\s*keyboard|fonte|cabo)\b/.test(model)) {
    model = model
      .replace(/\bteclado\s+m(?:\s+|a)gico\b/g, 'magic keyboard')
      .replace(/\bteclado\s+magic(?:o)?\b/g, 'magic keyboard')
      .replace(/\bcom\s+teclado\s+num(?:\s+|e)rico\b/g, 'numerico')
      .replace(/\bnumera\s*o\b|\bnum\s*rico\b|\bnumerico\b/g, 'numerico')
      .replace(/\bcaneta\s+pencil\b/g, 'apple pencil')
      .replace(/^pencil\b/g, 'apple pencil')
      .replace(/^apple\s+(?=airtag|magic|smart|fonte|cabo)/, '')
      .replace(/\b(\d+)\s*[aª]?\s*g(?:eracao|er)?\b/g, '$1 geracao')
      .replace(/\bgen\s*(\d+)\b/g, '$1 geracao')
      .replace(/\busbc\b|\busb\s+c\b/g, 'usb-c')
      .replace(/\b00\b/g, ' ');
    if (/\bapple\s+pencil\s+usb(?:-c)?\b/.test(model)) model = 'apple pencil usb-c';
    else if (/\bapple\s+pencil\s+pro\b/.test(model)) model = 'apple pencil pro';
    else if (/\bapple\s+pencil\s+2\b/.test(model)) model = 'apple pencil 2';
    else if (/\bapple\s+pencil\s+1\b/.test(model)) model = 'apple pencil 1';
    else if (/\bmagic\s+mouse\b/.test(model)) {
      model = /\busb-c\b/.test(model) ? 'magic mouse 2 usb-c' : 'magic mouse 2';
    } else if (/\bairtag\b/.test(model)) {
      const isGen2 = /\b2\s*geracao\b|^airtag 2$/.test(model);
      const is4Pack = /\b(?:4\s*pack|4pack|com\s*4|pack\s*com\s*4)\b|\bairtag\s+4\b/.test(model);
      if (isGen2 && is4Pack) model = 'airtag 2 geracao 4 pack';
      else if (isGen2) model = 'airtag 2 geracao 1 pack';
      else if (is4Pack) model = 'airtag 4 pack';
      else model = 'airtag 1 pack';
    } else if (/\bcabo\b/.test(model) && /\b(?:usb-c|tipoc|15\s*e\s*16)\b/.test(model) && /\b2\s*m(?:etro)?\b/.test(model)) {
      model = 'cabo original usb-c 2m';
    } else if (/\bfonte\s+original\b/.test(model)) {
      model = model.replace(/\b(?:com\s+garantia\s+1\s+ano|r\s*\d+|-?\s*apple)\b/g, ' ').replace(/\s+/g, ' ').trim();
    } else if (/\bmagic\s+keyboard\b/.test(model)) {
      model = model
        .replace(/\bde\s+(?:(\d+(?:\.\d+)?)\s+)?polegadas\b/g, (_, p) => (p ? `${p}"` : ' '))
        .replace(/\b(?:wi-fi|apple|portugues|ingles|abnt\d*|br|us|ll\/a|lla)\b/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
      const sizeMatch = model.match(/\b(10\.9|11|12\.9|13)(?:\s*"|\b)/);
      if (/\bipad\s+(air|pro)\b/.test(model)) {
        model = model.trim();
      } else if (/\bipad\b/.test(model) || sizeMatch) {
        model = sizeMatch ? `magic keyboard ${sizeMatch[1]}"` : 'magic keyboard ipad';
      } else if (!/\bfolio\b/.test(model)) {
        const hasTouch = /\btouch\s*id\b/.test(model);
        const hasNum = /\bnumerico\b/.test(model);
        if (hasTouch && hasNum) model = 'magic keyboard touch id numerico';
        else if (hasNum) model = 'magic keyboard numerico';
        else if (hasTouch) model = 'magic keyboard touch id';
        else model = 'magic keyboard';
      }
    } else if (/\bsmart\s+keyboard\b/.test(model)) {
      model = model.replace(/\s*wi-fi\b/g, ' ');
    }
  } else if (/\biphone\b/.test(model)) {
    model = model.replace(/^celular\s+/, '')
      .replace(/\biphone\s*\/\s*(\d{1,2})\b/g, 'iphone $1')
      .replace(/\biphone\s*(?:1[78]\s+)?(?:duo|dou|fold)\b/g, 'iphone duo')
      .replace(/\biphone\s*(\d{2})\s+e\b/g, 'iphone $1e')
      .replace(/\b(\d{1,2})\s*pm\b/g, '$1 pro max')
      .replace(/\b(\d{1,2})(pro\s*max|promax|pro|plus|air|mini)\b/g, '$1 $2')
      .replace(/\bpromax\b/g, 'pro max')
      .replace(/\b1 chip f(?:i| )sico\s*\+\s*1 virtual\b/g, ' ')
      .replace(/\bnano[\s-]*sim\b/g, ' ')
      .replace(/\bdual[\s-]*sim\b/g, ' ')
      .replace(/\bchip\b/g, ' ')
      .replace(/\bf[ií]?s[i]?c[oa]?\b/g, ' ')
      .replace(/\bvirtual\b/g, ' ')
      .replace(/\b(?:gb|tb)\b/g, ' ')
      .replace(/\b(?:chin\s+s|japon\s+s)\b/g, ' ')
      .replace(/\b(?:e[ -]?sim|americano|americana|global|[a-z]{2}\/a|lla|ll|hn|ah|ch|za|zp|ja|br|bz|be|ql|zd|lz|kh|th|ta|vc|aa|hk|jp|in|cn|ae|anatel|dubai|japon s|japones|indiano|chines|cpo|ativad[oa]|lacrado|bordo|top)\b/g, ' ');
    if (/\biphone\s+duo\b/.test(model)) {
      model = 'iphone duo';
    } else {
      const canonicalMatches = [...model.matchAll(/\biphone\s+(?:1[1-8]e?|se(?:\s*[23])?|xs(?:\s*max)?|xr?|[78])(?:\s+(?:pro\s+max|pro|plus|air|mini))?\b/g)];
      if (canonicalMatches.length) {
        model = canonicalMatches[canonicalMatches.length - 1][0];
      }
    }
  } else if (/\b(macbook|imac|mac\s*mini|mac\s*studio)\b/.test(model)) {
    const cleanMacStr = s => s
      .replace(/\b(\d+)\s*t\b/g, '$1tb')
      .replace(/\b\d+\s*c?\s*(?:cpu|gpu)\b/g, ' ')
      .replace(/\b(?:mod\.?\s*)?a\d{4}\b/g, ' ')
      .replace(/\b(?:lla|ll\/a|outras\s+configura(?:c|ç)?(?:o|õ)?es|outras\s+configura\s+es)\b/g, ' ');
    model = cleanMacStr(model)
      .replace(/\b13(?:\s+|\.)6\b/g, '13')
      .replace(/\b14(?:\s+|\.)2\b/g, '14')
      .replace(/\b15(?:\s+|\.)3\b/g, '15')
      .replace(/\b16(?:\s+|\.)2\b/g, '16')
      .replace(/\bmacbook\s+(m\d(?:\s+(?:pro|max|ultra))?)\s+(air|pro)\b/g, 'macbook $2 $1')
      .replace(/\b(m\d)(pro|max|ultra)\b/g, '$1 $2')
      .replace(/\bchip[\s-]*(pro|max|ultra)\b/g, ' $1 ')
      .replace(/\b(m\d)\s+(pro|max|ultra)(?:\s+\2)+\b/g, '$1 $2');
    let rawLine = cleanMacStr(normalize(offer.linhaOriginal || ''));
    let ram = '';
    const capPair = capacity.match(/^(\d{1,3})\/(\d+(?:gb|tb))$/);
    if (capPair) {
      ram = capPair[1];
      capacity = capPair[2];
    }
    const ramSlash = model.match(/\/\s*(8|16|18|24|32|36|48|64|96|128)\b/)
      || model.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*\//)
      || rawLine.match(/\/\s*(8|16|18|24|32|36|48|64|96|128)\b/)
      || rawLine.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*\//);
    const ramWord = model.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*(?:gb)?[\s-]*ram\b/) || rawLine.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*(?:gb)?[\s-]*ram\b/);
    const ramGb = (capacity && capacity !== '-') ? (model.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*gb\b/) || rawLine.match(/\b(8|16|18|24|32|36|48|64|96|128)\s*gb\b/)) : null;
    if (!ram && ramSlash) ram = ramSlash[1];
    if (!ram && ramWord) ram = ramWord[1];
    if (!ram && ramGb) ram = ramGb[1];

    // Remove RAM and core-count descriptors before matching screen inches so /24 or 16/ isn't mistaken for screen size
    const stripMacDescriptors = s => s
      .replace(/\/\s*(?:8|16|18|24|32|36|48|64|96|128)\b/g, ' ')
      .replace(/\b(?:8|16|18|24|32|36|48|64|96|128)\s*\//g, ' ')
      .replace(/\b(?:8|16|18|24|32|36|48|64|96|128)\s*(?:gb)?[\s-]*ram\b/g, ' ')
      .replace(/\b(?:\d{1,3}\s*\/\s*)?\d+\s*(?:gb|tb)\b/g, ' ')
      .replace(/\b(?:128|256|512|1024)\b/g, ' ')
      .replace(/(?:^|\s)[-/]*ram\b/g, ' ')
      .replace(/\b\d+\s*[-]?\s*cores?\s*(?:cpu|gpu)?\b/g, ' ')
      .replace(/\b\d+\s*c?\s*(?:cpu|gpu)\b/g, ' ')
      .replace(/\b(?:ssd|chip|gb|tb|polegadas?|inch|cpo|202[0-9])\b/g, ' ');
    model = stripMacDescriptors(model)
      .replace(/\b(m\d(?:\s+(?:pro|max|ultra))?)\s+[236]\s+(?=(?:13|14|15|16)\b)/g, '$1 ')
      .replace(/\s+[\/-]+\s+/g, ' ')
      .replace(/(?:^|\s)[\/-]+(?=\s|$)/g, ' ');
    rawLine = stripMacDescriptors(rawLine);
    if (ramGb) model = model.replace(ramGb[0], ' ');
    model = model
      .replace(/\b(pro|max|ultra)(?:\s+\1)+\b/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();

    let inches = '';
    const isMiniOrStudio = /\bmac\s*(?:mini|studio)\b/.test(model);
    const isImac = /\bimac\b/.test(model);
    const isAir = /\bmacbook\s+air\b/.test(model);
    const isNeo = /\bmacbook\s+neo\b/.test(model);
    const imacDash = model.match(/\b(m\d)-(24)\b/);
    const tInch = !isMiniOrStudio ? model.match(/\bt(13|14|15|16)\b/) : null;
    const allowedScreenPat = isImac ? '(24|27)' : (isAir ? '(13(?:\\.6)?|15(?:\\.3)?)' : '(13(?:\\.6)?|14(?:\\.2)?|15(?:\\.3)?|16(?:\\.2)?)');
    const expRe = new RegExp(`\\b${allowedScreenPat}(?:\\s*-?\\s*inch|")\\b`);
    const bareRe = new RegExp(`(?<!\\/\\s*)\\b${allowedScreenPat}\\b(?!\\s*(?:gb|ram|tb|\\/))`);
    const explicitInch = !isMiniOrStudio ? (model.match(expRe) || rawLine.match(expRe)) : null;
    const bareInch = !isMiniOrStudio ? (model.match(bareRe) || rawLine.match(bareRe)) : null;
    if (imacDash) {
      inches = imacDash[2];
      model = model.replace(imacDash[0], imacDash[1]);
    } else if (tInch) {
      inches = tInch[1];
      model = model.replace(tInch[0], ' ');
    } else if (explicitInch) {
      inches = explicitInch[1].split('.')[0];
    } else if (bareInch) {
      inches = bareInch[1].split('.')[0];
    } else if (isAir || isNeo) {
      inches = '13';
    } else if (isImac) {
      inches = '24';
    }
    if (inches && !isMiniOrStudio) {
      model = model.replace(new RegExp(`\\b${inches}(?:\\.\\d+)?(?:\\s*-?\\s*inch\\b|")?`, 'g'), ' ').replace(/"/g, ' ');
      model = model
        .replace(/(?:^|\s)[\/-]+(?=\s|$)/g, ' ')
        .replace(/\b(pro|max|ultra)(?:\s+\1)+\b/g, '$1')
        .replace(/\s+/g, ' ')
        .trim();
      model = `${model} ${inches}"`;
    } else {
      model = model
        .replace(/"/g, ' ')
        .replace(/(?:^|\s)[\/-]+(?=\s|$)/g, ' ')
        .replace(/\b(pro|max|ultra)(?:\s+\1)+\b/g, '$1')
        .replace(/\s+/g, ' ')
        .trim();
    }
    if (ram && capacity && capacity !== '-') {
      capacity = `${ram}gb ram · ${capacity}`;
    }
  } else if (/\bipad\b/.test(model)) {
    const rawLine = normalize(offer.linhaOriginal || '');
    const connSource = `${model} ${rawLine}`;
    const isCellular = /(?:\+|wi-?fi\s*\+?)\s*cel(?:l(?:ular)?|ular)?\b|\b(?:celular|cellular|5g|4g|lte|e-?sim|com\s+chip)\b/.test(connSource);
    model = model
      .replace(/\b(?:gb|tb)\b/g, ' ')
      .replace(/\s+/g, ' ')
      .replace(/\bwifi\b/g, 'wi-fi')
      .replace(/\bwi-fi\s*(?:\+?\s*(?:cel(?:l(?:ular)?|ular)?|5g|4g|lte))?\b/g, ' ')
      .replace(/\b(?:celular|cellular|5g|4g|lte|e-?sim|com\s+chip|202[0-9]|cpo|lla|ll\/a|a\d{4})\b/g, ' ')
      .replace(/\+\s*(?:cel(?:l)?)\b/g, ' ')
      .replace(/\+/g, ' ')
      .replace(/\b(\d+)(?:st|nd|rd|th)\b(?:\s+(?:generation|gen)\b)?/g, '$1')
      .replace(/\b(\d+)\s+(?:geracao|generation)\b/g, '$1')
      .replace(/\b(12)\s+9(?=\s*-?\s*inch\b)/g, '$1.9')
      .replace(/\bipad\s+air\s*3\s*(?:ger|geracao)?\b/g, 'ipad air 3')
      .replace(/\bipad\s+mini\s*(?:7(?:\s+a17(?:\s+pro)?)?|a17(?:\s+pro)?)\b/g, 'ipad mini 7')
      .replace(/\bipad\s+(?:11th|11|a16)(?:\s+a16)?\b/g, 'ipad 11 a16 11');
    let inches = '';
    const tInch = model.match(/\bt(11|13)\b/);
    const explicitInch = model.match(/\b(8\.3|10\.9|11|12\.9|13)(?:\s*-?\s*inch\b|")/);
    const bareInch = /\bipad\s+(?:pro|air|11)\b/.test(model) ? model.match(/\b(11|13)\b/) : null;
    if (tInch) {
      inches = tInch[1];
      model = model.replace(tInch[0], ' ');
    } else if (explicitInch) {
      inches = explicitInch[1];
    } else if (bareInch) {
      inches = bareInch[1];
    } else if (/\bipad\s+mini\b/.test(model)) {
      inches = '8.3';
    }
    if (inches) {
      if (inches === '11' && /\bipad\s+11\b/.test(model)) {
        model = model.replace(/\bipad\s+11\b/, 'ipad 11_GEN');
        model = model.replace(/\b11(?:\s*-?\s*inch|")?\b/g, ' ');
        model = model.replace('ipad 11_GEN', 'ipad 11');
      } else {
        model = model.replace(new RegExp(`\\b${inches.replace('.', '\\.')}(?:\\s*-?\\s*inch\\b|")?`, 'g'), ' ');
      }
      model = `${model.replace(/"/g, ' ').replace(/\s*-\s*$/,'').replace(/\s+-\s+/g, ' ').replace(/\s+/g, ' ').trim()} ${inches}"`;
    }
    model = `${model.replace(/\s+/g, ' ').trim()} ${isCellular ? 'wi-fi + cellular' : 'wi-fi'}`;
  } else if (/\bwatch\b/.test(model)) {
    const rawLine = normalize(offer.linhaOriginal || '');
    const isCellular = /\bultra\b/.test(model) || /\b(?:cell|celular|cellular|celula|lte|5g)\b|\+\s*cell\b/.test(`${model} ${rawLine}`);
    model = model
      .replace(/\bserie\s+apple\s+watch\b/g, 'apple watch')
      .replace(/(?:\bapple\s+watch\s+)+/g, 'apple watch ')
      .replace(/\b(?:series?|s)\s*(\d{1,2})\b/g, 's$1')
      .replace(/\b(se)\s*(\d)\s*g(?:eracao|er)?\b/g, '$1 $2')
      .replace(/\bse\s*([234])\b/g, 'se $1')
      .replace(/\bger(?:acao)?\b/g, ' ')
      .replace(/\b(?:caixa(?:\s+preta)?|pulseira\s+(?:de\s+\w+|loop\s*\w*)?|ocean\s+band|trail\s+loop|alpine\s+loop|milanese\s+loop|sport\s+band|sport\s+loop|wi-fi(?:\s*\+\s*5g)?)\b/g, ' ')
      .replace(/(?:\+\s*cell\b|\b(?:gps\s*\+\s*celular|gps\s*\+\s*cellular|gps|celular|cellular|celula|lte|5g|silicone|titanium|aluminio|sb|s\/m|m\/l|lla|ll\/a|a\d{4})\b)/g, ' ')
      .replace(/[\/+-]+/g, ' ');
    const mmMatch = model.match(/\b(38|40|41|42|44|45|46|49)\s*(?:mm)?\b/) || rawLine.match(/\b(38|40|41|42|44|45|46|49)\s*mm\b/);
    if (mmMatch) {
      const mm = mmMatch[1];
      model = model.replace(new RegExp(`\\b${mm}\\s*(?:mm)?\\b`, 'g'), ' ');
      if (!capacity || capacity === '-') capacity = `${mm}mm`;
    }
    const watchCore = model.match(/\bapple\s+watch\s+(ultra(?:\s*[2-9])?|s(?:1[0-9]|9)|se(?:\s*[2-9])?)\b/);
    if (watchCore) {
      model = `apple watch ${watchCore[1].replace(/\s+/g, ' ')} ${isCellular ? 'gps + cellular' : 'gps'}`;
    } else {
      model = `${model.replace(/\s+/g, ' ').trim()} ${isCellular ? 'gps + cellular' : 'gps'}`;
    }
  } else if (/\b(?:airpods|earpods)\b/.test(model)) {
    model = model
      .replace(/^apple\s+/, '')
      .replace(/\b(?:00|mod\.?\s*a\d+|a\d{4}|ll\/a|lla|ll|smart\s*case|lacrado|garantia\s+expirada|with|com)\b/g, ' ')
      .replace(/\b(\d+)\s*ger(?:acao)?\b/g, '$1')
      .replace(/\bger(?:acao)?\b/g, ' ')
      .replace(/\bairpods\s+3\s+pro\b/g, 'airpods pro 3')
      .replace(/\bairpods\s+pro\s+3\b.*/g, 'airpods pro 3')
      .replace(/\bairpods\s+4\s+(?:normal|regular|wireless|charging|case)+\b/g, 'airpods 4')
      .replace(/\bairpods\s+3\s+(?:normal|regular)\b/g, 'airpods 3')
      .replace(/\bairpods\s+(\d)\s+\1\b/g, 'airpods $1');
    if (/\bairpods\s+max\b/.test(model)) {
      const mMax = model.match(/\bmax\s*([1-9])\b/);
      model = mMax ? `airpods max ${mMax[1]} usb-c` : (/lightning/.test(model) ? 'airpods max 1 usb-c' : 'airpods max 2 usb-c');
    } else if (/\bpro(?:\b|\d)/.test(model)) {
      const pro = model.match(/\bpro\s*([1-3])\b|\b([1-3])\s*pro\b/);
      model = pro ? `airpods pro ${pro[1] || pro[2]}` : '';
    } else if (/\bairpods\s+([5-9])\b/.test(model)) {
      const gen = model.match(/\bairpods\s+([5-9])\b/)[1];
      model = /\b(?:anc|cancelamento|noise\s*cancellation|wireless|sem\s*fio|inducao)\b/.test(model) ? `airpods ${gen} anc` : `airpods ${gen}`;
    } else if (/\bairpods\s+4\b/.test(model)) {
      model = /\b(?:anc|cancelamento|noise\s*cancellation)\b/.test(model) ? 'airpods 4 anc' : 'airpods 4';
    } else if (/\bairpods\s+([23])\b/.test(model)) {
      model = `airpods ${model.match(/\bairpods\s+([23])\b/)[1]}`;
    }
  }
  model = model.replace(/\s+/g, ' ').trim();
  return {model, capacity, condition};
}
export function swatch(value) {
  const name = normalize(value);
  if (/cosmic\s*orange|laranja|orange/.test(name)) return '#f97316';
  if (/deep\s*blue|azul\s*escuro|midnight\s*blue|navy/.test(name)) return '#1e3a8a';
  if (/blue\s*titanium|azul\s*titanio/.test(name)) return '#3b4d61';
  if (/sky\s*blue/.test(name)) return '#38bdf8';
  if (/mist\s*blue|glacier|sierra/.test(name)) return '#93c5fd';
  if (/azul|blue/.test(name)) return '#2563eb';
  if (/burgundy|vinho|bordo/.test(name)) return '#731e35';
  if (/vermelho|red/.test(name)) return '#dc2626';
  if (/verde\s*alpine|alpine/.test(name)) return '#4b5d4e';
  if (/verde\s*acinzentado|teal/.test(name)) return '#4f8a8b';
  if (/sage/.test(name)) return '#84a98c';
  if (/verde|green/.test(name)) return '#22c55e';
  if (/deep\s*purple/.test(name)) return '#584266';
  if (/lavender|lavanda|roxo|purple/.test(name)) return '#a78bfa';
  if (/rose\s*gold|ouro\s*rosa/.test(name)) return '#e5a9a9';
  if (/soft\s*pink|rosa|pink|blush/.test(name)) return '#f472b6';
  if (/amarelo|yellow/.test(name)) return '#facc15';
  if (/bronze/.test(name)) return '#8c6239';
  if (/desert|areia/.test(name)) return '#c2a882';
  if (/light\s*gold|gold|dourado/.test(name)) return '#d8b44c';
  if (/preto|black|midnight|meia[\s-]*noite|night\s*sky/.test(name)) return '#111827';
  if (/grafite|graphite/.test(name)) return '#3f3f46';
  if (/space\s*gray|cinza[\s-]*espacial|slate|ardosia|cinza|gray|grey/.test(name)) return '#6b7280';
  if (/natural/.test(name)) return '#a8a29e';
  if (/prata|prateado|silver/.test(name)) return '#d4d4d8';
  if (/estelar|starlight/.test(name)) return '#f3efe6';
  if (/cloud\s*white|branco|white/.test(name)) return '#ffffff';
  return '#cbd5e1';
}

function parseCapacityMb(capStr) {
  const s = normalize(capStr).split('·').pop().trim();
  const tb = s.match(/(\d+(?:[.,]\d+)?)\s*tb/);
  if (tb) return parseFloat(tb[1].replace(',', '.')) * 1024;
  const gb = s.match(/(\d+(?:[.,]\d+)?)\s*gb/);
  if (gb) return parseFloat(gb[1].replace(',', '.'));
  const mm = s.match(/(\d+)\s*mm/);
  if (mm) return parseInt(mm[1], 10);
  return 0;
}

function rankDeviceGroup(catKey, group, m, cond, cap) {
  // iPhone: iPhone Duo primeiro -> geração decrescente -> Pro Max -> Pro -> Plus -> Air -> padrão -> e -> mini
  if (catKey === 'iphone') {
    if (!cap || !/\biphone\s*(?:\d|duo\b)/.test(m)) return [8, 0, 0, 0, 0, 0, 0, m];
    const isDuo = /\bduo\b/.test(m);
    const genMatch = m.match(/\biphone\s*(\d{1,2})(?:e)?\b/);
    let gen = isDuo ? 19 : (genMatch ? parseInt(genMatch[1], 10) : 0);
    if (!gen) {
      if (/\bxs\b|\bxr\b|\bx\b/.test(m)) gen = 10;
      else if (/\bse\b/.test(m)) gen = 9;
    }
    let tier = 5;
    if (isDuo) tier = 0;
    else if (/\bpro\s*max\b/.test(m)) tier = 1;
    else if (/\bpro\b/.test(m)) tier = 2;
    else if (/\bplus\b/.test(m)) tier = 3;
    else if (/\bair\b/.test(m)) tier = 4;
    else if (/\b\d{1,2}e\b/.test(m)) tier = 6;
    else if (/\bmini\b/.test(m)) tier = 7;

    let sub = 1;
    if (/\bativado\b/.test(`${m} ${cond}`)) sub = 2;
    else if (/\bcpo\b/.test(`${m} ${cond}`)) sub = 3;
    else if (cond.includes('semi') || /\bseminov/.test(m)) sub = 4;

    return [1, -gen, tier, sub, -cap, 0, 0, m];
  }

  // Mac / iMac: geração de chip mais recente primeiro (M6 -> M5 -> Neo -> M4 -> M2) -> Pro/Air/Studio/iMac/Mini -> tela -> RAM -> SSD
  if (catKey === 'mac' || catKey === 'imac') {
    const chipMatch = m.match(/\bm(\d)\b/);
    const chip = chipMatch ? parseInt(chipMatch[1], 10) : (/\bmacbook\s*neo\b/.test(m) ? 5 : 0);
    let line = 7;
    if (/macbook\s*pro/.test(m)) line = 1;
    else if (/macbook\s*air/.test(m)) line = 2;
    else if (/mac\s*studio/.test(m)) line = 3;
    else if (/\bimac\b/.test(m)) line = 4;
    else if (/mac\s*mini/.test(m)) line = 5;
    else if (/macbook\s*neo/.test(m)) line = 6;

    let chipTier = 4;
    if (/\bm\d\s+ultra\b/.test(m)) chipTier = 1;
    else if (/\bm\d\s+max\b/.test(m)) chipTier = 2;
    else if (/\bm\d\s+pro\b/.test(m)) chipTier = 3;

    const screenMatch = m.match(/\b(27|24|16|15|14|13)"/);
    const screen = screenMatch ? parseInt(screenMatch[1], 10) : 0;
    const ramMatch = normalize(group.capacity).match(/\b(\d+)\s*gb\s*ram\b/);
    const ram = ramMatch ? parseInt(ramMatch[1], 10) : 0;
    return [2, -chip, line, chipTier, -screen, -ram, -cap, m];
  }

  // iPad: lançamento mais recente primeiro (M5 -> Air M4 -> Air M3 / 11 -> Mini 7 -> Pro M4 -> Mini 6 -> Air 3)
  if (catKey === 'ipad') {
    let wave = 0;
    if (/\bm6\b/.test(m)) wave = 270;
    else if (/\bpro\b.*\bm5\b|\bm5\b/.test(m)) wave = 260;
    else if (/\bair\b.*\bm4\b/.test(m)) wave = 259;
    else if (/\bair\b.*\bm3\b/.test(m)) wave = 252;
    else if (/\bipad\s*11\b|\ba16\b/.test(m)) wave = 250;
    else if (/\bmini\s*7\b/.test(m)) wave = 245;
    else if (/\bpro\b.*\bm4\b/.test(m)) wave = 242;
    else if (/\bm4\b/.test(m)) wave = 240;
    else if (/\bm3\b/.test(m)) wave = 235;
    else if (/\bm2\b/.test(m)) wave = 225;
    else if (/\bipad\s*10\b/.test(m)) wave = 222;
    else if (/\bmini\s*6\b/.test(m)) wave = 215;
    else if (/\bm1\b/.test(m)) wave = 210;
    else if (/\bair\s*3\b/.test(m)) wave = 190;
    else {
      const chipMatch = m.match(/\bm(\d)\b/);
      const genMatch = m.match(/\b(\d{1,2})\b/);
      wave = chipMatch ? 200 + parseInt(chipMatch[1], 10) * 10 : (genMatch ? 180 + parseInt(genMatch[1], 10) : 100);
    }
    let line = 3;
    if (/\bpro\b/.test(m)) line = 1;
    else if (/\bair\b/.test(m)) line = 2;
    else if (/\bmini\b/.test(m)) line = 4;
    const screenMatch = m.match(/\b(13|12\.9|11|10\.9|8\.3)"/);
    const screen = screenMatch ? parseFloat(screenMatch[1]) : 0;
    const cell = /cellular/.test(m) ? 2 : 1;
    return [3, -wave, line, cell, -screen, -cap, 0, m];
  }

  // Apple Watch: geração/lançamento mais recente primeiro (Ultra 4 -> S12 -> Ultra 3 -> S11 -> SE 3 -> S10 -> Ultra 2 -> SE 2 -> SE)
  if (catKey === 'watch') {
    let wave = 15;
    let line = 2;
    if (/\bultra\s*4\b/.test(m)) { wave = 26; line = 1; }
    else if (/\bs12\b/.test(m)) { wave = 26; line = 2; }
    else if (/\bse\s*4\b/.test(m)) { wave = 26; line = 3; }
    else if (/\bultra\s*3\b/.test(m)) { wave = 25; line = 1; }
    else if (/\bs11\b/.test(m)) { wave = 25; line = 2; }
    else if (/\bse\s*3\b/.test(m)) { wave = 25; line = 3; }
    else if (/\bs10\b/.test(m)) { wave = 24; line = 2; }
    else if (/\bultra\s*2\b/.test(m)) { wave = 23; line = 1; }
    else if (/\bs9\b/.test(m)) { wave = 23; line = 2; }
    else if (/\bse\s*2\b/.test(m)) { wave = 22; line = 3; }
    else if (/\bultra\b/.test(m)) { wave = 22; line = 1; }
    else if (/\bse\b/.test(m)) { wave = 20; line = 3; }
    else {
      const sMatch = m.match(/\bs(\d{1,2})\b/);
      if (sMatch) wave = 13 + parseInt(sMatch[1], 10);
    }
    const cell = /cellular/.test(m) ? 2 : 1;
    return [4, -wave, line, -cap, cell, 0, 0, m];
  }

  // AirPods: lançamento mais recente primeiro (Pro N -> N ANC -> N -> Max 2 -> 4 ANC -> 4 -> Pro 2 -> 3 -> Max 1 -> 2 -> EarPods)
  if (catKey === 'airpods') {
    let wave = 10;
    const proGen = m.match(/\bairpods\s*pro\s*([3-9])\b/);
    const genAnc = m.match(/\bairpods\s*([5-9])\s*anc\b/);
    const genStd = m.match(/\bairpods\s*([5-9])\b/);
    if (proGen) wave = 23 + parseInt(proGen[1], 10);
    else if (genAnc) wave = 20.4 + parseInt(genAnc[1], 10);
    else if (genStd) wave = 20.2 + parseInt(genStd[1], 10);
    else if (/\bairpods\s*max\s*2\b/.test(m)) wave = 25;
    else if (/\bairpods\s*4\s*anc\b/.test(m)) wave = 24.2;
    else if (/\bairpods\s*4\b/.test(m)) wave = 24.0;
    else if (/\bairpods\s*pro\s*2\b/.test(m)) wave = 23;
    else if (/\bairpods\s*3\b/.test(m)) wave = 21.2;
    else if (/\bairpods\s*max\s*1\b/.test(m)) wave = 21.0;
    else if (/\bairpods\s*2\b/.test(m)) wave = 19;
    return [5, -wave, 0, 0, 0, 0, -cap, m];
  }

  // Acessórios: lançamentos mais recentes e linha Pro/iPad primeiro -> periféricos Mac -> gerações anteriores -> fontes -> cabos
  if (catKey === 'accessories') {
    let wave = 10;
    if (/\bairtag\s*2\b/.test(m)) wave = 26;
    else if (/\bapple\s*pencil\s*pro\b/.test(m)) wave = 25;
    else if (/\bmagic\s*keyboard\b.*\b(13|12\.9|11|ipad)\b/.test(m)) wave = 24;
    else if (/\bmagic\s*mouse\s*2\s*usb-c\b/.test(m)) wave = 23.5;
    else if (/\bmagic\s*keyboard\b/.test(m)) wave = 23;
    else if (/\bmagic\s*trackpad\b/.test(m)) wave = 22.5;
    else if (/\bapple\s*pencil\s*usb-c\b/.test(m)) wave = 22;
    else if (/\bairtag\b/.test(m)) wave = 21;
    else if (/\bmagic\s*mouse\b/.test(m)) wave = 20.5;
    else if (/\bapple\s*pencil\b/.test(m)) wave = 20;
    else if (/\bsmart\s*keyboard\b/.test(m)) wave = 19;
    else if (/\bfonte\b/.test(m)) wave = 12;
    return [6, -wave, 0, 0, 0, 0, group.minimum, m];
  }

  return [9, 0, 0, 0, 0, 0, group.minimum, m];
}

function modelSortRank(group) {
  const m = normalize(group.model);
  const cond = normalize(group.condition);
  const cap = parseCapacityMb(group.capacity);
  if (group.category === 'cpo' || group.category === 'seminovos') {
    let innerCat = 'accessories';
    if (/\biphone\b/.test(m)) innerCat = 'iphone';
    else if (/\bimac\b/.test(m)) innerCat = 'imac';
    else if (/\b(macbook|mac\s*(?:mini|studio))\b/.test(m)) innerCat = 'mac';
    else if (/\bipad\b/.test(m)) innerCat = 'ipad';
    else if (/\bwatch\b/.test(m)) innerCat = 'watch';
    else if (/\b(?:airpods|earpods)\b/.test(m)) innerCat = 'airpods';
    return rankDeviceGroup(innerCat, group, m, cond, cap);
  }
  return rankDeviceGroup(group.category, group, m, cond, cap);
}

export function modelFamilyLabel(group) {
  const model = String(group?.model || '').trim();
  const cat = group?.category || '';
  if (!model) return 'Outros';
  let label = model;
  if (cat === 'accessories') {
    if (/AirTag 2/i.test(model)) label = 'AirTag 2ª Geração';
    else if (/AirTag/i.test(model)) label = 'AirTag 1ª Geração';
    else if (/Apple Pencil Pro/i.test(model)) label = 'Apple Pencil Pro';
    else if (/Apple Pencil USB-C/i.test(model)) label = 'Apple Pencil USB-C';
    else if (/Apple Pencil/i.test(model)) label = 'Apple Pencil (1ª / 2ª Geração)';
    else if (/Magic Keyboard.*(13|12\.9|11|iPad)/i.test(model)) label = 'Magic Keyboard (iPad)';
    else if (/Magic Keyboard/i.test(model)) label = 'Magic Keyboard (Mac)';
    else if (/Smart Keyboard/i.test(model)) label = 'Smart Keyboard (iPad)';
    else if (/Magic Mouse 2 USB-C/i.test(model)) label = 'Magic Mouse (USB-C)';
    else if (/Magic Mouse/i.test(model)) label = 'Magic Mouse 2';
    else if (/Magic Trackpad/i.test(model)) label = 'Magic Trackpad';
    else if (/Fonte/i.test(model)) label = 'Fontes Originais';
    else label = 'Cabos Originais';
    return label;
  }
  if (cat === 'mac' || cat === 'imac' || /\b(MacBook|iMac|Mac\s+(?:Mini|Studio))\b/i.test(model)) {
    label = model.replace(/\s+\d+(?:\.\d+)?"$/i, '').trim();
  } else if (cat === 'ipad' || /\biPad\b/i.test(model)) {
    const isCell = /\bWi-Fi\s*\+\s*Cellular\b/i.test(model);
    const baseIpad = model.replace(/\s+\d+(?:\.\d+)?"/i, '').replace(/\s+Wi-Fi(?:\s*\+\s*Cellular)?$/i, '').trim();
    label = `${baseIpad} ${isCell ? 'Wi-Fi + Cellular' : 'Wi-Fi'}`;
  } else if (cat === 'watch' || /\bWatch\b/i.test(model)) {
    label = model.replace(/\s+GPS(?:\s*\+\s*Cellular)?$/i, '').trim();
  } else if (cat === 'airpods' || /\b(?:AirPods|EarPods)\b/i.test(model)) {
    if (/AirPods Max 2/i.test(model)) label = 'AirPods Max 2 (USB-C)';
    else if (/AirPods Max 1/i.test(model)) label = 'AirPods Max 1';
    else if (/AirPods 5/i.test(model)) label = 'AirPods 5';
    else if (/AirPods 4/i.test(model)) label = 'AirPods 4';
    else if (/EarPods/i.test(model)) label = 'EarPods';
  }
  return cat === 'cpo' ? `${label} · CPO` : label;
}

export function sortCatalogForDisplay(groups) {
  return [...(groups || [])].sort((a, b) => {
    const ra = modelSortRank(a);
    const rb = modelSortRank(b);
    for (let i = 0; i < ra.length; i++) {
      if (ra[i] < rb[i]) return -1;
      if (ra[i] > rb[i]) return 1;
    }
    return a.minimum - b.minimum;
  });
}
export function catalogSections(groups) {
  const families = new Map();
  for (const group of groups) {
    const family = modelFamilyLabel(group);
    if (!families.has(family)) families.set(family, {family, items: []});
    families.get(family).items.push(group);
  }
  return [...families.values()];
}
const comparePrice = (a,b) => Number(a.preco) - Number(b.preco) || String(a.fornecedor || '').localeCompare(String(b.fornecedor || ''),'pt-BR');
const colorKeyOf = colorKey;
// A lista diária pode chegar novamente após a cotação mais recente.
export function currentCatalogOffers(offers) {
  const latest = new Map(), undated = [];
  for (const offer of offers) {
    if (!offer || !offer.modelo || !(Number(offer.preco) > 0)) continue;
    const supplier = String(offer.telefone || '').replace(/\D/g,'') || normalize(offer.fornecedor);
    if (!supplier || !offer.data || !offer.hora) {
      undated.push(offer);
      continue;
    }
    const {model, capacity} = identityOf(offer);
    const color = colorKeyOf(offer.cor, model, offer.linhaOriginal);
    const key = JSON.stringify([offer.data, supplier, model, capacity, color, normalize(offer.condicao)]);
    const prior = latest.get(key);
    if (!prior || offer.hora > prior.hora) latest.set(key, offer);
  }
  return [...latest.values(), ...undated];
}
export function supplierOffers(offers, colorKey = null) {
  const unique = new Map();
  for (const offer of currentCatalogOffers(offers)) {
    if (colorKey !== null && colorKeyOf(offer.cor, offer.modelo, offer.linhaOriginal) !== colorKeyOf(colorKey, offer.modelo)) continue;
    const identity = String(offer.telefone || '').replace(/\D/g,'') || normalize(offer.fornecedor) || String(offer.id || 'sem-fornecedor');
    const {model, capacity, condition} = identityOf(offer);
    const key = [identity, normalize(offer.modelo), capacity, normalize(offer.condicao) || condition, colorKeyOf(offer.cor, model, offer.linhaOriginal)].join('|');
    if (!unique.has(key) || comparePrice(offer,unique.get(key)) < 0) unique.set(key,offer);
  }
  return [...unique.values()].sort(comparePrice);
}
export function priceNote(price, median, existingNote = '') {
  if (existingNote) return existingNote;
  if (!Number.isFinite(median) || median <= 0) return '';
  const p = Number(price);
  if (!Number.isFinite(p) || p <= 0) return '';
  const pct = Math.round(Math.abs(p - median) / median * 100);
  if (p < median * 0.88) return `Valor ${pct}% abaixo da mediana (${money(median)}) — confirmar preço e condição; referência: ofertas capturadas`;
  if (p > median * 1.20) return `Valor ${pct}% acima da mediana (${money(median)})`;
  return '';
}
export function groupProducts(products) {
  const groups = new Map();
  for (const offer of currentCatalogOffers(products || [])) {
    if (!offer || typeof offer !== 'object' || !offer.modelo || !Number.isFinite(Number(offer.preco)) || Number(offer.preco) <= 0) continue;
    const category = categoryOf(offer);
    const {model, capacity, condition} = identityOf(offer);
    if (!model) continue;
    if (/\b(?:tablet\b|pelle|capinha|oura\s*ring|capinha\s+mause|kit\s+gamer|logitech|multilaser|redragon|hori|ledx\d*|airfryer|hopestar|primeira\s*linha|1a?\s*linha)\b/.test(`${model} ${normalize(offer.modelo)}`)) continue;
    if ((category === 'accessories' || category === 'airpods') && /\d\s*(gb|tb)\b/i.test(capacity)) continue;
    if (category === 'airpods' && Number(offer.preco) < 400) continue;
    if (/\bearpods\b/.test(model) && Number(offer.preco) < 75) continue;
    const ck = colorKey(offer.cor, model, offer.linhaOriginal);
    if (ck === 'cor nao informada') continue;
    const hasStorage = /\b\d+(?:\.\d+)?\s*(?:gb|tb)\b/i.test(capacity);
    if ((category === 'mac' || category === 'imac') && (!/\bram\b/i.test(capacity) || !hasStorage)) continue;
    if (category === 'mac' && /\bmacbook\b/.test(model) && !/\b(13|14|15|16)"/.test(model)) continue;
    if ((category === 'iphone' || category === 'ipad' || ((category === 'cpo' || category === 'seminovos') && /\b(iphone|ipad)\b/.test(model) && !/\b(?:keyboard|teclado|pencil|capa|folio)\b/.test(model))) && !hasStorage) continue;
    if (category === 'seminovos' && /\biphone\b/.test(model)) {
      if (/\b(?:duo|dou|fold)\b/.test(model)) continue;
      const gm = model.match(/\biphone\s*(\d{1,2})(?:e)?\b/);
      const gen = gm ? parseInt(gm[1], 10) : 0;
      if (gen < 13 || gen > 17) continue;
    }
    if (category === 'ipad' && /\bipad\s+(?:pro|air\s*m[2-5])\b/.test(model) && !/\b(10\.9|11|12\.9|13)"/.test(model)) continue;
    if (category === 'watch' && (!/\b\d{2}\s*mm\b/i.test(capacity) || !/\bapple\s+watch\s+(?:ultra(?:\s*[2-9])?|s(?:1[0-9]|9)|se(?:\s*[2-9])?)\b/.test(model))) continue;
    const key = [category,model,capacity,normalize(condition)].join('|');
    if (!groups.has(key)) groups.set(key,{key,category,model:modelLabel(model),capacity:(category === 'accessories' || /\b(?:airpods|earpods)\b/.test(model)) ? '' : capacityLabel(capacity),condition,offers:[],variants:[],minimum:Infinity,median:0});
    const group = groups.get(key);
    group.offers.push(offer);
    group.minimum = Math.min(group.minimum, Number(offer.preco));
  }
  for (const group of groups.values()) {
    const prices = group.offers.map(o => Number(o.preco)).filter(n => Number.isFinite(n) && n > 0).sort((a, b) => a - b);
    const mid = Math.floor(prices.length / 2);
    group.median = prices.length >= 2 ? (prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2) : 0;
    const colors = new Map();
    for (const offer of group.offers) {
      const key = colorKey(offer.cor, identityOf(offer).model, offer.linhaOriginal);
      if (key === 'cor nao informada') continue;
      if (!colors.has(key)) colors.set(key,{key,color:(/\bS11\b/i.test(group.model) ? ({'rose gold':'Ouro rosa','silver':'Prateado','jet black':'Preto brilhante','space gray':'Cinza-espacial','slate':'Ardósia'}[key] || colorLabel(key)) : colorLabel(key)),offers:[],minimum:Infinity,note:''});
      const variant=colors.get(key);
      variant.offers.push(offer);
      const p = Number(offer.preco);
      if (p < variant.minimum) {
        variant.minimum = p;
        variant.note = priceNote(p, group.median, offer.observacao || '');
      }
    }
    group.variants=[...colors.values()].sort((a,b)=>a.minimum-b.minimum||a.color.localeCompare(b.color,'pt-BR'));
  }
  return [...groups.values()].sort((a,b)=>a.minimum-b.minimum||a.model.localeCompare(b.model,'pt-BR',{numeric:true})||a.capacity.localeCompare(b.capacity,'pt-BR',{numeric:true}));
}
