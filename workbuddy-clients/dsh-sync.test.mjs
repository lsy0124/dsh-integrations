import test from 'node:test';
import assert from 'node:assert/strict';
import {createDshSynchronizer} from './dsh-sync.mjs';
const entry=(id,state='ready')=>({id,enabled:true,state,credential:{uid:id,accessToken:'fixture-'+id,refreshToken:'fixture-refresh',nickname:id,domain:'www.codebuddy.cn',expiresAtMs:9999999999999}});
test('merges newly added accounts and refresh changes once, keeping DSH historical accounts',async()=>{
 const imported=[],known=new Set(['old','historical']),pool={sourceState:'ready',entries:new Map([['old',entry('old')],['new',entry('new')],['dead',entry('dead','needs_login')]])};
 const sync=createDshSynchronizer({connect:async()=>({}),send:async(c,route,body)=>{if(body){const records=JSON.parse(body.text);imported.push(records.map(r=>r.uid));for(const r of records)known.add(r.uid);return {imported:records.length};}return {accounts:[...known].map(id=>({id}))};}});
 assert.equal((await sync(pool)).imported,2);assert.deepEqual(imported,[['old','new']]);assert.equal((await sync(pool)).imported,0);assert.ok(known.has('historical'));
 pool.entries.get('new').credential.accessToken='renewed-fixture';assert.equal((await sync(pool)).imported,1);assert.deepEqual(imported,[['old','new'],['new']]);
});
test('unavailable Helper source never writes to DSH',async()=>{let called=false;const sync=createDshSynchronizer({connect:async()=>{called=true;}});assert.equal((await sync({sourceState:'unavailable'})).state,'source-unavailable');assert.equal(called,false);});
test('unconfirmed imports are retried instead of silently marked synchronized',async()=>{const pool={sourceState:'ready',entries:new Map([['a',entry('a')]])};let attempts=0;const sync=createDshSynchronizer({connect:async()=>({}),send:async(c,r,body)=>{if(body){attempts++;return {};}return {accounts:[]};}});await assert.rejects(sync(pool),/not confirmed/);await assert.rejects(sync(pool),/not confirmed/);assert.equal(attempts,2);});
