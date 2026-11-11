const fs = require('fs');
const { validateProduct } = require('./price_guardian.cjs');

const file = '/opt/disparador-fornecedores/produtos_dia.json';
if (!fs.existsSync(file)) process.exit(0);

const prods = JSON.parse(fs.readFileSync(file, 'utf8'));
console.log('Produtos antes da auditoria:', prods.length);

const approved = [];
for (const p of prods) {
  const check = validateProduct(p, approved);
  if (check.valid && check.sanitizedProduct) {
    approved.push(check.sanitizedProduct);
  }
}
console.log('Produtos aprovados pelo Guardião Anti-Erros:', approved.length);
fs.writeFileSync(file, JSON.stringify(approved, null, 2), 'utf8');
