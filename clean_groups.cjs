const fs = require('fs');
const path = require('path');

const fornFile = '/opt/disparador-fornecedores/fornecedores.json';
const comFile = '/opt/disparador-fornecedores/comunidades_entradas.json';
const arqFile = '/opt/disparador-fornecedores/arquivados_monitorados.json';

const fornRaw = fs.readFileSync(fornFile, 'utf8').replace(/^\uFEFF/, '');
const forn = JSON.parse(fornRaw);
const supplierDigits = new Set();
for (const s of forn) {
  const d = String(s.telefone || '').replace(/\D/g, '');
  if (d) {
    supplierDigits.add(d);
    if (d.startsWith('55') && d.length === 13) supplierDigits.add(d.slice(0, 4) + d.slice(5));
    if (d.startsWith('55') && d.length === 12) supplierDigits.add(d.slice(0, 4) + '9' + d.slice(4));
  }
}

const blacklist = [
  'universo feminino', 'miga', 'desapeg', 'feira do rolo', 'compra e venda',
  'compras e vendas', 'doacao', 'brecho', 'ifindz', 'divulga', 'brechó', 'doação',
  'bazar', 'renda extra', 'afiliado', 'cassino', 'tigre', 'aposta', 'bet',
  'grajaú', 'osasco', 'capão', 'pirituba', 'notícias', 'rataiada', 'postou', 'pedreira',
  'carapicuíba', 'biritiba', 'mogi', 'paraisópolis', 'm\' boi'
];

function isClean(name, senderJid) {
  const n = (name || '').toLowerCase();
  const j = (senderJid || '').toLowerCase();

  if (j.includes('status@broadcast')) return false;
  if (blacklist.some(b => n.includes(b) || j.includes(b))) return false;
  return true;
}

if (fs.existsSync(comFile)) {
  const coms = JSON.parse(fs.readFileSync(comFile, 'utf8').replace(/^\uFEFF/, ''));
  console.log(`Comunidades antes: ${coms.length}`);
  const cleanComs = coms.filter(c => isClean(c.groupName, c.groupJid || c.senderJid));
  console.log(`Comunidades depois: ${cleanComs.length}`);
  fs.writeFileSync(comFile, JSON.stringify(cleanComs, null, 2));
}

if (fs.existsSync(arqFile)) {
  const arq = JSON.parse(fs.readFileSync(arqFile, 'utf8').replace(/^\uFEFF/, ''));
  console.log(`Arquivados antes: ${arq.length}`);
  const cleanArq = arq.filter(a => isClean(a.name, a.jid));
  console.log(`Arquivados depois: ${cleanArq.length}`);
  fs.writeFileSync(arqFile, JSON.stringify(cleanArq, null, 2));
}

console.log('Limpeza profunda concluída!');
