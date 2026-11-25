const {test}=require('node:test');
const assert=require('node:assert/strict');
test('project scope retains supplier communities while blocking general marketplaces',async()=>{
 const {projectInviteDecision:decide}=await import('../invite_policy.js');
 for(const name of ['JR CELULAR LISTA DE PREÇO ATACADO','AF IPHONES','Wingy Cell','Lista Apple','KAYAN LOJA'])assert.equal(decide(name,{knownSupplier:true}).allowed,true,name);
 for(const name of ['Desapega celular','Feira do rolo Biritiba','COMPRAS VENDAS E DOAÇÕES CAPÃO REDONDO','Taboão Condomínio Vendas Geral','Diadema e Região - Venda de Produtos e Serviços'])assert.equal(decide(name,{knownSupplier:true}).allowed,false,name);
 assert.equal(decide('Grupo qualquer').status,'REVISAR_ESCOPO');
 assert.equal(decide('Apple fornecedor',{blocked:true,knownSupplier:true}).allowed,false);
});
test('membership guard leaves only explicitly blocked groups and deduplicates concurrent events',async()=>{
 const {createBlockedMembershipGuard}=await import('../invite_policy.js');
 let calls=0,left=0,release;
 const wait=new Promise(resolve=>release=resolve);
 const guard=createBlockedMembershipGuard({getSocket:()=>({groupLeave:async()=>{calls++;await wait;}}),isConnected:()=>true,isBlocked:jid=>jid==='blocked@g.us',onLeft:()=>left++,onError:error=>{throw error;}});
 assert.equal(await guard('supplier@g.us'),false);
 const first=guard('blocked@g.us');
 assert.equal(await guard('blocked@g.us'),false);
 release();assert.equal(await first,true);assert.equal(calls,1);assert.equal(left,1);
});
test('membership guard reports a failed leave without repeated automatic attempts',async()=>{
 const {createBlockedMembershipGuard}=await import('../invite_policy.js');
 let calls=0,errors=0;
 const guard=createBlockedMembershipGuard({getSocket:()=>({groupLeave:async()=>{calls++;throw new Error('offline');}}),isConnected:()=>true,isBlocked:()=>true,onLeft:()=>assert.fail(),onError:()=>errors++});
 assert.equal(await guard('blocked@g.us'),false);assert.equal(calls,1);assert.equal(errors,1);
});
