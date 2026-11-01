import test from 'node:test';
import assert from 'node:assert/strict';
import {HelperAccountPool} from './helper-pool.mjs';
const account=(uid,extra={})=>({id:'helper-'+uid,uid,accessToken:'token-'+uid,refreshToken:'refresh-'+uid,domain:'www.codebuddy.cn',enabled:true,nickname:uid,...extra});
function fixture(records){let clock=1000000;const source={records,fail:false};const client={fetchCredits:async c=>{if(c.accessToken==='bad')throw new Error('HTTP 401');return {total:100};},refreshHelper:async()=>{throw new Error('Helper login expired');}};const pool=new HelperAccountPool({read:async()=>{if(source.fail)throw new Error('unavailable');return source.records;},client,now:()=>clock});return {source,client,pool,tick:()=>clock+=31000};}
test('rotates all enabled unique Helper accounts, ignoring DSH historical duplicates',async()=>{const f=fixture([account('a'),account('b'),account('a'),account('c',{enabled:false}),account('global',{version:'intl'})]);await f.pool.sync(true);assert.deepEqual(await Promise.all(Array.from({length:6},async()=> (await f.pool.pick()).uid)),['a','b','a','b','a','b']);assert.equal(f.pool.status().accounts.length,3);});
test('failed login stays visible but is never selected',async()=>{const f=fixture([account('a',{accessToken:'bad'}),account('b')]);await f.pool.sync(true);assert.equal(f.pool.status().accounts[0].state,'needs_login');assert.equal((await f.pool.pick()).uid,'b');assert.doesNotMatch(JSON.stringify(f.pool.status()),/accessToken|refreshToken|token-b/);});
test('Helper re-login, disable, removal and addition are picked up continuously',async()=>{const f=fixture([account('a',{accessToken:'bad'}),account('b')]);await f.pool.sync(true);f.source.records=[account('a'),account('b',{enabled:false}),account('c')];f.tick();await f.pool.sync();assert.deepEqual((await Promise.all([f.pool.pick(),f.pool.pick()])).map(c=>c.uid),['a','c']);f.source.records=[account('c')];f.tick();await f.pool.sync();assert.equal(f.pool.status().accounts.length,1);assert.equal((await f.pool.pick()).uid,'c');});
test('missing authoritative Helper store fails closed rather than serving removed accounts',async()=>{const f=fixture([account('a')]);await f.pool.sync(true);f.source.fail=true;f.tick();await assert.rejects(f.pool.pick(),/Helper account store/);});
test('failover never selects an already attempted account',async()=>{const f=fixture([account('a'),account('b')]);await f.pool.sync(true);const first=await f.pool.pick(),id=f.pool.status().accounts.find(a=>a.name===first.uid).id;assert.equal((await f.pool.pick([id])).uid,'b');assert.equal(await f.pool.pick(f.pool.status().accounts.map(a=>a.id)),undefined);});
test('exhausted accounts and cooldowns do not loop',async()=>{const f=fixture([account('a'),account('b')]);f.client.fetchCredits=async c=>({total:c.uid==='a'?0:100});await f.pool.sync(true);const id=f.pool.status().accounts.find(e=>e.name==='b').id;f.pool.failure(id,{kind:'soft_rate'});assert.equal(await f.pool.pick(),undefined);assert.equal(f.pool.status().accounts[0].state,'empty');});
test('model-wide busy and malformed requests do not disable accounts',async()=>{const f=fixture([account('a')]);await f.pool.sync(true);const id=f.pool.status().accounts[0].id;f.pool.failure(id,{kind:'model_busy'});f.pool.failure(id,{kind:'client'});assert.equal((await f.pool.pick()).uid,'a');});
test('Helper owns encrypted refresh and renewed credentials are used',async()=>{const f=fixture([account('a',{accessToken:'bad'})]);f.client.refreshHelper=async()=>{f.source.records=[account('a')];};await f.pool.sync(true);assert.equal((await f.pool.pick()).accessToken,'token-a');assert.equal(f.pool.status().accounts[0].state,'ready');});

test('manual selection stays on requested account and never falls back',async()=>{const f=fixture([account('a'),account('b',{accessToken:'bad'})]);await f.pool.sync(true);const entries=f.pool.status().accounts;assert.equal((await f.pool.resolveAccount(entries[0].id)).uid,'a');await assert.rejects(f.pool.resolveAccount(entries[1].id),/Selected Helper account is unavailable/);assert.equal(f.pool.status().accounts[0].uses,1);await assert.rejects(f.pool.resolveAccount('missing'),/absent/);});

test('manual retry is not blocked by automatic scheduling cooldown or another model rejection',async()=>{const f=fixture([account('a')]);await f.pool.sync(true);const id=f.pool.status().accounts[0].id;f.pool.failure(id,{kind:'soft_rate'});assert.equal(await f.pool.pick(),undefined);assert.equal((await f.pool.resolveAccount(id)).uid,'a');f.pool.failure(id,{kind:'policy_reject'});assert.equal((await f.pool.resolveAccount(id)).uid,'a');});

test('preferred routes fall back on unavailable, quota and cooldown without repeating accounts',async()=>{
 const f=fixture([account('a'),account('b')]);await f.pool.sync(true);const id=f.pool.status().accounts[0].id;
 assert.equal((await f.pool.resolvePreferredAccount(id)).uid,'a');
 f.pool.entries.get(id).state='unavailable';assert.equal((await f.pool.resolvePreferredAccount(id)).uid,'b');
 f.pool.entries.get(id).state='ready';f.pool.failure(id,{kind:'soft_rate'});assert.equal((await f.pool.resolvePreferredAccount(id)).uid,'b');
 f.pool.failure(id,{kind:'hard_credit'});assert.equal((await f.pool.resolvePreferredAccount(id)).uid,'b');
 f.pool.failure(f.pool.status().accounts[1].id,{kind:'hard_credit'});await assert.rejects(f.pool.resolvePreferredAccount(id),/No available Helper account/);
});
test('preferred routes never mask disabled, removed or unreadable Helper state',async()=>{
 const f=fixture([account('a'),account('b')]);await f.pool.sync(true);const id=f.pool.status().accounts[0].id;
 f.pool.entries.get(id).enabled=false;await assert.rejects(f.pool.resolvePreferredAccount(id),/absent or disabled/);
 await assert.rejects(f.pool.resolvePreferredAccount('missing'),/absent or disabled/);
 f.source.fail=true;f.tick();await assert.rejects(f.pool.resolvePreferredAccount(id),/Helper account store/);
});
