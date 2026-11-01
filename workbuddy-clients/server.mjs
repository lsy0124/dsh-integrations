import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {pipeline} from 'node:stream';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {StringDecoder} from 'node:string_decoder';
import {HelperAccountPool,refreshHelperAccount} from './helper-pool.mjs';
import {createControl} from './control.mjs';
import {synchronizeSelectors} from './configure-selectors.mjs';
import {createDshSynchronizer} from './dsh-sync.mjs';
import {createStreamCompat} from './stream-compat.mjs';

const ROOT=fileURLToPath(new URL('.',import.meta.url));
export async function collectChatStream(incoming){
  const decoder=new StringDecoder('utf8');let pending='',bytes=0,meta={},usage,streamError;
  const choices=new Map();
  const frame=text=>{
    const data=text.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
    if(!data||data==='[DONE]')return;
    const value=JSON.parse(data);if(value.error){streamError=value.error;return;}
    for(const field of ['id','model','created'])if(value[field]!==undefined)meta[field]=value[field];
    if(value.usage)usage=value.usage;
    for(const item of value.choices||[]){
      const index=item.index??0;let choice=choices.get(index);if(!choice){choice={index,finish_reason:null,message:{role:'assistant',content:''},tools:new Map()};choices.set(index,choice);}
      const delta=item.delta||item.message||{};
      if(typeof delta.content==='string')choice.message.content+=delta.content;
      if(typeof delta.reasoning_content==='string')choice.message.reasoning_content=(choice.message.reasoning_content||'')+delta.reasoning_content;
      for(const tool of delta.tool_calls||[]){const i=tool.index??0;let current=choice.tools.get(i);if(!current){current={id:'',type:'function',function:{name:'',arguments:''}};choice.tools.set(i,current);}if(tool.id)current.id=tool.id;if(tool.function?.name)current.function.name+=tool.function.name;if(tool.function?.arguments)current.function.arguments+=tool.function.arguments;}
      if(item.finish_reason!==undefined&&item.finish_reason!==null)choice.finish_reason=item.finish_reason;
    }
  };
  for await(const chunk of incoming){bytes+=chunk.length;if(bytes>32*1024*1024)throw new Error('response limit');pending+=decoder.write(chunk);let match;while((match=/\r?\n\r?\n/.exec(pending))){frame(pending.slice(0,match.index));pending=pending.slice(match.index+match[0].length);}}
  pending+=decoder.end();if(pending.trim())frame(pending);
  if(streamError)throw new Error('upstream stream error');
  return {...meta,object:'chat.completion',choices:[...choices.values()].sort((a,b)=>a.index-b.index).map(({tools,...choice})=>{if(tools.size){choice.message.tool_calls=[...tools.entries()].sort((a,b)=>a[0]-b[0]).map(([,tool])=>tool);if(!choice.message.content)choice.message.content=null;}return choice;}),...(usage?{usage}:{})};
}
export function createForwarder({target,upstreamKey,clientKey,accountStatus,control,resolveTarget,maxBytes=16*1024*1024}) {
  const expected=crypto.createHash('sha256').update(clientKey).digest();
  return http.createServer(async(req,res)=>{
    const reply=(status,body)=>{res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(body));};
    const host=String(req.headers.host||'').split(':')[0];
    if(!['127.0.0.1','localhost'].includes(host)){reply(403,{error:{message:'Loopback Host required'}});return;}
    if(req.headers.origin){try{if(!['127.0.0.1','localhost','[::1]'].includes(new URL(req.headers.origin).hostname))throw new Error();}catch{reply(403,{error:{message:'Origin not allowed'}});return;}}
    if(req.method==='GET'&&req.url==='/healthz'){reply(200,{app:'workbuddy-clients',ok:true});return;}
    if(control&&await control(req,res))return;
    const supplied=String(req.headers.authorization||'').replace(/^Bearer\s+/i,'');
    if(!crypto.timingSafeEqual(expected,crypto.createHash('sha256').update(supplied).digest())){reply(401,{error:{message:'Invalid local API key'}});return;}
    if(req.method==='GET'&&req.url==='/v1/accounts'&&accountStatus){reply(200,accountStatus());return;}
    const hideReasoning=req.url.startsWith('/zcode/');
    const requestPath=hideReasoning?req.url.slice('/zcode'.length):req.url;
    const pinned=/^\/accounts\/([a-f0-9]{24})(\/v1\/(?:models|chat\/completions))$/.exec(requestPath);
    const route=pinned?pinned[2]:requestPath;
    if(!((req.method==='GET'&&route==='/v1/models')||(req.method==='POST'&&route==='/v1/chat/completions'))){reply(404,{error:{message:'Unsupported route'}});return;}
    const chunks=[];let bytes=0;
    try{
      for await(const chunk of req){bytes+=chunk.length;if(bytes>maxBytes){reply(413,{error:{message:'Request too large'}});return;}chunks.push(chunk);}
    }catch{return;}
    let streaming=true;
    if(req.method==='POST'){try{streaming=JSON.parse(Buffer.concat(chunks).toString('utf8')).stream===true;}catch{reply(400,{error:{message:'Invalid JSON body'}});return;}}
    let destination={target,upstreamKey};
    try{if(resolveTarget)destination=await resolveTarget(pinned?.[1]);else if(pinned)throw new Error();}catch{reply(404,{error:{message:'Selected Helper account is not present; refresh account settings'}});return;}
    const url=new URL(route,destination.target);
    const upstream=http.request(url,{method:req.method,headers:{authorization:'Bearer '+destination.upstreamKey,'content-type':'application/json',...bytes?{'content-length':String(bytes)}:{}},timeout:180000},incoming=>{
      if(!streaming&&incoming.statusCode===200&&String(incoming.headers['content-type']).includes('text/event-stream')){
        collectChatStream(hideReasoning?incoming.pipe(createStreamCompat({hideReasoning:true})):incoming).then(value=>reply(200,value)).catch(()=>{if(!res.headersSent)reply(502,{error:{message:'Could not assemble upstream completion'}});});return;
      }
      res.writeHead(incoming.statusCode||502,{'content-type':incoming.headers['content-type']||'application/json','cache-control':'no-store','x-accel-buffering':'no'});
      if(streaming&&incoming.statusCode===200&&String(incoming.headers['content-type']).includes('text/event-stream'))pipeline(incoming,createStreamCompat({hideReasoning}),res,()=>{});
      else pipeline(incoming,res,()=>{});
    });
    upstream.on('timeout',()=>upstream.destroy(new Error('timeout')));
    upstream.on('error',()=>{if(!res.headersSent)reply(502,{error:{message:'WorkBuddy request failed; check local service and account login'}});else res.destroy();});
    res.on('close',()=>{if(!res.writableEnded)upstream.destroy();});
    upstream.end(bytes?Buffer.concat(chunks):undefined);
  });
}

export async function start(){
  const home=process.env.DSH_HOME||path.join(process.env.APPDATA,'dsh-desktop','harness');process.env.DSH_HOME=home;
  const runtime=path.join(ROOT,'runtime');await fs.mkdir(runtime,{recursive:true});
  const settingsPath=path.join(runtime,'client.json');let settings;
  try{settings=JSON.parse(await fs.readFile(settingsPath,'utf8'));}
  catch(error){if(error.code!=='ENOENT')throw new Error('Local client settings are invalid; not overwritten');settings={port:18181,region:'cn',clientKey:crypto.randomBytes(32).toString('base64url')};await fs.writeFile(settingsPath,JSON.stringify(settings,null,2),{mode:0o600,flag:'wx'});}
  if(!Number.isInteger(settings.port)||settings.port<1024||settings.port>65535||typeof settings.clientKey!=='string'||settings.clientKey.length<32)throw new Error('Invalid local service settings');
  const mod=await import(pathToFileURL(path.join(home,'profiles','web','node_modules','dsh-connect-workbuddy','lib','index.js')));
  const client=new mod.WorkBuddyUpstreamClient();
  const recentRequests=[];
  const chatClient={chatStream:async(credential,body,signal)=>{
    const result=await client.chatStream(credential,body,signal);let model;try{model=JSON.parse(body).model;}catch{}
    const accountId=crypto.createHash('sha256').update('workbuddy\0'+credential.uid).digest('hex').slice(0,24);
    recentRequests.push({at:new Date().toISOString(),accountId,model:typeof model==='string'&&/^[\w./-]{1,100}$/.test(model)?model:'unknown',ok:result.ok,status:result.ok?result.response.status:result.status,...result.ok?{}:{kind:result.kind}});if(recentRequests.length>20)recentRequests.shift();return result;
  }};
  const pool=new HelperAccountPool({client:{fetchCredits:c=>client.fetchCredits(c),refreshHelper:refreshHelperAccount}});
  await pool.sync(true);
  let selectorSync={state:'starting'},selectorJob;
  const syncDsh=createDshSynchronizer({home});
  const syncSelectors=()=>{
    if(selectorJob)return selectorJob;
    selectorJob=(async()=>{
      if(pool.sourceState!=='ready')return;
      const result=await synchronizeSelectors({accounts:pool.status().accounts,base:`http://127.0.0.1:${settings.port}`});
      let dsh;try{dsh=await syncDsh(pool);}catch{dsh={state:'retry-needed'};}
      selectorSync={...result,dsh,accounts:pool.entries.size,at:new Date().toISOString()};
    })().catch(()=>{selectorSync={state:'retry-needed'};}).finally(()=>{selectorJob=null;});return selectorJob;
  };
  await syncSelectors();
  const first=[...pool.entries.values()].find(e=>e.enabled&&e.state==='ready');
  if(!first)throw new Error('No authorized Helper account; refresh or log in through Helper');
  const store={resolve:async()=>{const credential=await pool.pick();if(!credential)throw new Error('No available Helper account; inspect /v1/accounts');return credential;}};
  const models=await client.fetchModels(first.credential);
  const catalog=new mod.WorkBuddyCatalog(settings.region);catalog.set(mod.deriveCatalog(models,new Set()));
  const shim=mod.createWorkBuddyShim({store,client:chatClient,catalog,failoverAccount:tried=>pool.pick(tried),onAccountFailure:(id,failure)=>pool.failure(id,failure),logger:{warn:()=>{},error:()=>{}}});await shim.ready;
  const pinnedShims=new Map();
  const status=()=>({...pool.status(),selectedAccountId:settings.selectedAccountId||null,recentRequests,selectorSync});
  let save=Promise.resolve();
  const control=createControl({status,refresh:async()=>{await pool.recheck();await syncSelectors();},select:id=>{
    const next=save.catch(()=>{}).then(async()=>{
      await pool.sync();if(id!==null){const e=pool.entries.get(id);if(!e||!e.enabled||e.state!=='ready')throw new Error('Account unavailable');}
      const nextSettings={...settings,selectedAccountId:id};const temp=settingsPath+'.tmp';await fs.writeFile(temp,JSON.stringify(nextSettings,null,2),{mode:0o600});await fs.rename(temp,settingsPath);settings=nextSettings;
    });save=next;return next;
  }});
  const resolveTarget=async explicitId=>{
    const id=explicitId||settings.selectedAccountId;
    if(!id)return {target:shim.baseUrl(),upstreamKey:shim.token()};
    await pool.sync();if(!pool.entries.has(id))throw new Error('Account missing');
    if(!pinnedShims.has(id))pinnedShims.set(id,(async()=>{
      const pinned=mod.createWorkBuddyShim({store:{resolve:()=>pool.resolvePreferredAccount(id)},client:chatClient,catalog,failoverAccount:tried=>pool.pick(tried),onAccountFailure:(failed,failure)=>pool.failure(failed,failure),logger:{warn:()=>{},error:()=>{}}});await pinned.ready;return pinned;
    })());
    const pinned=await pinnedShims.get(id);return {target:pinned.baseUrl(),upstreamKey:pinned.token()};
  };
  const front=createForwarder({target:shim.baseUrl(),upstreamKey:shim.token(),clientKey:settings.clientKey,accountStatus:status,control,resolveTarget});
  try{await new Promise((resolve,reject)=>{front.once('error',reject);front.listen(settings.port,'127.0.0.1',resolve);});}catch(error){await shim.close();throw error;}
  delete settings.accountId;settings.accountMode='helper-pool';await fs.writeFile(settingsPath,JSON.stringify(settings,null,2),{mode:0o600});
  await fs.writeFile(path.join(runtime,'status.json'),JSON.stringify({app:'workbuddy-clients',pid:process.pid,port:settings.port,region:settings.region,modelIds:catalog.current().map(m=>m.id),startedAt:new Date().toISOString()},null,2),{mode:0o600});
  console.log(JSON.stringify({app:'workbuddy-clients',baseUrl:`http://127.0.0.1:${settings.port}/v1`,models:catalog.current().length,accountMode:'helper-pool',accounts:pool.entries.size,ready:[...pool.entries.values()].filter(e=>e.state==='ready'&&e.enabled).length}));
  const stop=async()=>{front.closeAllConnections();front.close();await shim.close();for(const promise of pinnedShims.values()){try{await(await promise).close();}catch{}}process.exit(0);};process.once('SIGINT',stop);process.once('SIGTERM',stop);
  const sync=setInterval(async()=>{try{await pool.sync();await syncSelectors();}catch{}},30000);sync.unref();
  const refresh=setInterval(async()=>{try{const credential=[...pool.entries.values()].find(e=>e.enabled&&e.state==='ready')?.credential;if(credential)catalog.set(mod.deriveCatalog(await client.fetchModels(credential),new Set()));}catch{}},10*60*1000);refresh.unref();
  return {front,shim,pool};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))start().catch(()=>{console.error('WorkBuddy client bridge failed to start. Check account login, installed plugin and available port.');process.exitCode=1;});
