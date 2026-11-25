const {test}=require('node:test');
const assert=require('node:assert/strict');
test('missing IDs, non-own messages and mismatched destinations cannot be reported as sent',async()=>{
  const {sentMessageReceipt}=await import('../delivery_tracking.js');
  const jid='55119999@s.whatsapp.net';
  for(const message of [null,{}, {key:{id:'ID',remoteJid:jid,fromMe:false}}, {key:{id:'ID',remoteJid:'other',fromMe:true}}])assert.throws(()=>sentMessageReceipt(message,jid));
});
test('valid outgoing message carries its ID and real receipt state',async()=>{
  const {sentMessageReceipt}=await import('../delivery_tracking.js');
  const jid='55119999@s.whatsapp.net';const key={id:'ID',remoteJid:jid,fromMe:true};
  assert.equal(sentMessageReceipt({key,status:1},jid).ack,1);
  assert.equal(sentMessageReceipt({key,status:3},jid).ack,3);
});
