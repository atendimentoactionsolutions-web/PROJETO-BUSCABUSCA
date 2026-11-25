import { CATEGORIES, groupProducts, sortCatalogForDisplay, catalogSections, supplierOffers, money, swatch, colorLabel, effectivePrice, getDfuDelta } from './catalog.js';

const $ = id => document.getElementById(id);
const eyePaths = ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'];
const CATEGORY_ICONS = {
  iphone: ['M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z','M11 18h2'],
  seminovos: ['M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z','M11 18h2','M12 7v4l3 3'],
  mac: ['M4 5h16a1 1 0 0 1 1 1v10H3V6a1 1 0 0 1 1-1Z','M2 18h20'],
  ipad: ['M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z','M12 18h.01'],
  watch: ['M9 2h6v3H9zM9 19h6v3H9z','M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z'],
  airpods: ['M6 12a3 3 0 0 1 6 0v6H9a3 3 0 0 1-3-3v-3Z','M18 12a3 3 0 0 0-6 0v6h3a3 3 0 0 0 3-3v-3Z'],
  accessories: ['M12 2v8M8 6h8M7 10h10l-1 8H8l-1-8Z','M10 18v4M14 18v4'],
  imac: ['M3 4h18v12H3z','M8 20h8M12 16v4'],
  cpo: ['M12 2l3 6 6 .9-4.5 4.4 1 6.2L12 16.5 6.5 19.5l1-6.2L3 8.9 9 8l3-6Z'],
  seminovos: ['M4 4h16v16H4z','M4 9h16','M9 4v16','M12 15l2 2 4-4']
};
let catalog = [], selectedCategory = 'iphone', perPage = 60, limit = 60, refreshing = false, demo = false, serverPriced = false;
let lastPayload = null, selection = null;
function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}
function icon(paths = eyePaths) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');
  svg.setAttribute('aria-hidden','true');
  for (const data of paths) { const path = document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',data); svg.append(path); }
  return svg;
}
function conditionQuestion(condition) {
  const label = String(condition || '').trim();
  switch (label.toLocaleLowerCase('pt-BR')) {
    case 'lacrado':
    case 'novo / lacrado':
      return 'Você confirma que está lacrado de fábrica?';
    case 'seminovo':
      return 'Você confirma que é seminovo?';
    case 'novo':
    case 'novo — lacre não informado':
      return 'Você confirma que é novo? Está lacrado de fábrica ou já foi aberto?';
    case '':
    case '-':
    case 'condição não informada':
      return 'Qual é a condição do produto? Está lacrado de fábrica, é seminovo ou tem outra condição?';
    default:
      return `Você confirma que a condição é “${label}”?`;
  }
}
function renderCategories() {
  const counts = new Map(CATEGORIES.map(([id]) => [id, 0]));
  for (const g of catalog) counts.set(g.category, (counts.get(g.category) || 0) + 1);
  $('categories').replaceChildren(...CATEGORIES.map(([id,label]) => {
    const button = element('button','category-tab'+(id === selectedCategory ? ' active' : ''));
    button.type='button'; button.setAttribute('aria-pressed',String(id === selectedCategory));
    button.append(icon(CATEGORY_ICONS[id] || eyePaths), element('span','',label), element('span','tab-count',String(counts.get(id) || 0)));
    button.addEventListener('click',() => { selectedCategory=id; limit=perPage; renderCategories(); renderCatalog(); });
    return button;
  }));
}
function supplierButton(group) {
  const button=element('button','eye-button model-eye');
  button.type='button';
  button.append(icon());
  const description=[group.model,group.capacity].filter(Boolean).join(' · ');
  button.setAttribute('aria-label',`Ver fornecedores de ${description}`);
  button.title='Ver todos os fornecedores deste modelo';
  button.addEventListener('click',()=>openSuppliers(group,null));
  return button;
}
function card(group) {
  const article=element('article','product-card');
  const header=element('div','card-head');
  const title=element('h2','product-name',group.model.toUpperCase());
  const capText = group.capacity.includes('·') ? group.capacity : group.capacity.replace(/\s+/g,'');
  const cap=element('span','capacity',capText);
  title.title=title.textContent;
  header.append(title);
  if (group.capacity) header.append(cap);
  header.append(supplierButton(group));
  article.append(header);
  const hasAllSealed = group.offers.length > 0 && group.offers.every(o => /lacrad/i.test(o.condicao || ''));
  const conditionText = group.condition === 'Novo / Lacrado'
    ? (hasAllSealed ? 'Lacrado · declarado pelo fornecedor' : 'Novo / Lacrado · conferir obs. no fornecedor')
    : group.condition;
  const condition=element('div','condition-label'+(group.condition === 'Novo / Lacrado' ? ' sealed' : ''),conditionText);
  article.append(condition);
  const knownVariants = group.variants.filter(v => v.key !== 'cor nao informada');
  const displayVariants = knownVariants.length ? knownVariants : group.variants;
  for (const variant of displayVariants) {
    const displayColor = variant.key === 'cor nao informada' ? 'CONSULTAR COR' : variant.color.toUpperCase();
    const row=element('div','variant-row');
    row.title=`Clique no olho para ver fornecedores de ${group.model} (${displayColor})`;
    row.addEventListener('click',()=>openSuppliers(group,variant.key));
    const color=element('div','variant-color'), dot=element('span','color-dot');
    dot.style.backgroundColor=swatch(variant.key); dot.setAttribute('aria-hidden','true');
    color.append(dot,element('span','color-name',displayColor));
    const actions=element('div','variant-actions'), pricing=element('div','price-stack');
    const effMin = serverPriced ? variant.minimum : (variant.minimum + getDfuDelta(group.offers[0] || { modelo: group.model, condicao: group.condition }, group.category));
    pricing.append(element('span','price-label','CUSTO'),element('strong','price',money(effMin)));
    const eyeBtn=element('button','eye-button variant-eye');
    eyeBtn.type='button';
    eyeBtn.append(icon(eyePaths));
    eyeBtn.title=`Ver todos os fornecedores de ${group.model} · ${variant.color}`;
    eyeBtn.setAttribute('aria-label',`Ver fornecedores de ${group.model} ${variant.color}`);
    eyeBtn.addEventListener('click',ev=>{ev.stopPropagation();openSuppliers(group,variant.key);});
    actions.append(pricing,eyeBtn); row.append(color,actions); article.append(row);
  }
  return article;
}
function renderCatalog(error = false) {
  const catInfo = CATEGORIES.find(([id]) => id === selectedCategory);
  if ($('sectionTitle')) $('sectionTitle').textContent = catInfo ? catInfo[1] : 'Produtos';
  const visible = sortCatalogForDisplay(catalog.filter(group => group.category === selectedCategory));
  const shown = visible.slice(0, limit);
  const totalVariants = visible.reduce((acc, g) => acc + g.variants.length, 0);
  if ($('sectionSummary')) {
    $('sectionSummary').textContent = visible.length
      ? `Mostrando modelos 1-${shown.length} de ${visible.length} modelos · ${totalVariants} variações`
      : 'Nenhum modelo nesta categoria';
  }
  $('loadMore').hidden=visible.length<=limit;
  if (!visible.length) {
    const empty=element('section','empty-state');
    empty.append(icon(),element('h2','',error ? 'Não foi possível carregar as ofertas' : 'Nenhuma oferta nesta categoria'),element('p','',error ? 'Vamos tentar novamente automaticamente. Você também pode atualizar a página.' : 'Os produtos recebidos nas listas dos fornecedores aparecerão aqui.'));
    $('catalogGrid').replaceChildren(empty);
  } else {
    const sections = catalogSections(shown);
    const fragment = sections.map(section => {
      const wrap = element('section', 'model-section');
      const head = element('div', 'model-section-head');
      head.append(
        element('h2', 'model-section-title', section.family),
        element('span', 'model-section-count', `${section.items.length} ${section.items.length === 1 ? 'opção' : 'opções'}`)
      );
      const grid = element('div', 'catalog-grid');
      grid.append(...section.items.map(card));
      wrap.append(head, grid);
      return wrap;
    });
    $('catalogGrid').replaceChildren(...fragment);
  }
}
function renderSupplierList(group,colorKey) {
  const offers=supplierOffers(group.offers,colorKey);
  $('supplierTitle').textContent=[group.model,group.capacity].filter(Boolean).join(' · ');
  const variant=group.variants.find(item=>item.key===colorKey);
  $('supplierSubtitle').textContent=`${variant?.color || 'Todas as cores'} · ${new Set(offers.map(o=>o.condicao)).size === 1 ? (offers[0]?.condicao || 'Condição não informada') : 'Condição por fornecedor'} · ${offers.length} ${offers.length === 1 ? 'oferta' : 'ofertas'} · menor preço primeiro`;
  $('supplierList').replaceChildren(...offers.map((offer,index)=>{
    const row=element('div','supplier-row'); row.dataset.price=Number(offer.preco);
    const info=element('div'), name=element('div','supplier-name',offer.fornecedor || 'Fornecedor não identificado');
    info.append(name,element('div','supplier-details',[offer.modelo,colorLabel(offer.cor),offer.condicao,offer.origem,offer.data,offer.hora].filter(Boolean).join(' · ')));
    if (index===0) info.append(element('span','best-label','MENOR VALOR DESTA SELEÇÃO'));
    const dfuPrice = serverPriced ? Number(offer.preco) : effectivePrice(offer, group.category);
    const pricing=element('div','supplier-cost');
    pricing.append(element('strong','',money(dfuPrice)));
    const phone=String(offer.telefone||'').replace(/\D/g,'');
    if (!demo && phone.length>=10 && phone.length<=15) {
      const link=element('a','','Consultar fornecedor ↗');
      link.target='_blank'; link.rel='noopener noreferrer';
      const product = [group.model, group.capacity].filter(Boolean).join(' ');
      const colorQuestion = offer.cor === 'Cor da caixa a confirmar' ? ' Qual é a cor da caixa do relógio?'
        : offer.cor === 'Cor a confirmar' ? ' Qual é a cor do teclado?' : '';
      const message = `Olá! Gostaria de consultar ${product}, ${colorLabel(offer.cor)}, por ${money(offer.preco)}. Está disponível? ${conditionQuestion(offer.condicao)}${colorQuestion}`;
      link.href = `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
      pricing.append(link);
    } else pricing.append(element('small','',demo ? 'Contato indisponível na prévia' : 'Telefone não informado'));
    row.append(element('span','rank',index+1),info,pricing);return row;
  }));
}
function openSuppliers(group,colorKey) {
  selection={key:group.key,colorKey};
  renderSupplierList(group,colorKey);
  $('supplierDialog').showModal();
}
function updateUserBar(user) {
  if (!user || !user.username) return;
  if ($('userBadge')) {
    const exp = user.expires_at ? ` · até ${user.expires_at}` : '';
    $('userBadge').textContent = `${user.name || user.username}${exp}`;
    $('userBadge').hidden = false;
  }
  const isMaster = user.role === 'master';
  if ($('adminNavBtn')) $('adminNavBtn').hidden = !isMaster;
  if ($('footerAdminLink')) $('footerAdminLink').hidden = !isMaster;
  if ($('btnLogout')) $('btnLogout').hidden = false;
}
if ($('btnLogout')) {
  $('btnLogout').addEventListener('click', async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_) {}
    window.location.href = '/login';
  });
}
async function refresh() {
  if (refreshing) return;
  refreshing=true;
  try {
    const response=await fetch('/api/products',{signal:AbortSignal.timeout(12000),cache:'no-store'});
    if (response.status === 401) { window.location.href = '/login'; return; }
    if (!response.ok) throw new Error('Falha no catálogo');
    const data=await response.json();
    if (!Array.isArray(data.products)) throw new Error('Catálogo inválido');
    demo=data.demo===true;
    serverPriced=data.dfuApplied===true;
    if (data.user) updateUserBar(data.user);
    const payload=JSON.stringify(data.products);
    if (lastPayload!==payload) {
      catalog=groupProducts(data.products);lastPayload=payload;renderCategories();renderCatalog();
      if ($('supplierDialog').open && selection) {
        const group=catalog.find(item=>item.key===selection.key);
        if (group) renderSupplierList(group,selection.colorKey);
        else $('supplierDialog').close();
      }
    }
    $('catalogStatus').textContent=`O Fornecedor · ${catalog.length} ${catalog.length===1?'produto':'produtos'} · Atualizado às ${new Date().toLocaleTimeString('pt-BR')}`;
  } catch(error) {
    $('catalogStatus').textContent='Sem atualização no momento · tentando novamente';
    if (!lastPayload) renderCatalog(true);
  } finally { refreshing=false;$('catalogGrid').setAttribute('aria-busy','false'); }
}
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{
  const rect=dialog.getBoundingClientRect();
  if(event.target===dialog&&(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom))dialog.close();
});
if ($('perPageSelect')) {
  $('perPageSelect').addEventListener('change', event => {
    perPage = Number(event.target.value) || 30;
    limit = perPage;
    renderCatalog();
  });
}
$('loadMore').addEventListener('click',()=>{limit+=perPage;renderCatalog();});
renderCategories();refresh();setInterval(refresh,5000);
