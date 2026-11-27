const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('whatsapp_bridge.js','utf8');
let projectInviteDecision;
test.before(async()=>{({projectInviteDecision}=await import('../invite_policy.js'));});
const archiveSource=source.slice(source.indexOf('async function archiveChatJid'),source.indexOf('// Solicita ao WhatsApp'));
function setup(message){
  const calls=[];
  const context={waSocket:{chatModify:async (...args)=>calls.push(args)},chatLastMsgMap:new Map(message?[['chat@g.us',message]]:[]),realMessageAnchorsMap:new Map(),archiveInFlight:new Map(),archivedMessageIds:new Map(),groupSubjectCache:new Map(),isSafeGroupName:()=>true,isInviteBlocked:()=>false,registerMonitoredArchivedChat(){},saveMonitoredArchived(){},addLog(){}};
  vm.createContext(context);vm.runInContext(archiveSource,context);
  return {context,calls};
}
test('archive never sends invented state when no message is available',async()=>{
  const {context,calls}=setup();
  assert.equal(await context.archiveChatJid('chat@g.us','','','',true),false);
  assert.equal(calls.length,0);
});
test('archive uses the real message id and participant',async()=>{
  const {context,calls}=setup({key:{id:'REAL_ID',fromMe:false,participant:'sender@s.whatsapp.net'},messageTimestamp:12345});
  assert.equal(await context.archiveChatJid('chat@g.us'),true);
  assert.equal(calls[0][0].lastMessages[0].key.id,'REAL_ID');
  assert.equal(calls[0][0].lastMessages[0].key.participant,'sender@s.whatsapp.net');
});
test('archive skips a previously fabricated anchor',async()=>{
  const {context,calls}=setup({key:{id:'ARCHIVE_123'},messageTimestamp:12345});
  await context.archiveChatJid('chat@g.us','','','',true);
  assert.equal(calls.length,0);
});
test('duplicate archive events share a request and do not write the same state again',async()=>{
  const {context,calls}=setup({key:{id:'REAL',fromMe:false},messageTimestamp:12345});
  await Promise.all([context.archiveChatJid('chat@g.us'),context.archiveChatJid('chat@g.us')]);
  await context.archiveChatJid('chat@g.us');
  assert.equal(calls.length,1);
});
test('blocked groups never trigger archive writes',async()=>{
  const {context,calls}=setup({key:{id:'REAL'},messageTimestamp:12345});
  context.isInviteBlocked=()=>true;
  assert.equal(await context.archiveChatJid('chat@g.us'),false);
  assert.equal(calls.length,0);
});
const reconnectSource=source.slice(source.indexOf('function scheduleReconnect'),source.indexOf('let connectionStatus'));
test('duplicate reconnect events schedule one connection attempt',()=>{
  let callback;let scheduled=0;let starts=0;
  const context={stopping:false,reconnectTimer:null,setTimeout(fn){callback=fn;scheduled++;return 123},startWhatsApp(){starts++}};
  vm.createContext(context);vm.runInContext(reconnectSource,context);
  context.scheduleReconnect(100);context.scheduleReconnect(100);
  assert.equal(scheduled,1);callback();assert.equal(starts,1);assert.equal(context.reconnectTimer,null);
});
test('outbound supplier messaging routes and acknowledgments are absent',()=>{
  assert.equal(source.includes("app.post('/api/send'"),false);
  assert.equal(source.includes('waSocket.sendMessage('),false);
  assert.equal(source.includes('/api/internal/message-ack'),false);
  assert.match(source,/app\.post\('\/api\/join-community'/);
});
const inviteSource=source.slice(source.indexOf('function parseManualInvite'),source.indexOf('async function processInviteCode'));
test('manual invites accept valid codes and links, rejecting unrelated URLs',()=>{
  const context={URL};vm.createContext(context);vm.runInContext(inviteSource,context);
  const code='AbCdEfGhIjKlMnOpQrStUv';
  assert.equal(context.parseManualInvite(code),code);
  assert.equal(context.parseManualInvite('https://chat.whatsapp.com/'+code+'?mode=invite'),code);
  assert.equal(context.parseManualInvite('chat.whatsapp.com/invite/'+code),code);
  assert.equal(context.parseManualInvite('https://example.com/'+code),null);
  assert.equal(context.parseManualInvite('not an invite'),null);
});
test('invite errors distinguish expired links, restrictions and limits',()=>{
  const context={URL};vm.createContext(context);vm.runInContext(inviteSource,context);
  assert.match(context.inviteErrorMessage({output:{statusCode:404}}),/expirou/);
  assert.match(context.inviteErrorMessage({output:{statusCode:403}}),/administrador/);
  assert.match(context.inviteErrorMessage({output:{statusCode:429}}),/limitou/);
});
const extractionSource=source.slice(source.indexOf('function extractInviteCodesFromMessage'),source.indexOf('function findNativeGroupInvite'));
test('invites are extracted from captions and native messages, excluding quoted messages and lookalike hosts',()=>{
  const context={Buffer};vm.createContext(context);vm.runInContext(extractionSource,context);
  const code='AbCdEfGhIjKlMnOpQrStUv';
  assert.deepEqual([...context.extractInviteCodesFromMessage({imageMessage:{caption:'Entre: https://chat.whatsapp.com/'+code}})],[code]);
  assert.deepEqual([...context.extractInviteCodesFromMessage({groupInviteMessage:{inviteCode:code}})],[code]);
  assert.equal(context.extractInviteCodesFromMessage({extendedTextMessage:{text:'ok',contextInfo:{quotedMessage:{conversation:'https://chat.whatsapp.com/'+code}}}}).length,0);
  assert.equal(context.extractInviteCodesFromMessage({conversation:'https://evilchat.whatsapp.com/'+code}).length,0);
});
const supplierInviteSource=source.slice(source.indexOf('function enqueueSupplierInvites'),source.indexOf('async function inspectIncomingMessage'));
test('automatic entry is triggered only for known suppliers, never own messages',()=>{
  const queued=[];const context={loadKnownSuppliers(){},resolveLidJid:jid=>jid,isKnownSupplierJid:jid=>jid==='supplier@s.whatsapp.net',extractInviteCodesFromMessage:()=>['ABC'],joinedCommunitiesMap:new Map(),inviteQueue:{enqueue:item=>{queued.push(item);return true}},contactNameCache:new Map(),addLog(){}};
  vm.createContext(context);vm.runInContext(supplierInviteSource,context);
  context.enqueueSupplierInvites({key:{remoteJid:'unknown@s.whatsapp.net'},message:{}});
  context.enqueueSupplierInvites({key:{remoteJid:'supplier@s.whatsapp.net',fromMe:true},message:{}});
  assert.equal(queued.length,0);
  context.enqueueSupplierInvites({key:{remoteJid:'supplier@s.whatsapp.net'},message:{}});
  context.enqueueSupplierInvites({key:{remoteJid:'group@g.us',participantAlt:'supplier@s.whatsapp.net'},message:{}});
  assert.equal(queued.length,2);
  context.joinedCommunitiesMap.set('ABC',{status:'ENTROU'});
  context.enqueueSupplierInvites({key:{remoteJid:'supplier@s.whatsapp.net'},message:{}});
  assert.equal(queued.length,2);
});
const processingSource=source.slice(source.indexOf('async function processInviteCode'),source.indexOf('const inviteQueue'));
function inviteContext(socket){
  const context={projectInviteDecision,isKnownSupplierJid:()=>true,isInviteBlocked:()=>false,waSocket:socket,connectionStatus:'CONNECTED',joinedCommunitiesMap:new Map(),pendingInviteCodes:new Set(),saveJoinedCommunities(){},registerMonitoredArchivedChat(){},groupSubjectCache:new Map(),saveMonitoredArchived(){},archiveChatJid:async()=>false,addLog(){},inviteErrorMessage:error=>error.message};
  vm.createContext(context);vm.runInContext(processingSource,context);return context;
}
test('confirmed entry is registered and never falsely labeled archived',async()=>{
  const context=inviteContext({groupGetInviteInfo:async()=>({id:'group@g.us',subject:'Supplier Group'}),groupAcceptInvite:async()=>'group@g.us'});
  const result=await context.processInviteCode('ABC');
  assert.equal(result.status,'ENTROU');
  assert.equal(context.joinedCommunitiesMap.size,1);
  assert.equal(context.pendingInviteCodes.size,0);
});
test('a missing join confirmation is not success; admin approval is a separate state',async()=>{
  let approval=false;
  const context=inviteContext({groupGetInviteInfo:async()=>({id:'group@g.us',joinApprovalMode:approval}),groupAcceptInvite:async()=>undefined});
  assert.equal((await context.processInviteCode('ABC')).status,'ERRO');
  approval=true;
  assert.equal((await context.processInviteCode('DEF')).status,'AGUARDANDO_APROVACAO');
});
test('expired invites do not get retried, network failures do',async()=>{
  let code=404;
  const context=inviteContext({groupGetInviteInfo:async()=>{throw Object.assign(new Error('failure'),{output:{statusCode:code}})}});
  assert.equal((await context.processInviteCode('ABC')).retryable,false);
  code=408;
  assert.equal((await context.processInviteCode('DEF')).retryable,true);
});
test('conflict responses require proof of current membership',async()=>{
  let member=false;
  const context=inviteContext({user:{id:'55119999:2@s.whatsapp.net'},groupGetInviteInfo:async()=>({id:'group@g.us'}),groupAcceptInvite:async()=>{throw Object.assign(new Error('conflict'),{output:{statusCode:409}})},groupMetadata:async()=>({participants:member?[{id:'55119999@s.whatsapp.net'}]:[]})});
  assert.equal((await context.processInviteCode('ABC')).status,'ERRO');
  member=true;
  assert.equal((await context.processInviteCode('DEF')).status,'ENTROU');
});

test('unrelated groups are rejected before accepting an invite, including supplier-forwarded links',async()=>{
  for(const subject of ['Desapega iPhones','Feira do rolo','Vendas Osasco e região','Capital Gain company']){
    let accepted=0;
    const context=inviteContext({groupGetInviteInfo:async()=>({id:'group@g.us',subject}),groupAcceptInvite:async()=>{accepted++;return 'group@g.us';}});
    const result=await context.processInviteCode('CODE');
    assert.equal(result.status,'FORA_ESCOPO');assert.equal(accepted,0);assert.equal(result.retryable,false);
  }
});

test('unidentified communities and explicitly blocked group IDs cannot be joined manually',async()=>{
  let accepted=0;
  const context=inviteContext({groupGetInviteInfo:async()=>({id:'group@g.us',subject:'Grupo qualquer'}),groupAcceptInvite:async()=>{accepted++;return 'group@g.us';}});
  context.isKnownSupplierJid=()=>false;
  assert.equal((await context.processInviteCode('UNKNOWN','Manual')).status,'REVISAR_ESCOPO');
  context.isKnownSupplierJid=()=>true;context.isInviteBlocked=()=>true;
  assert.equal((await context.processInviteCode('BLOCKED')).status,'FORA_ESCOPO');
  assert.equal(accepted,0);
});
