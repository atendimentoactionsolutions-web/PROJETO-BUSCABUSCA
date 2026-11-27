const {test}=require('node:test');
const assert=require('node:assert/strict');
const base={modelo:'IPHONE 16',categoria:'iPhones',capacidade:'128GB',condicao:'Novo / Lacrado',cor:'PRETO',preco:5000,fornecedor:'Alfa'};
test('cards group matching model, capacity and condition; colors use the cheapest offer',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([base,{...base,preco:4700,fornecedor:'Beta'},{...base,cor:'BRANCO',preco:4600},{...base,capacidade:'256GB',preco:5400},{...base,condicao:'Seminovo',preco:3000}]);
  assert.equal(groups.length,3);
  const group=groups.find(g=>g.capacity==='128 GB'&&g.condition==='Novo / Lacrado');
  assert.equal(group.variants.length,2);
  assert.equal(group.minimum,4600);
  assert.deepEqual(group.variants.map(v=>v.minimum),[4600,4700]);
  assert.deepEqual(groups.map(g=>g.minimum),[3000,4600,5400]);
});
test('supplier eye compares only the chosen color, sorted by ascending cost without duplicate suppliers',async()=>{
  const {supplierOffers}=await import('../web/catalog.js');
  const offers=supplierOffers([base,{...base,preco:5100},{...base,fornecedor:'Beta',preco:4600},{...base,fornecedor:'Gama',cor:'BRANCO',preco:4500}],'preto');
  assert.deepEqual(offers.map(o=>o.fornecedor),['Beta','Alfa']);
  assert.deepEqual(offers.map(o=>o.preco),[4600,5000]);
});
test('invalid prices are excluded and capacity spacing does not create duplicate cards',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([base,{...base,capacidade:'128 GB'},null,{...base,preco:NaN},{...base,preco:-1},{...base,preco:Infinity}]);
  assert.equal(groups.length,1);assert.equal(groups[0].offers.length,2);
});
test('Mac and iMac tabs separate models from the existing shared backend category',async()=>{
  const {categoryOf}=await import('../web/catalog.js');
  assert.equal(categoryOf({modelo:'IMAC M3',categoria:'MacBook & iMac'}),'imac');
  assert.equal(categoryOf({modelo:'MACBOOK AIR M3',categoria:'MacBook & iMac'}),'mac');
});
test('model aliases and regions share a card, preserving original supplier specifications',async()=>{
  const {groupProducts,supplierOffers}=await import('../web/catalog.js');
  const models=['IPHONE 18 PRO MAX','IPHONE 18 PROMAX','CELULAR IPHONE 18 PRO MAX ESIM','IPHONE 18 PRO MAX LL/A','IPHONE 18 PRO MAX E-SIM AMERICANO'];
  const offers=models.map((modelo,i)=>({...base,modelo,cor:i%2?'BLACK':'PRETO',preco:5000+i}));
  const groups=groupProducts(offers);
  assert.equal(groups.length,1);
  assert.equal(groups[0].variants.length,1);
  assert.equal(groups[0].offers[3].modelo,models[3]);
  assert.equal(supplierOffers(groups[0].offers,'preto').length,5);
});
test('color spelling aliases combine, but unknown colors are not guessed',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts(['BORDO','BORD','BURGUNDY','VINHO','GLACIAL','GLACIER','GLARCEIR','AZUL','GLACIAL E BORD'].map(cor=>({...base,modelo:'IPHONE 18 PRO MAX',capacidade:'256GB',cor})));
  assert.equal(groups[0].variants.length,2);
  assert.equal(groups[0].variants.find(v=>v.key==='burgundy').offers.length,4);
  assert.equal(groups[0].variants.find(v=>v.key==='glacier').offers.length,4);
});
test('conditions stay separate and embedded storage joins explicit storage',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([base,{...base,modelo:'IPHONE 16 128GB',capacidade:'-'},{...base,modelo:'IPHONE 16 ATIVADO'},{...base,condicao:'Ativado'},{...base,modelo:'IPHONE 16 CPO'}]);
  assert.equal(groups.length,3);
  assert.equal(groups.find(g=>g.condition==='Ativado').offers.length,2);
  assert.equal(groups.find(g=>g.condition==='Novo / Lacrado').offers.length,2);
});
test('display sorts generation, model and descending storage including e models',async()=>{
  const {groupProducts,sortCatalogForDisplay}=await import('../web/catalog.js');
  const groups=sortCatalogForDisplay(groupProducts([
    {...base,modelo:'IPHONE 16 PRO MAX'}, {...base,modelo:'IPHONE 17E'},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'512GB'},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'1TB'},
    {...base,modelo:'IPHONE 18 PRO'},
  ]));
  assert.deepEqual(groups.map(g=>[g.model,g.capacity]),[
    ['iPhone 18 Pro Max','1 TB'],['iPhone 18 Pro Max','512 GB'],['iPhone 18 Pro','128 GB'],['iPhone 17e','128 GB'],['iPhone 16 Pro Max','128 GB']
  ]);
});
test('live supplier storage shorthand and regional qualifiers do not duplicate models',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts(['IPHONE 18 PRO MAX 1T','IPHONE 18 PRO MAX 1 T','IPHONE 18 PRO MAX LL','IPHONE 18 PRO MAX ANATEL','IPHONE 18 PRO MAX INDIANO 1 CHIP F SICO + 1 VIRTUAL'].map((modelo,i)=>({...base,modelo,capacidade:i<2?'-':'1TB'})));
  assert.equal(groups.length,1);
  assert.equal(groups[0].capacity,'1 TB');
  const cpo=groupProducts([{...base,modelo:'IPHONE 16CPO'},{...base,modelo:'IPHONE 16 CPO'}]);
  assert.equal(cpo.length,1);assert.equal(cpo[0].condition,'CPO');
});
test('macbooks format inches, RAM + SSD and normalize colors',async()=>{
  const {groupProducts,colorKey}=await import('../web/catalog.js');
  const groups=groupProducts([
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M5 PRO /24 14',capacidade:'2TB',cor:'SPB MGED4HN/A'},
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M5 13',capacidade:'16/512GB',cor:'00 SKYBLUE'},
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M5PRO T16',capacidade:'512GB',cor:'PRETO ESPACIAL',linhaOriginal:'MACBOOK PRO M5PRO T16 /24 512GB PRETO ESPACIAL'},
  ]);
  assert.equal(groups.find(g=>g.model.includes('14"')).capacity,'24GB RAM · 2TB');
  assert.equal(groups.find(g=>g.model.includes('13"')).capacity,'16GB RAM · 512GB');
  assert.equal(groups.find(g=>g.model.includes('16"')).capacity,'24GB RAM · 512GB');
  assert.equal(colorKey('SPB MGED4HN/A','MACBOOK PRO M5 PRO 14"'),'space black');
  assert.equal(colorKey('00 SKYBLUE','MACBOOK AIR M5 13"'),'sky blue');
  assert.equal(colorKey('MOD A3241/ 10C CPU/ 10C GPU','MACBOOK PRO M5 14"'),'cor nao informada');
});
test('ipad shows inches and storage without RAM, watch shows mm and colors, single-color accessories infer white',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const ipads=groupProducts([
    {...base,categoria:'iPad',modelo:'IPAD PRO M5 T13 WI-FI',capacidade:'512GB',cor:'PRETO ESPACIAL'},
    {...base,categoria:'iPad',modelo:'IPAD AIR M4 11 WIFI',capacidade:'128GB',cor:'00 GREY'},
    {...base,categoria:'iPad',modelo:'IPAD MINI 7 WIFI',capacidade:'128GB',cor:'ROXO'},
  ]);
  assert.equal(ipads.find(g=>g.model.includes('13"')).capacity,'512 GB');
  assert.equal(ipads.find(g=>g.model.includes('13"')).variants[0].color,'Space Black');
  assert.equal(ipads.find(g=>g.model.includes('11"')).variants[0].color,'Space Gray');
  assert.equal(ipads.find(g=>g.model.includes('Mini 7')).model.includes('8.3"'),true);

  const watches=groupProducts([
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 4 49',capacidade:'-',cor:'TITANIO NATURAL'},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH S RIE 11 46MM',capacidade:'-',cor:'PRETO'},
  ]);
  assert.equal(watches.find(g=>g.model.includes('Ultra 4')).capacity,'49MM');
  assert.equal(watches.find(g=>g.model.includes('S11')).capacity,'46MM');
  assert.equal(watches.find(g=>g.model.includes('S11')).variants[0].color,'Preto brilhante');

  const acc=groupProducts([
    {...base,categoria:'AirPods',modelo:'AIRPODS 4',capacidade:'-',cor:'PADRÃO'},
    {...base,categoria:'Acessórios Apple',modelo:'APPLE PENCIL PRO',capacidade:'-',cor:'-'},
  ]);
  assert.equal(acc[0].variants[0].color,'White');
  assert.equal(acc[1].variants[0].color,'White');
});
test('cpo products are isolated in CPO category and combined bordeaux/glacier is rejected',async()=>{
  const {groupProducts,colorKey}=await import('../web/catalog.js');
  assert.equal(colorKey('GLACIAL E BORD','IPHONE 18 PRO MAX'),'cor nao informada');
  const groups=groupProducts([
    {...base,categoria:'iPhones',modelo:'IPHONE 18 PRO MAX',capacidade:'256GB',cor:'BORDO'},
    {...base,categoria:'iPhones',modelo:'IPHONE 16 PRO CPO',capacidade:'256GB',cor:'PRETO'},
    {...base,categoria:'MacBook & iMac',modelo:'MACBOOK PRO M4 14 /16 CPO',capacidade:'512GB',cor:'PRATA'},
    {...base,categoria:'iPad',modelo:'IPAD PRO CPO',capacidade:'256GB',cor:'PRATA'},
  ]);
  assert.equal(groups.filter(g=>g.category==='cpo').length,3);
  assert.equal(groups.filter(g=>g.category==='iphone').length,1);
  assert.equal(groups.filter(g=>g.category==='mac').length,0);
  assert.equal(groups.filter(g=>g.category==='ipad').length,0);
});
test('incomplete products without color, storage, RAM (Mac), or mm (Watch) are excluded',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([
    {...base,categoria:'MacBook & iMac',modelo:'MACBOOK PRO M5 14',capacidade:'1TB',cor:'PRATA'},
    {...base,categoria:'MacBook & iMac',modelo:'MACBOOK PRO M5 /24 14',capacidade:'1TB',cor:'PADRÃO'},
    {...base,categoria:'MacBook & iMac',modelo:'MACBOOK PRO M5 /24 14',capacidade:'1TB',cor:'PRATA'},
    {...base,categoria:'iPhones',modelo:'IPHONE 17',capacidade:'-',cor:'PRETO'},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 4',capacidade:'-',cor:'PRETO'},
  ]);
  assert.equal(groups.length,1);
  assert.equal(groups[0].capacity,'24GB RAM · 1TB');
});
test('airpods aliases unify pro 3, standard 4 vs anc, and max 2',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([
    {...base,categoria:'AirPods',modelo:'AIRPODS 3 PRO',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS PRO 3',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'APPLE AIRPODS PRO 3',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4 LL/A',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4 NORMAL',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4 REGULAR',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4 WIRELESS',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'APPLE AIRPODS 4',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS 4 ANC',capacidade:'-',cor:'-'},
    {...base,categoria:'AirPods',modelo:'AIRPODS MAX 2 SMART CASE USB-C',capacidade:'-',cor:'AZUL'},
    {...base,categoria:'AirPods',modelo:'AIRPODS MAX 2 USB-C',capacidade:'-',cor:'ROXO'},
  ]);
  assert.deepEqual(groups.map(g=>g.model).sort(),[
    'AirPods 4',
    'AirPods 4 Anc',
    'AirPods Max 2 USB-C',
    'AirPods Pro 3',
  ]);
});
test('all categories unify conditionless listings, region codes, decimal inches and accessory aliases without duplicate cards',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const iphone=groupProducts([
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'256GB',condicao:'Novo / Lacrado',preco:8100},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'256GB',condicao:'Condição não informada',preco:7900},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'256GB',condicao:'Novo — lacre não informado',preco:8000},
    {...base,modelo:'IPHONE 18 PRO MAX NANO-SIM FISICO',capacidade:'256GB',condicao:'Novo / Lacrado',preco:8300},
    {...base,modelo:'IPHONE 18 PRO MAX HN',capacidade:'256GB',condicao:'Novo / Lacrado',preco:8200},
  ]);
  assert.equal(iphone.length,1);
  assert.equal(iphone[0].minimum,7900);

  const mac=groupProducts([
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M5 13 16RAM',capacidade:'512GB',cor:'ESTELAR',preco:8200},
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M5 13.6 16GB-RAM',capacidade:'512GB',cor:'MIDNIGHT',condicao:'Condição não informada',preco:8100},
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M5 16/512',capacidade:'512GB',cor:'SILVER',preco:8150,linhaOriginal:'MACBOOK AIR M5 16/512 SILVER 8150'},
  ]);
  assert.equal(mac.length,1);
  assert.equal(mac[0].capacity,'16GB RAM · 512GB');

  const watch=groupProducts([
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH SERIES 12 46MM',capacidade:'-',cor:'BRONZE',condicao:'Condição não informada',preco:2850},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH S12 46MM',capacidade:'-',cor:'DOURADO',condicao:'Novo / Lacrado',preco:2899},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH APPLE WATCH ULTRA 4 49MM',capacidade:'-',cor:'PRETO',preco:5050},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 4 GERACAO 49MM',capacidade:'-',cor:'PRETO',preco:5350},
  ]);
  assert.equal(watch.length,2);

  const cpoIncomplete=groupProducts([
    {...base,modelo:'IPHONE 16 PRO MAX CPO',capacidade:'-',cor:'DESERT TITÂNIO',condicao:'CPO',preco:7099},
    {...base,modelo:'IPHONE 16 PRO MAX CPO',capacidade:'512GB',cor:'DESERT TITÂNIO',condicao:'CPO',preco:6999},
  ]);
  assert.equal(cpoIncomplete.length,1);
  assert.equal(cpoIncomplete[0].capacity,'512 GB');
});
test('unifies iPhone Duo naming, OCR noise, Teclado M Gico, and sorts Duo first before 18 Pro Max',async()=>{
  const {groupProducts,sortCatalogForDisplay,modelFamilyLabel}=await import('../web/catalog.js');
  const mixed=groupProducts([
    {...base,modelo:'O - IPHONE 18 PRO MAX',capacidade:'512GB',cor:'BURGUNDY',condicao:'Novo / Lacrado',preco:10300},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'512GB',cor:'BURGUNDY',condicao:'Novo / Lacrado',preco:10150},
    {...base,modelo:'IPHONE 18 PRO MAX LLA',capacidade:'512GB',cor:'BLACK',condicao:'Novo / Lacrado',preco:10250},
    {...base,modelo:'IPHONE 18 DUO',capacidade:'512GB',cor:'PRETO',condicao:'Novo / Lacrado',preco:16300},
    {...base,modelo:'IPHONE 18 DUO LLA',capacidade:'512GB',cor:'NIGHT SKY',condicao:'Novo / Lacrado',preco:17000},
    {...base,modelo:'IPHONE FOLD',capacidade:'512GB',cor:'BRANCO',condicao:'Novo / Lacrado',preco:15600},
    {...base,modelo:'IPHONE 17 - IPHONE 17 PRO MAX',capacidade:'512GB',cor:'COSMIC ORANGE',condicao:'Novo / Lacrado',preco:8450},
    {...base,modelo:'IPHONE 17 PRO MAX F SICO',capacidade:'512GB',cor:'DEEP BLUE',condicao:'Novo / Lacrado',preco:9599},
    {...base,modelo:'IPHONE 17 PRO MAX',capacidade:'512GB',cor:'SILVER',condicao:'Novo / Lacrado',preco:8680},
    {...base,modelo:'IPHONE 17 PRO MAX - YO PELLE AN 2',capacidade:'512GB',cor:'BLACK',condicao:'Novo / Lacrado',preco:8100},
  ]);
  const sorted=sortCatalogForDisplay(mixed);
  assert.deepEqual(sorted.map(g=>`${g.model} ${g.capacity}`),[
    'iPhone Duo 512 GB',
    'iPhone 18 Pro Max 512 GB',
    'iPhone 17 Pro Max 512 GB',
  ]);
  assert.equal(sorted[0].minimum,15600);
  assert.equal(sorted[1].minimum,10150);
  assert.equal(sorted[2].minimum,8450);
  assert.deepEqual(sorted.map(modelFamilyLabel),['iPhone Duo','iPhone 18 Pro Max','iPhone 17 Pro Max']);

  const acc=groupProducts([
    {...base,categoria:'Acessórios',modelo:'TECLADO M GICO APPLE COM TECLADO NUM RICO',capacidade:'-',cor:'BRANCO',preco:1200},
    {...base,categoria:'Acessórios',modelo:'MAGIC KEYBOARD NUMERICO',capacidade:'-',cor:'PRETO',preco:1150},
  ]);
  assert.equal(acc.length,1);
  assert.equal(acc[0].minimum,1150);
});

test('all categories sort strictly from newest generation to oldest including Watch, Mac, iPad, AirPods and CPO',async()=>{
  const {groupProducts,sortCatalogForDisplay,modelFamilyLabel}=await import('../web/catalog.js');
  const watches=sortCatalogForDisplay(groupProducts([
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 2 49MM',capacidade:'-',cor:'PRETO',preco:4199},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH S10 46MM',capacidade:'-',cor:'PRETO',preco:2150},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH S12 46MM',capacidade:'-',cor:'PRETO',preco:2850},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 3 49MM',capacidade:'-',cor:'PRETO',preco:4400},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 4 49MM',capacidade:'-',cor:'PRETO',preco:5050},
  ]));
  assert.deepEqual(watches.map(modelFamilyLabel),[
    'Apple Watch Ultra 4',
    'Apple Watch S12',
    'Apple Watch Ultra 3',
    'Apple Watch S10',
    'Apple Watch Ultra 2',
  ]);

  const macs=sortCatalogForDisplay(groupProducts([
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M2 13 16/256GB',capacidade:'-',cor:'MIDNIGHT',preco:6900},
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M4 PRO 16 48/1TB',capacidade:'-',cor:'PRETO',preco:29999},
    {...base,categoria:'Mac',modelo:'MACBOOK AIR M5 13 16/512GB',capacidade:'-',cor:'MIDNIGHT',preco:8050},
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M5 PRO 14 24/1TB',capacidade:'-',cor:'PRETO',preco:16400},
    {...base,categoria:'Mac',modelo:'MAC MINI M6 16/256GB',capacidade:'-',cor:'PRATA',preco:6650},
  ]));
  assert.deepEqual(macs.map(modelFamilyLabel),[
    'Mac Mini M6',
    'MacBook Pro M5 Pro',
    'MacBook Air M5',
    'MacBook Pro M4 Pro',
    'MacBook Air M2',
  ]);

  const ipads=sortCatalogForDisplay(groupProducts([
    {...base,categoria:'iPad',modelo:'IPAD MINI 6 256GB',capacidade:'-',cor:'CINZA',preco:3450},
    {...base,categoria:'iPad',modelo:'IPAD PRO M4 11 WI-FI',capacidade:'256GB',cor:'PRETO',preco:7400},
    {...base,categoria:'iPad',modelo:'IPAD AIR M4 11 WI-FI',capacidade:'128GB',cor:'AZUL',preco:4900},
    {...base,categoria:'iPad',modelo:'IPAD PRO M5 13 WI-FI',capacidade:'512GB',cor:'PRETO',preco:9550},
  ]));
  assert.deepEqual(ipads.map(modelFamilyLabel),[
    'iPad Pro M5 Wi-Fi',
    'iPad Air M4 Wi-Fi',
    'iPad Pro M4 Wi-Fi',
    'iPad Mini 6 Wi-Fi',
  ]);

  const cpo=sortCatalogForDisplay(groupProducts([
    {...base,modelo:'IPHONE 13 CPO',capacidade:'128GB',condicao:'CPO',preco:2850},
    {...base,modelo:'IPHONE 15 PRO CPO',capacidade:'256GB',condicao:'CPO',preco:4900},
    {...base,modelo:'IPHONE 16 PRO MAX CPO',capacidade:'512GB',condicao:'CPO',preco:6999},
  ]));
  assert.deepEqual(cpo.map(g=>g.model),['iPhone 16 Pro Max','iPhone 15 Pro','iPhone 13']);
});

test('excludes MacBooks and iPad Pro/Air missing required screen inches or color',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const groups=groupProducts([
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M5',capacidade:'16/1TB',cor:'PRETO',preco:13600},
    {...base,categoria:'Mac',modelo:'MACBOOK PRO M5 14"',capacidade:'16/1TB',cor:'PRETO',preco:13600},
    {...base,categoria:'iPad',modelo:'IPAD PRO M4',capacidade:'512GB',cor:'PRETO',preco:7500},
    {...base,categoria:'iPad',modelo:'IPAD PRO M4 11"',capacidade:'512GB',cor:'PRETO',preco:7600},
  ]);
  assert.deepEqual(groups.map(g=>g.model).sort(),['MacBook Pro M5 14"','iPad Pro M4 11" Wi-Fi'].sort());
});

test('enforces official colors per model, unifies midnight/preto and teal/verde, and separates AirPods 5',async()=>{
  const {groupProducts,sortCatalogForDisplay}=await import('../web/catalog.js');
  const cards=groupProducts([
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'512GB',cor:'LARANJA',preco:10300},
    {...base,modelo:'IPHONE 18 PRO MAX',capacidade:'512GB',cor:'BURGUNDY',preco:10150},
    {...base,modelo:'IPHONE 18 PRO',capacidade:'256GB',cor:'ROXO',preco:8000},
    {...base,modelo:'IPHONE 18 PRO',capacidade:'256GB',cor:'AZUL',preco:7950},
    {...base,modelo:'IPHONE 16',capacidade:'128GB',cor:'LAVANDA',preco:5500},
    {...base,modelo:'IPHONE 16',capacidade:'128GB',cor:'PRATA',preco:4600},
    {...base,modelo:'IPHONE 16',capacidade:'128GB',cor:'VERDE',preco:4500},
    {...base,modelo:'IPHONE 16',capacidade:'128GB',cor:'VERDE ACINZENTADO',preco:4499},
    {...base,modelo:'IPHONE 14',capacidade:'128GB',cor:'MEIA-NOITE',preco:3400},
    {...base,modelo:'IPHONE 14',capacidade:'128GB',cor:'PRETO',preco:3350},
    {...base,modelo:'IPHONE 14',capacidade:'128GB',cor:'ESTELAR',preco:3420},
    {...base,modelo:'IPHONE 14',capacidade:'128GB',cor:'BRANCO',preco:3390},
    {...base,categoria:'iPad',modelo:'IPAD 11 WI-FI',capacidade:'128GB',cor:'PRETO',preco:3000},
    {...base,categoria:'Apple Watch',modelo:'APPLE WATCH ULTRA 2 49MM',capacidade:'-',cor:'LARANJA',preco:4350},
  ]);
  assert.deepEqual(cards.find(c=>c.model==='iPhone 18 Pro Max').variants.map(v=>v.key),['burgundy']);
  assert.deepEqual(cards.find(c=>c.model==='iPhone 18 Pro').variants.map(v=>v.key),['glacier']);
  assert.deepEqual(cards.find(c=>c.model==='iPhone 16').variants.map(v=>v.key),['verde acinzentado']);
  assert.deepEqual(cards.find(c=>c.model==='iPhone 14').variants.map(v=>v.key).sort(),['midnight','starlight']);
  assert.equal(cards.find(c=>c.model.includes('iPad 11')),undefined);
  assert.equal(cards.find(c=>c.model.includes('Ultra 2')),undefined);

  const airpods=sortCatalogForDisplay(groupProducts([
    {...base,categoria:'AirPods',modelo:'AIRPODS 4',capacidade:'-',cor:'-',preco:800},
    {...base,categoria:'AirPods',modelo:'AIRPODS 5',capacidade:'-',cor:'-',preco:1050},
    {...base,categoria:'AirPods',modelo:'AIRPODS 5 WIRELESS CHARGING CASE',capacidade:'-',cor:'-',preco:1200},
    {...base,categoria:'AirPods',modelo:'AIRPODS PRO 3',capacidade:'-',cor:'-',preco:1550},
  ]));
  assert.deepEqual(airpods.map(a=>a.model),['AirPods Pro 3','AirPods 5 Anc','AirPods 5','AirPods 4']);
});

test('accessories reject storage and preserve iPad keyboard compatibility',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const accessory={...base,categoria:'Acessórios',capacidade:'-',cor:'BRANCO'};
  const groups=groupProducts([
    {...accessory,modelo:'AIRTAG 4 PACK',capacidade:'128GB'},
    {...accessory,modelo:'MAGIC KEYBOARD IPAD AIR 11" M2/M3/M4'},
    {...accessory,modelo:'MAGIC KEYBOARD IPAD PRO 11" M4/M5'},
  ]);
  assert.equal(groups.length,2);
  assert.ok(groups.every(group=>group.model.toLowerCase().includes('keyboard')));
});

test('repeated iPad storage units share their Wi-Fi heading while Wi-Fi + Cellular gets its own heading',async()=>{
  const {groupProducts,catalogSections}=await import('../web/catalog.js');
  const ipad={...base,categoria:'iPad',modelo:'IPAD 11 A16 WI-FI',cor:'ROSA'};
  const groups=groupProducts([ipad,{...ipad,modelo:'IPAD 11 A16 GB WI-FI',fornecedor:'Beta'},
    {...ipad,capacidade:'256GB'}, {...ipad,modelo:'IPAD 11TH WIFI + CELLULAR'}]);
  assert.equal(groups.length,3);
  assert.equal(groups.find(g=>g.capacity==='128 GB'&&!g.model.includes('Cellular')).offers.length,2);
  const sections=catalogSections([groups[0],{...groups[0],model:'iPad Mini 7 Wi-Fi'},...groups.slice(1)]);
  assert.equal(sections.length,3);
  assert.equal(sections.find(s=>s.family==='iPad 11 A16 Wi-Fi').items.length,2);
  assert.equal(sections.find(s=>s.family==='iPad 11 A16 Wi-Fi + Cellular').items.length,1);
});

test('Series 11 GPS separates rose gold from titanium gold',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const watch={...base,categoria:'Apple Watch',modelo:'APPLE WATCH S11 46MM GPS',capacidade:'46MM'};
  const groups=groupProducts([{...watch,cor:'DOURADO'}, {...watch,cor:'ROSA GOLD'},
    {...watch,modelo:watch.modelo+' + CELLULAR',cor:'DOURADO'}]);
  assert.deepEqual(groups.find(x=>!x.model.includes('Cellular')).variants.map(v=>v.color),['Ouro rosa']);
  assert.equal(groups.length,2);
});

test('titânio em português aparece no cartão e a oferta de meio-dia prevalece', async () => {
  const {groupProducts,supplierOffers} = await import('../web/catalog.js');
  const morning = {...base, modelo:'IPHONE 15 PRO MAX',cor:'PRETO TITÂNIO',data:'23/11/2026',hora:'09:00:00',preco:5000};
  const noon = {...morning,cor:'Titânio preto',hora:'12:00:00',preco:5400};
  const white = {...morning,cor:'BRANCO TITÂNIO'};
  const [group] = groupProducts([noon, white, morning]);
  assert.deepEqual(group.variants.map(v=>[v.key,v.minimum]), [['white titanium',5000],['black titanium',5400]]);
  assert.deepEqual(supplierOffers(group.offers,'black titanium'), [noon]);
});

test('ofertas com cor a confirmar continuam acessíveis pelo cartão e pelo fornecedor', async () => {
  const {groupProducts,supplierOffers} = await import('../web/catalog.js');
  const rows = [
    {...base, modelo:'APPLE WATCH SE3 44MM GPS', categoria:'Apple Watch', capacidade:'-', cor:'Cor da caixa a confirmar', preco:1890},
    {...base, modelo:'SMART KEYBOARD IPAD', categoria:'Acessórios Apple', capacidade:'-', condicao:'Seminovo', cor:'Cor a confirmar', preco:750}
  ];
  for (const row of rows) {
    const [group] = groupProducts([row]);
    assert.deepEqual(supplierOffers(group.offers,group.variants[0].key),[row]);
  }
});

test('midnight e demais cores mostram swatch fiel, AirPods falsos são bloqueados e iPhones descontinuados não vão para Novos', async () => {
  const {swatch, categoryOf, groupProducts} = await import('../web/catalog.js');
  assert.equal(swatch('Midnight'), '#111827');
  assert.equal(swatch('Amarelo'), '#facc15');
  assert.equal(swatch('Rose Gold'), '#e5a9a9');
  assert.equal(swatch('Bronze'), '#8c6239');
  assert.equal(categoryOf({modelo: 'IPHONE 12 PRO MAX', categoria: 'iPhones', condicao: 'Novo / Lacrado'}), 'seminovos');
  assert.equal(categoryOf({modelo: 'IPHONE 13 PRO', categoria: 'iPhones', condicao: 'Novo / Lacrado'}), 'seminovos');
  assert.equal(categoryOf({modelo: 'IPHONE 14 PLUS', categoria: 'iPhones', condicao: 'Novo / Lacrado'}), 'seminovos');
  assert.equal(categoryOf({modelo: 'IPHONE 14 PRO MAX', categoria: 'iPhones', condicao: 'Novo / Lacrado'}), 'seminovos');
  assert.equal(categoryOf({modelo: 'IPHONE 13 PRO', categoria: 'iPhones', condicao: 'CPO'}), 'cpo');
  assert.equal(categoryOf({modelo: 'IPHONE 15', categoria: 'iPhones', condicao: 'Novo / Lacrado'}), 'iphone');
  assert.equal(categoryOf({modelo: 'EARPODS USB-C', categoria: 'AirPods', condicao: 'Novo / Lacrado'}), 'accessories');
  assert.equal(groupProducts([{...base, categoria: 'AirPods', modelo: 'AIRPODS PRO 2', capacidade: '-', cor: 'BRANCO', preco: 58}]).length, 0);
});

test('supplier and reference iPad names group by generation and screen size',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  for (const models of [
    ['IPAD MINI 7 WIFI','IPAD MINI 7 A17 PRO','IPAD MINI A17 PRO'],
    ['IPAD MINI 6','IPAD MINI 6TH GENERATION'],
    ['IPAD PRO 12 9 INCH 6TH GENERATION','IPAD PRO 12.9" 6 GERACAO'],
  ]) {
    const offers=models.map((modelo,i)=>({...base,categoria:'iPad',modelo,cor:modelo.includes('MINI')?'ROXO':'PRATA',fornecedor:String(i)}));
    const groups=groupProducts(offers);
    assert.equal(groups.length,1);
    assert.equal(groups[0].offers.length,offers.length);
  }
});

test('hyphenated iPad 11 Wi-Fi and A1611 aliases group together without merging storage or Cellular',async()=>{
  const {groupProducts}=await import('../web/catalog.js');
  const ipad={...base,categoria:'iPad',condicao:'Novo / Lacrado',cor:'ROSA'};
  const groups=groupProducts([
    {...ipad,modelo:'IPAD-11-WI-FI',capacidade:'128GB',fornecedor:'Hyphen'},
    {...ipad,modelo:'IPAD 11 A1611 WI-FI',capacidade:'128 GB',cor:'AZUL',fornecedor:'A1611'},
    {...ipad,modelo:'IPAD 11 A1611 WI-FI',capacidade:'256GB',fornecedor:'256GB'},
    {...ipad,modelo:'IPAD 11 A1611 WI-FI + CELLULAR',capacidade:'128GB',fornecedor:'Cellular'},
  ]);
  assert.equal(groups.length,3);
  const wifi128=groups.find(g=>g.capacity==='128 GB'&&!g.model.includes('Cellular'));
  assert.equal(wifi128.offers.length,2);
  assert.deepEqual(wifi128.variants.map(v=>v.key).sort(),['blue','pink']);
  assert.ok(groups.some(g=>g.capacity==='256 GB'&&!g.model.includes('Cellular')));
  assert.ok(groups.some(g=>g.capacity==='128 GB'&&g.model.includes('Cellular')));
});

test('separates iPad Wi-Fi and Wi-Fi + Cellular into distinct ordered sections for all models', async () => {
  const { groupProducts, sortCatalogForDisplay, catalogSections } = await import('../web/catalog.js');
  const rows = [
    { ...base, categoria: 'iPad', modelo: 'IPAD PRO 13 INCH M5 WIFI+CELLULAR 2025 5G', capacidade: '512GB', cor: 'PRETO', preco: 11200 },
    { ...base, categoria: 'iPad', modelo: 'IPAD PRO 11 INCH M5 WIFI', capacidade: '256GB', cor: 'PRATA', preco: 7900 },
    { ...base, categoria: 'iPad', modelo: 'IPAD PRO M5 13 WI-FI', capacidade: '512GB', cor: 'PRETO', preco: 9600 },
    { ...base, categoria: 'iPad', modelo: 'IPAD PRO 11 INCH M5 WIFI + CELULAR 2025 5G', capacidade: '256GB', cor: 'PRATA', preco: 9300 },
    { ...base, categoria: 'iPad', modelo: 'IPAD AIR 13 INCH M4 WIFI + CELLULAR 2025 5G', capacidade: '128GB', cor: 'AZUL', preco: 8200 },
    { ...base, categoria: 'iPad', modelo: 'IPAD AIR 11 INCH M4 WIFI', capacidade: '128GB', cor: 'ESTELAR', preco: 5200 },
    { ...base, categoria: 'iPad', modelo: 'IPAD AIR 13 INCH M4 WIFI', capacidade: '128GB', cor: 'AZUL', preco: 7100 },
    { ...base, categoria: 'iPad', modelo: 'IPAD AIR 11 INCH M4 WIFI + CELLULAR 2025 5G', capacidade: '128GB', cor: 'AZUL', preco: 6500 },
    { ...base, categoria: 'iPad', modelo: 'IPAD 11TH WIFI', capacidade: '128GB', cor: 'AZUL', preco: 3000 },
    { ...base, categoria: 'iPad', modelo: 'IPAD 11TH WIFI + CELLULAR', capacidade: '128GB', cor: 'ROSA', preco: 4199 },
    { ...base, categoria: 'iPad', modelo: 'IPAD MINI 7 WIFI', capacidade: '128GB', cor: 'ROXO', preco: 3800 },
    { ...base, categoria: 'iPad', modelo: 'IPAD MINI 7 5G', capacidade: '128GB', cor: 'ROXO', preco: 4700 },
  ];
  const sections = catalogSections(sortCatalogForDisplay(groupProducts(rows)));
  assert.deepEqual(sections.map(s => s.family), [
    'iPad Pro M5 Wi-Fi',
    'iPad Pro M5 Wi-Fi + Cellular',
    'iPad Air M4 Wi-Fi',
    'iPad Air M4 Wi-Fi + Cellular',
    'iPad 11 A16 Wi-Fi',
    'iPad 11 A16 Wi-Fi + Cellular',
    'iPad Mini 7 Wi-Fi',
    'iPad Mini 7 Wi-Fi + Cellular',
  ]);
  assert.deepEqual(sections[0].items.map(i => i.model), ['iPad Pro M5 13" Wi-Fi', 'iPad Pro M5 11" Wi-Fi']);
  assert.deepEqual(sections[1].items.map(i => i.model), ['iPad Pro M5 13" Wi-Fi + Cellular', 'iPad Pro M5 11" Wi-Fi + Cellular']);
  assert.deepEqual(sections[2].items.map(i => i.model), ['iPad Air M4 13" Wi-Fi', 'iPad Air M4 11" Wi-Fi']);
  assert.deepEqual(sections[3].items.map(i => i.model), ['iPad Air M4 13" Wi-Fi + Cellular', 'iPad Air M4 11" Wi-Fi + Cellular']);
});

test('Apple Watch Ultra 4 never displays Burgundy band as case color and heals Black Titanium from raw line', async () => {
  const { groupProducts } = await import('../web/catalog.js');
  const groups = groupProducts([
    { ...base, categoria: 'Apple Watch', modelo: 'APPLE WATCH ULTRA 4 49MM GPS + CELLULAR', capacidade: '49MM', cor: 'PRETO', preco: 5400 },
    { ...base, categoria: 'Apple Watch', modelo: 'APPLE WATCH ULTRA 4 49MM GPS + CELLULAR', capacidade: '49MM', cor: 'NATURAL TITÂNIO', preco: 5650 },
    { ...base, categoria: 'Apple Watch', modelo: 'APPLE WATCH ULTRA 4 49MM GPS + CELLULAR', capacidade: '49MM', cor: 'BURGUNDY', preco: 5350, fornecedor: 'ZOI', linhaOriginal: 'BLACK TI BURGUNDY ALP LOOP $5.350,00' },
    { ...base, categoria: 'Apple Watch', modelo: 'APPLE WATCH ULTRA 4 49MM GPS + CELLULAR', capacidade: '49MM', cor: 'BURGUNDY', preco: 5450, fornecedor: 'SEM_CAIXA', linhaOriginal: 'BURGUNDY ALP LOOP $5.450,00' },
  ]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].variants.map(v => v.key).sort(), ['black titanium', 'natural titanium']);
  assert.equal(groups[0].variants.find(v => v.key === 'black titanium').minimum, 5350);
});

test('iPhones Seminovos allow strictly iPhone 13 through iPhone 17 Pro Max', async () => {
  const { groupProducts } = await import('../web/catalog.js');
  const groups = groupProducts([
    { ...base, modelo: 'IPHONE XR', capacidade: '64GB', cor: 'PRETO', condicao: 'Seminovo', preco: 1100 },
    { ...base, modelo: 'IPHONE 11', capacidade: '128GB', cor: 'PRETO', condicao: 'Seminovo', preco: 1450 },
    { ...base, modelo: 'IPHONE 12 PRO MAX', capacidade: '256GB', cor: 'GRAFITE', condicao: 'Seminovo', preco: 2400 },
    { ...base, modelo: 'IPHONE 13', capacidade: '128GB', cor: 'MEIA-NOITE', condicao: 'Seminovo', preco: 2100 },
    { ...base, modelo: 'IPHONE 17 PRO MAX', capacidade: '256GB', cor: 'LARANJA', condicao: 'Seminovo', preco: 6900 },
    { ...base, modelo: 'IPHONE 18 PRO MAX', capacidade: '256GB', cor: 'PRETO', condicao: 'Seminovo', preco: 7900 },
    { ...base, modelo: 'IPHONE DUO', capacidade: '256GB', cor: 'PRETO', condicao: 'Seminovo', preco: 12000 },
    { ...base, modelo: 'IPHONE 18 PRO MAX', capacidade: '256GB', cor: 'PRETO', condicao: 'Novo / Lacrado', preco: 8500 },
  ]);
  const semiModels = groups.filter(g => g.category === 'seminovos').map(g => g.model).sort();
  const newModels = groups.filter(g => g.category === 'iphone').map(g => g.model);
  assert.deepEqual(semiModels, ['iPhone 13', 'iPhone 17 Pro Max']);
  assert.deepEqual(newModels, ['iPhone 18 Pro Max']);
});
