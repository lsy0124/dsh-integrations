import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createForwarder,collectChatStream} from './server.mjs';
import {Readable} from 'node:stream';

test('only ZCode compatibility route hides reasoning in streamed and assembled responses',async t=>{
 const upstream=http.createServer((req,res)=>{assert.equal(req.url,'/v1/chat/completions');res.writeHead(200,{'content-type':'text/event-stream'});for(const delta of [{role:'assistant',reasoning_content:'fixture-thought'},{content:'fixture-answer'}])res.write('data: '+JSON.stringify({choices:[{index:0,delta,finish_reason:null}]})+'\n\n');res.end('data: [DONE]\n\n');});await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
 const front=createForwarder({target:`http://127.0.0.1:${upstream.address().port}`,upstreamKey:'fixture',clientKey:'fixture'});await new Promise(r=>front.listen(0,'127.0.0.1',r));t.after(()=>{front.closeAllConnections();upstream.closeAllConnections();front.close();upstream.close();});
 const call=async(route,stream)=>{const r=await fetch(`http://127.0.0.1:${front.address().port}`+route,{method:'POST',headers:{authorization:'Bearer fixture','content-type':'application/json'},body:JSON.stringify({stream,messages:[]})});assert.equal(r.status,200);return r.text();};
 assert.doesNotMatch(await call('/zcode/v1/chat/completions',true),/fixture-thought|reasoning_content/);assert.match(await call('/v1/chat/completions',true),/fixture-thought/);const assembled=await call('/zcode/v1/chat/completions',false);assert.doesNotMatch(assembled,/fixture-thought|reasoning_content/);assert.match(assembled,/fixture-answer/);
});
async function fixture(t){
  let calls=0;const key='fixture-only';
  const upstream=http.createServer((req,res)=>{calls++;assert.equal(req.headers.authorization,'Bearer internal-fixture');res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({path:req.url}));});
  await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
  const front=createForwarder({target:`http://127.0.0.1:${upstream.address().port}`,upstreamKey:'internal-fixture',clientKey:key,maxBytes:100});await new Promise(r=>front.listen(0,'127.0.0.1',r));
  t.after(()=>{front.closeAllConnections();upstream.closeAllConnections();front.close();upstream.close();});return {url:`http://127.0.0.1:${front.address().port}`,key,calls:()=>calls};
}
test('health is identified without exposing account or API keys',async t=>{const f=await fixture(t);assert.deepEqual(await(await fetch(f.url+'/healthz')).json(),{app:'workbuddy-clients',ok:true});assert.equal(f.calls(),0);});
test('unauthorized and foreign-origin requests never reach upstream',async t=>{const f=await fixture(t);assert.equal((await fetch(f.url+'/v1/models')).status,401);assert.equal((await fetch(f.url+'/v1/models',{headers:{authorization:'Bearer '+f.key,origin:'https://example.com'}})).status,403);assert.equal(f.calls(),0);});
test('supported requests forward with internal key; arbitrary paths are rejected',async t=>{const f=await fixture(t);const headers={authorization:'Bearer '+f.key};assert.deepEqual(await(await fetch(f.url+'/v1/models',{headers})).json(),{path:'/v1/models'});assert.equal((await fetch(f.url+'/admin',{headers})).status,404);assert.equal(f.calls(),1);});
test('large request bodies are rejected before forwarding',async t=>{const f=await fixture(t);assert.equal((await fetch(f.url+'/v1/chat/completions',{method:'POST',headers:{authorization:'Bearer '+f.key},body:'x'.repeat(101)})).status,413);assert.equal(f.calls(),0);});

test('account diagnostics require the local client key',async t=>{
 const front=createForwarder({target:'http://127.0.0.1:1',upstreamKey:'internal',clientKey:'local',accountStatus:()=>({accounts:[{name:'fixture',state:'ready'}]})});await new Promise(r=>front.listen(0,'127.0.0.1',r));t.after(()=>{front.closeAllConnections();front.close();});const url=`http://127.0.0.1:${front.address().port}/v1/accounts`;
 assert.equal((await fetch(url)).status,401);assert.equal((await(await fetch(url,{headers:{authorization:'Bearer local'}})).json()).accounts[0].state,'ready');
});
test('nonstream assembly preserves UTF-8, reasoning, tool deltas and usage',async()=>{
 const events=[{id:'fixture',model:'fixture',choices:[{index:0,delta:{reasoning_content:'思考',content:'你好',tool_calls:[{index:0,id:'call-1',function:{name:'example',arguments:'{"x":'}}]}}]},{choices:[{index:0,delta:{tool_calls:[{index:0,function:{arguments:'1}'}}]},finish_reason:'tool_calls'}],usage:{total_tokens:2}}];
 const bytes=Buffer.from(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join('')+'data: [DONE]\r\n\r\n');
 const result=await collectChatStream(Readable.from([...bytes].map(byte=>Buffer.from([byte]))));assert.equal(result.choices[0].message.content,'你好');assert.equal(result.choices[0].message.reasoning_content,'思考');assert.equal(result.choices[0].message.tool_calls[0].function.arguments,'{"x":1}');assert.equal(result.usage.total_tokens,2);
});
