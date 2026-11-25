const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { patchSource, applyPatch } = require('../scripts/patch-baileys.cjs');

test('the compatibility patch is repeatable and rejects an unexpected dependency', () => {
  applyPatch();
  const source = fs.readFileSync('node_modules/@whiskeysockets/baileys/lib/Utils/process-message.js', 'utf8');
  assert.equal(patchSource(source), source);
  assert.throws(() => patchSource('new unsupported source'), /mudou/);
});

test('real Baileys group notification processing accepts plain JIDs, JSON, and invalid entries', async () => {
  applyPatch();
  const { default: processMessage } = await import('../node_modules/@whiskeysockets/baileys/lib/Utils/process-message.js');
  const { WAMessageStubType } = await import('../node_modules/@whiskeysockets/baileys/lib/Types/index.js');
  for (const name of ['GROUP_PARTICIPANT_CHANGE_NUMBER', 'GROUP_PARTICIPANT_REMOVE', 'GROUP_PARTICIPANT_ADD', 'GROUP_PARTICIPANT_DEMOTE', 'GROUP_PARTICIPANT_PROMOTE']) {
    const events = [];
    await processMessage({
      key: { remoteJid: '120000@g.us', id: 'notification', fromMe: false },
      messageTimestamp: 1000, messageStubType: WAMessageStubType[name],
      messageStubParameters: ['5511000000000@s.whatsapp.net', '123456@lid', JSON.stringify({ id: 'other@lid', phoneNumber: '5511999999999@s.whatsapp.net' }), 'invalid', 'null']
    }, { creds: { me: { id: '5511000000000@s.whatsapp.net' } }, ev: { emit: (name, data) => events.push({ name, data }) } });
    const event = events.find(item => item.name === 'group-participants.update');
    assert.ok(event, name);
    assert.equal(event.data.participants.length, 3);
    assert.equal(event.data.participants[0].phoneNumber, '5511000000000@s.whatsapp.net');
    assert.equal(event.data.participants[1].lid, '123456@lid');
  }
});
