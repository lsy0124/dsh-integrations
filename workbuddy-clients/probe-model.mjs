import fs from 'node:fs/promises';
import {Readable} from 'node:stream';
import {collectChatStream} from './server.mjs';
const config=JSON.parse(await fs.readFile(new URL('./runtime/client.json',import.meta.url),'utf8'));
const model=process.argv[2]||'mimo-v2.6-pro';
const base=`http://127.0.0.1:${config.port}`,headers={authorization:'Bearer '+config.clientKey,'content-type':'application/json'};
let route='/v1/chat/completions';if(process.argv[3]){const s=await(await fetch(base+'/v1/accounts',{headers})).json();const a=s.accounts.find(a=>a.name===process.argv[3]);if(!a)throw new Error('Account not found');route='/accounts/'+a.id+route;}
const res=await fetch(base+route,{method:'POST',headers,body:JSON.stringify({model,max_tokens:2048,stream:true,messages:[{role:'user',content:'连接检查，请只回复 OK，不调用工具。'}]}),signal:AbortSignal.timeout(90000)});
if(!res.ok){console.log(JSON.stringify({model,http:res.status,ok:false}));process.exitCode=1;}else{
const body=await collectChatStream(Readable.fromWeb(res.body));
const c=body.choices?.[0];console.log(JSON.stringify({model,http:res.status,content:c?.message?.content,reasoningCharacters:c?.message?.reasoning_content?.length||0,finishReason:c?.finish_reason,usage:body.usage}));
if(c?.message?.content?.trim()!=='OK')process.exitCode=1;
}
