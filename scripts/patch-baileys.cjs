// Compatibility fix for Baileys 7.0.0-rc14, tracked upstream in issue #2714.
// Fail on an unexpected upstream layout instead of silently patching new versions.
const fs = require('node:fs');
const path = require('node:path');
const marker = '// BuscaBusca: tolerate plain participant JIDs (Baileys #2714).';
const helper = `${marker}
function parseStubParticipant(raw) {
    let value = raw;
    if (typeof raw === 'string') {
        try { value = JSON.parse(raw); } catch { value = raw; }
    }
    if (typeof value === 'string' && /^\\d+(?::\\d+)?@(s\\.whatsapp\\.net|lid)$/.test(value)) {
        return { id: value, ...(value.endsWith('@lid') ? { lid: value } : { phoneNumber: value }) };
    }
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}
`;

function patchSource(source) {
  const broken = 'message.messageStubParameters.map((a) => JSON.parse(a)) || []';
  const fixed = '(message.messageStubParameters || []).map(parseStubParticipant).filter(Boolean)';
  if (source.startsWith(helper) && source.split(fixed).length - 1 === 5 && !source.includes(broken)) return source;
  if (source.includes(marker) || source.split(broken).length - 1 !== 5) {
    throw new Error('Baileys mudou: revisar a correção de participantes antes de publicar.');
  }
  return helper + source.split(broken).join(fixed);
}

function applyPatch(root = path.resolve(__dirname, '..')) {
  const directory = path.join(root, 'node_modules/@whiskeysockets/baileys');
  const version = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8')).version;
  if (version !== '7.0.0-rc14') throw new Error(`Baileys ${version}: correção exige revisão para esta versão.`);
  const file = path.join(directory, 'lib/Utils/process-message.js');
  const original = fs.readFileSync(file, 'utf8');
  const patched = patchSource(original);
  if (patched !== original) {
    const temporary = `${file}.buscabusca.tmp`;
    fs.writeFileSync(temporary, patched);
    fs.renameSync(temporary, file);
  }
  return patched !== original;
}

module.exports = { patchSource, applyPatch };
if (require.main === module) {
  applyPatch();
  console.log('Compatibilidade Baileys: participantes de grupos validada.');
}
