const fs = require('fs');

const comFile = '/opt/disparador-fornecedores/comunidades_entradas.json';
const arqFile = '/opt/disparador-fornecedores/arquivados_monitorados.json';
const fornFile = '/opt/disparador-fornecedores/fornecedores.json';

const fornRaw = fs.readFileSync(fornFile, 'utf8').replace(/^\uFEFF/, '');
const forn = JSON.parse(fornRaw);

const validSupplierKeywords = ['ultra phone', 'wingy cell', 'maher cell', 'bob cell', 'lista fontes', 'produtos sistema', 'grupo '];
for (const f of forn) {
  const n = (f.nome || '').toLowerCase().replace(/\s+\d+$/, '').trim();
  if (n && n.length >= 4) validSupplierKeywords.push(n);
}

function isLegitSupplierGroup(name) {
  const n = (name || '').toLowerCase();
  if (n.includes('vende') || n.includes('jardim') || n.includes('campo limpo') || n.includes('sp') || n.includes('desapeg') || n.includes('feira')) {
    return false;
  }
  return validSupplierKeywords.some(k => n.includes(k));
}

if (fs.existsSync(comFile)) {
  const data = JSON.parse(fs.readFileSync(comFile, 'utf8'));
  const filtered = data.filter(item => isLegitSupplierGroup(item.groupName));
  fs.writeFileSync(comFile, JSON.stringify(filtered, null, 2), 'utf8');
  console.log(`Comunidades estritas: ${data.length} -> ${filtered.length}`);
}

if (fs.existsSync(arqFile)) {
  const data = JSON.parse(fs.readFileSync(arqFile, 'utf8'));
  const filtered = data.filter(item => !item.jid.endsWith('@g.us') || isLegitSupplierGroup(item.name));
  fs.writeFileSync(arqFile, JSON.stringify(filtered, null, 2), 'utf8');
  console.log(`Arquivados estritos: ${data.length} -> ${filtered.length}`);
}
