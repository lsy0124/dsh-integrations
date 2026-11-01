import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
const exec=promisify(execFile);

export async function refreshHelperAccount(id){
  const base='http://127.0.0.1:18080';
  const page=await fetch(base+'/',{signal:AbortSignal.timeout(5000)});
  if(!page.ok)throw new Error('Helper refresh unavailable');
  const token=/<meta name="app-token" content="([^"]+)">/.exec(await page.text())?.[1];
  if(!token)throw new Error('Helper local session unavailable');
  const response=await fetch(base+'/api/accounts/'+encodeURIComponent(id)+'/points',{method:'POST',headers:{'X-App-Token':token,Origin:base},signal:AbortSignal.timeout(45000)});
  if(!response.ok)throw new Error(response.status===401?'Helper login expired':'Helper refresh unavailable');
}

export async function readHelperAccounts(){
  try{
    const {stdout}=await exec('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',fileURLToPath(new URL('./Read-HelperAccounts.ps1',import.meta.url))],{windowsHide:true,timeout:15000,maxBuffer:4*1024*1024});
    const records=JSON.parse(stdout.replace(/^\uFEFF/,''));
    if(!Array.isArray(records))throw new Error();
    return records;
  }catch{throw new Error('Helper account store unavailable; existing local credentials were not changed');}
}
function expiry(value){const n=Number(value);return Number.isFinite(n)&&n>0?(n<1e11?n*1000:n):(Date.parse(value)||0);}
function identity(c){return crypto.createHash('sha256').update('workbuddy\0'+c.uid).digest('hex').slice(0,24);}
function authError(error){return /401|403|12153|offline user session|unauthor|credential.*reject|login|sign.in|token.*(invalid|expired)/i.test(String(error?.message||error));}

export class HelperAccountPool {
  constructor({read=readHelperAccounts,client,now=Date.now,syncMs=30000,checkMs=300000}){
    Object.assign(this,{read,client,now,syncMs,checkMs});this.entries=new Map();this.cursor=0;this.lastSync=0;this.sourceState='starting';this.pending=null;
  }
  async sync(force=false){
    if(this.pending)return this.pending;
    if(!force&&this.now()-this.lastSync<this.syncMs)return;
    this.pending=this._sync().finally(()=>{this.pending=null;});return this.pending;
  }
  async _sync(){
    let records;try{records=await this.read();}catch{this.sourceState='unavailable';throw new Error('Helper account store unavailable');}
    const next=new Map();
    for(const a of records){
      if(a.version==='intl'||/(^|\.)workbuddy\.ai$/i.test(a.domain||''))continue;
      if(!a.uid)continue;
      const credential={uid:String(a.uid),accessToken:String(a.accessToken||''),refreshToken:String(a.refreshToken||''),domain:a.domain||'www.codebuddy.cn',nickname:a.nickname||'',...(a.enterpriseId?{enterpriseId:String(a.enterpriseId)}:{}),expiresAtMs:expiry(a.expiresAt),source:'dsh',filePath:'helper-memory'};
      const id=identity(credential);if(next.has(id))continue;
      const fingerprint=crypto.createHash('sha256').update(credential.accessToken+'\0'+credential.refreshToken).digest('hex');
      let entry=this.entries.get(id);
      if(!entry||entry.fingerprint!==fingerprint)entry={id,fingerprint,credential,state:'unchecked',checkedAt:0,uses:entry?.uses||0,refresh:null};
      entry.helperId=a.id;entry.name=a.alias||a.nickname||id;entry.enabled=a.enabled===true;
      if(!entry.enabled)entry.state='disabled';
      else if(entry.state==='disabled')entry.state='unchecked';
      next.set(id,entry);
    }
    this.entries=next;this.lastSync=this.now();this.sourceState='ready';
    // Sequential checks avoid a burst against the same upstream gateway.
    for(const e of next.values())if(e.enabled&&(e.state==='unchecked'||(e.state!=='needs_login'&&this.now()-e.checkedAt>=this.checkMs)))await this.check(e);
  }
  async renew(e){
    if(e.refresh)return e.refresh;
    e.refresh=(async()=>{
      // Helper owns durable token refresh. Its API saves renewed credentials
      // through the encrypted store, avoiding two writers or plaintext copies.
      if(!this.client.refreshHelper)throw new Error('Helper reauthentication required');
      await this.client.refreshHelper(e.helperId);
      const record=(await this.read()).find(a=>a.id===e.helperId);
      if(!record?.accessToken)throw new Error('Helper reauthentication required');
      e.credential={...e.credential,accessToken:record.accessToken,refreshToken:record.refreshToken||'',expiresAtMs:expiry(record.expiresAt)};
      e.fingerprint=crypto.createHash('sha256').update(e.credential.accessToken+'\0'+e.credential.refreshToken).digest('hex');
    })().finally(()=>{e.refresh=null;});return e.refresh;
  }
  async check(e){
    try{
      let credits;
      try{credits=await this.client.fetchCredits(e.credential);}catch(error){if(!authError(error))throw error;await this.renew(e);credits=await this.client.fetchCredits(e.credential);}
      e.balance=credits.total;e.state=credits.total>0?'ready':'empty';e.checkedAt=this.now();e.coolUntil=0;
    }catch(error){e.state=authError(error)?'needs_login':'unavailable';e.checkedAt=this.now();e.coolUntil=this.now()+this.checkMs;}
  }
  async pick(excluded=[]){
    // Fail closed when the authoritative list cannot be read: do not keep
    // serving an account the user may have just disabled or removed in Helper.
    await this.sync();
    const entries=[...this.entries.values()],start=this.cursor;
    for(let n=0;n<entries.length;n++){
      const index=(start+n)%entries.length,e=entries[index];
      if(!e.enabled||excluded.includes(e.id)||e.state!=='ready'||(e.coolUntil||0)>this.now())continue;
      this.cursor=(index+1)%entries.length;
      if(e.credential.expiresAtMs&&e.credential.expiresAtMs<this.now()+60000){try{await this.renew(e);}catch{e.state='needs_login';continue;}}
      e.uses++;e.lastUsedAt=new Date(this.now()).toISOString();return e.credential;
    }
    return undefined;
  }
  async resolveAccount(id){
    await this.sync();
    const e=this.entries.get(id);
    if(!e||!e.enabled)throw new Error('Selected Helper account is absent or disabled');
    if(e.state!=='ready')throw new Error('Selected Helper account is unavailable; refresh or sign in through Helper');
    if(e.credential.expiresAtMs&&e.credential.expiresAtMs<this.now()+60000){try{await this.renew(e);}catch{e.state='needs_login';throw new Error('Selected Helper account requires login');}}
    e.uses++;e.lastUsedAt=new Date(this.now()).toISOString();return e.credential;
  }
  async resolvePreferredAccount(id){
    await this.sync();
    const entry=this.entries.get(id);
    // A removed/disabled account is an explicit user choice, not an outage.
    if(!entry||!entry.enabled)throw new Error('Selected Helper account is absent or disabled');
    if(entry.state==='ready'&&(entry.coolUntil||0)<=this.now()){
      try{return await this.resolveAccount(id);}catch{
        // resolveAccount may discover a refresh failure; pick re-reads Helper
        // and still fails closed if the authoritative store is unavailable.
      }
    }
    const credential=await this.pick([id]);
    if(!credential)throw new Error('No available Helper account; refresh or sign in through Helper');
    return credential;
  }
  async recheck(){
    await this.sync(true);
    for(const e of this.entries.values())if(e.enabled)await this.check(e);
    return this.status();
  }
  failure(id,{kind}){
    const e=this.entries.get(id);if(!e)return;
    if(['client','model_busy','policy_reject','not_found'].includes(kind))return;
    e.coolUntil=this.now()+(kind==='soft_rate'?60000:5000);
    if(kind==='session_dead'){e.state='needs_login';e.checkedAt=this.now();}
    else if(kind==='hard_credit'){e.state='empty';e.checkedAt=this.now();}
  }
  status(){return {source:'WorkBuddy Helper',mode:'round-robin',syncSeconds:this.syncMs/1000,sourceState:this.sourceState,lastSync:this.lastSync?new Date(this.lastSync).toISOString():null,accounts:[...this.entries.values()].map(e=>({id:e.id,name:e.name,enabled:e.enabled,state:e.state,balance:e.balance,uses:e.uses,lastUsedAt:e.lastUsedAt,checkedAt:e.checkedAt?new Date(e.checkedAt).toISOString():null}))};}
}
