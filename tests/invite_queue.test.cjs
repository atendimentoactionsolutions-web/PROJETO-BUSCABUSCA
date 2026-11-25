const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
async function fixture(execute){
  const {InviteQueue}=await import('../invite_queue.js');
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'buscabusca-queue-'));
  const file=path.join(directory,'queue.json');
  const state={connected:false,time:100000};
  const options={file,execute,connected:()=>state.connected,now:()=>state.time};
  const queue=new InviteQueue(options);
  return {queue,state,file,options,InviteQueue,close(){queue.stop();fs.rmSync(directory,{recursive:true,force:true})}};
}
test('invites survive restarts, stay paused offline, and are deduplicated',async()=>{
  let calls=0;const f=await fixture(async()=>{calls++;return {status:'ENTROU'}});
  try{
    assert.equal(f.queue.enqueue({code:'ABC'}),true);
    assert.equal(f.queue.enqueue({code:'ABC'}),false);
    await f.queue.run();assert.equal(calls,0);
    const resumed=new f.InviteQueue(f.options);
    f.state.connected=true;await resumed.run();resumed.stop();
    assert.equal(calls,1);assert.equal(resumed.items.get('ABC').status,'ENTROU');
  }finally{f.close()}
});
test('concurrent events process a queued invite once',async()=>{
  let calls=0;const f=await fixture(async()=>{calls++;await new Promise(r=>setTimeout(r,10));return {status:'ENTROU'}});
  try{f.queue.enqueue({code:'ABC'});f.state.connected=true;await Promise.all([f.queue.run(),f.queue.run(),f.queue.run()]);assert.equal(calls,1)}finally{f.close()}
});
test('transient failures retry with delay and keep pending work',async()=>{
  let calls=0;const f=await fixture(async()=>++calls===1?{status:'ERRO',retryable:true}:{status:'ENTROU'});
  try{f.queue.enqueue({code:'ABC'});f.state.connected=true;await f.queue.run();assert.equal(f.queue.items.get('ABC').status,'PENDENTE');await f.queue.run();assert.equal(calls,1);f.state.time+=3001;await f.queue.run();assert.equal(calls,2);assert.equal(f.queue.items.get('ABC').status,'ENTROU')}finally{f.close()}
});
test('permanent failures and pending approval are not retried',async()=>{
  let calls=0;const f=await fixture(async item=>{calls++;return item.code==='ABC'?{status:'ERRO',retryable:false}:{status:'AGUARDANDO_APROVACAO'}});
  try{f.queue.enqueue({code:'ABC'});f.queue.enqueue({code:'DEF'});f.state.connected=true;await f.queue.run();await f.queue.run();f.state.time+=100000;await f.queue.run();assert.equal(calls,2);assert.equal(f.queue.items.get('DEF').status,'AGUARDANDO_APROVACAO')}finally{f.close()}
});
test('rate limits wait at least one minute and attempts are bounded',async()=>{
  const f=await fixture(async()=>({status:'ERRO',retryable:true,errorCode:429}));
  try{f.queue.enqueue({code:'ABC'});f.state.connected=true;for(let i=0;i<5;i++){await f.queue.run();if(i<4)assert.equal(f.queue.items.get('ABC').nextAttempt,f.state.time+60000);f.state.time+=60001}assert.equal(f.queue.items.get('ABC').status,'ERRO');assert.equal(f.queue.items.get('ABC').attempts,5)}finally{f.close()}
});
