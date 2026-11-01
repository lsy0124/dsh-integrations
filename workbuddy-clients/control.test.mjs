import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createControl} from './control.mjs';
import {createForwarder} from './server.mjs';
async function fixture(t){
 let selected=null,refreshes=0;
 const control=createControl({status:()=>({selectedAccountId:selected}),select:async id=>{selected=id;},refresh:async()=>{refreshes++;},html:'<script nonce="__NONCE__">const csrf="__CSRF__"</script>'});
 const server=createForwarder({target:'http://127.0.0.1:1',clientKey:'local',upstreamKey:'internal',control});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>{server.closeAllConnections();server.close();});const base=`http://127.0.0.1:${server.address().port}`;
 const page=await fetch(base+'/accounts'),html=await page.text();return {base,cookie:page.headers.get('set-cookie').split(';')[0],csrf:/csrf="([^"]+)"/.exec(html)[1],refreshes:()=>refreshes,page};
}
test('local panel does not expose client key and session cookie is HttpOnly',async t=>{const f=await fixture(t);assert.match(f.page.headers.get('set-cookie'),/HttpOnly; SameSite=Strict/);assert.equal(f.page.headers.get('x-frame-options'),'DENY');assert.equal((await fetch(f.base+'/control/status')).status,401);});
test('manual selection requires same-origin CSRF session, then persists through status',async t=>{const f=await fixture(t);const id='a'.repeat(24),headers={cookie:f.cookie,'content-type':'application/json','x-csrf-token':f.csrf,origin:f.base};assert.equal((await fetch(f.base+'/control/selection',{method:'POST',headers:{cookie:f.cookie},body:JSON.stringify({accountId:id})})).status,403);const r=await fetch(f.base+'/control/selection',{method:'POST',headers,body:JSON.stringify({accountId:id})});assert.equal((await r.json()).selectedAccountId,id);assert.equal((await fetch(f.base+'/control/status',{headers:{cookie:f.cookie}})).status,200);});
test('external web origin cannot refresh or change selection',async t=>{const f=await fixture(t);assert.equal((await fetch(f.base+'/control/refresh',{method:'POST',headers:{cookie:f.cookie,'x-csrf-token':f.csrf,origin:'https://example.com'}})).status,403);assert.equal(f.refreshes(),0);});
test('pinned account route forwards only its normalized OpenAI route',async t=>{let seen;const upstream=http.createServer((req,res)=>{seen=req.url;res.end('{}');});await new Promise(r=>upstream.listen(0,'127.0.0.1',r));t.after(()=>{upstream.closeAllConnections();upstream.close();});const id='b'.repeat(24);const server=createForwarder({clientKey:'local',resolveTarget:async account=>{assert.equal(account,id);return {target:`http://127.0.0.1:${upstream.address().port}`,upstreamKey:'internal'};}});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>{server.closeAllConnections();server.close();});const r=await fetch(`http://127.0.0.1:${server.address().port}/accounts/${id}/v1/models`,{headers:{authorization:'Bearer local'}});assert.equal(r.status,200);assert.equal(seen,'/v1/models');});
