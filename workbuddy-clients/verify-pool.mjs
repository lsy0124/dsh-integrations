import fs from 'node:fs/promises';
const config=JSON.parse(await fs.readFile(new URL('./runtime/client.json',import.meta.url),'utf8'));
const base=`http://127.0.0.1:${config.port}`;
const headers={authorization:'Bearer '+config.clientKey,'content-type':'application/json'};
const getStatus=async()=>await(await fetch(base+'/v1/accounts',{headers})).json();
const initial=await getStatus();
const count=initial.accounts.filter(a=>a.state==='ready'&&a.enabled).length;
const results=[];
for(let i=0;i<count;i++){
  const before=await getStatus();
  const response=await fetch(base+'/v1/chat/completions',{method:'POST',headers,body:JSON.stringify({model:'hy3',reasoning_effort:'off',max_tokens:128,stream:false,messages:[{role:'user',content:'接入验证：请只回复 OK，不调用工具。'}]}),signal:AbortSignal.timeout(60000)});
  const body=await response.json();
  const after=await getStatus();
  const used=after.accounts.filter(a=>a.uses>(before.accounts.find(b=>b.id===a.id)?.uses||0)).map(a=>a.name);
  const ok=response.ok&&body.choices?.[0]?.message?.content?.trim()==='OK';
  const result={accounts:used,ok,http:response.status};results.push(result);console.log(JSON.stringify(result));
}
const final=await getStatus();
console.log(JSON.stringify({connected:final.accounts.length,ready:final.accounts.filter(a=>a.state==='ready').length,accounts:final.accounts.map(({name,state})=>({name,state}))}));
await fs.writeFile(new URL('./runtime/pool-verification.json',import.meta.url),JSON.stringify({at:new Date().toISOString(),results,accounts:final.accounts},null,2));
if(results.some(r=>!r.ok))process.exitCode=1;
