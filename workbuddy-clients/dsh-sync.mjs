import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

export async function readDshConnection(home){
  const file=await fs.open(path.join(home,'..','logs','harness.log'),'r');let text;
  try{const size=(await file.stat()).size,buffer=Buffer.alloc(Math.min(size,512*1024));await file.read(buffer,0,buffer.length,size-buffer.length);text=buffer.toString('utf8').replace(/\x1b\[[0-9;]*m/g,'');}finally{await file.close();}
  const matches=[...text.matchAll(/dsh web: (http:\/\/[^\s]+)/g)];
  const url=new URL(matches.at(-1)?.[1]||'invalid:');
  if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname)||!url.searchParams.get('token'))throw new Error('Live local DSH Desktop endpoint unavailable');
  return {base:url.origin,headers:{Authorization:'Bearer '+url.searchParams.get('token'),Origin:url.origin}};
}
async function request(connection,route,body){
  const response=await fetch(connection.base+route,{headers:{...connection.headers,...body?{'content-type':'application/json'}:{}},...body?{method:'POST',body:JSON.stringify(body)}:{},signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw new Error('Local DSH plugin request failed');return response.json();
}
export function createDshSynchronizer({home,connect=()=>readDshConnection(home),send=request}){
  const previous=new Map();
  return async pool=>{
    if(pool.sourceState!=='ready')return {state:'source-unavailable'};
    const connection=await connect(),route='/plugins/dsh-connect-workbuddy/usage?region=cn&detail=fast';
    const usage=await send(connection,route),known=new Set((usage.accounts||[]).map(a=>a.id));
    const eligible=[...pool.entries.values()].filter(e=>e.enabled&&e.state==='ready');
    const fingerprint=e=>crypto.createHash('sha256').update(JSON.stringify(e.credential)).digest('hex');
    // An existing DSH id does not prove its credentials match Helper. Establish
    // the baseline by merging once after startup, then track credential changes.
    const changed=eligible.filter(e=>!known.has(e.id)||previous.get(e.id)!==fingerprint(e));
    if(changed.length){
      const records=changed.map(({credential:c})=>({access_token:c.accessToken,refresh_token:c.refreshToken,uid:c.uid,nickname:c.nickname,domain:c.domain,expiresAt:c.expiresAtMs,...c.enterpriseId?{enterpriseId:c.enterpriseId}:{}}));
      await send(connection,'/plugins/dsh-connect-workbuddy/transfer?region=cn&action=import',{text:JSON.stringify(records),indexes:records.map((_,i)=>i)});
      const verified=await send(connection,route);const ids=new Set((verified.accounts||[]).map(a=>a.id));
      if(changed.some(e=>!ids.has(e.id)))throw new Error('DSH account merge not confirmed');
    }
    for(const e of eligible)previous.set(e.id,fingerprint(e));
    for(const id of previous.keys())if(!pool.entries.has(id))previous.delete(id);
    return {state:changed.length?'updated':'current',imported:changed.length,eligible:eligible.length,at:new Date().toISOString()};
  };
}
