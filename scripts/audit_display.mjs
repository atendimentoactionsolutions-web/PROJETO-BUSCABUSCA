import { readFileSync } from 'node:fs';
import { currentCatalogOffers, groupProducts } from '../web/catalog.js';

const offers = JSON.parse(readFileSync(0, 'utf8'));
const current = currentCatalogOffers(offers);
const groups = groupProducts(current);
const represented = new Set(groups.flatMap(group => group.variants.flatMap(variant => variant.offers)));
const missing = current.filter(offer => !represented.has(offer));
console.log(JSON.stringify({
  current: current.length, superseded: offers.length - current.length, missing: missing.length,
  examples: missing.slice(0, 20).map(({modelo, cor, fornecedor}) => ({modelo, cor, fornecedor}))
}));
