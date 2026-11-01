import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const managed=id=>/^workbuddy-account-[a-f0-9]{24}$/.test(id);
const providerId=a=>'workbuddy-account-'+a.id;
function validate(accounts,base){
  const u=new URL(base);if(u.protocol!=='http:'||u.hostname!=='127.0.0.1'||u.pathname!=='/'||u.search||u.hash||u.username||u.password)throw new Error('Local bridge origin required');
  if(!Array.isArray(accounts)||accounts.length>200||accounts.some(a=>!/^[a-f0-9]{24}$/.test(a.id)||typeof a.name!=='string')||new Set(accounts.map(a=>a.id)).size!==accounts.length)throw new Error('Invalid account list');
}
export function mergeZcode(config,accounts,base){
  validate(accounts,base);const z=structuredClone(config);
  if(z.schemaVersion!==1||!Array.isArray(z.config?.providerConfigRules?.providerRules)||!Array.isArray(z.config.providerOrder))throw new Error('Unsupported ZCode schema');
  const rules=z.config.providerConfigRules.providerRules,template=rules.find(r=>r.providerId==='workbuddy-local');
  if(!template||![base+'/v1',base+'/zcode/v1'].includes(template.config.api.baseUrl))throw new Error('Matching ZCode WorkBuddy provider not found');
  template.config.api.baseUrl=base+'/zcode/v1';
  const wanted=new Set(accounts.map(providerId));
  z.config.providerConfigRules.providerRules=rules.filter(r=>!managed(r.providerId)||wanted.has(r.providerId));
  for(const a of accounts){
    const existing=z.config.providerConfigRules.providerRules.find(r=>r.providerId===providerId(a));
    if(existing){if([base+'/accounts/'+a.id+'/v1',base+'/zcode/accounts/'+a.id+'/v1'].includes(existing.config?.api?.baseUrl))existing.config.api.baseUrl=base+'/zcode/accounts/'+a.id+'/v1';continue;}
    const r=structuredClone(template);r.providerId=providerId(a);r.providerName='WorkBuddy · '+a.name;r.config.api.baseUrl=base+'/zcode/accounts/'+a.id+'/v1';z.config.providerConfigRules.providerRules.push(r);
  }
  z.config.providerOrder=z.config.providerOrder.filter(id=>!managed(id)||wanted.has(id));
  for(const id of wanted)if(!z.config.providerOrder.includes(id))z.config.providerOrder.push(id);
  return z;
}
export function mergeMiMo(config,accounts,base){
  validate(accounts,base);const m=structuredClone(config),template=m.provider?.['workbuddy-local'];
  if(!template||template.options.baseURL!==base+'/v1')throw new Error('Matching MiMo WorkBuddy provider not found');
  const wanted=new Set(accounts.map(providerId));
  for(const id of Object.keys(m.provider))if(managed(id)&&!wanted.has(id))delete m.provider[id];
  for(const a of accounts){
    if(m.provider[providerId(a)])continue;
    const r=structuredClone(template);r.name='WorkBuddy · '+a.name;r.options.baseURL=base+'/accounts/'+a.id+'/v1';m.provider[providerId(a)]=r;
  }
  return m;
}
export async function syncFile(file,merge,accounts,base){
  const before=await fs.readFile(file,'utf8'),parsed=JSON.parse(before),after=merge(parsed,accounts,base);
  if(JSON.stringify(parsed)===JSON.stringify(after))return {state:'current',changed:false};
  const backups=path.join(path.dirname(file),'local-backups');await fs.mkdir(backups,{recursive:true});
  await fs.writeFile(path.join(backups,path.basename(file)+'.selectors-'+Date.now()+'.json'),before,{mode:0o600,flag:'wx'});
  const temp=file+'.workbuddy-'+crypto.randomBytes(8).toString('hex')+'.tmp';
  try{
    await fs.writeFile(temp,JSON.stringify(after,null,2),{mode:0o600,flag:'wx'});
    if(await fs.readFile(file,'utf8')!==before)throw new Error('Client configuration changed concurrently; retry later');
    await fs.rename(temp,file);
  }catch(error){await fs.unlink(temp).catch(()=>{});throw error;}
  return {state:'updated',changed:true};
}
export async function synchronizeSelectors({accounts,base,userRoot=process.env.USERPROFILE}){
  const jobs=[['zcode',path.join(userRoot,'.zcode/v2/provider_config.json'),mergeZcode],['mimo',path.join(userRoot,'.config/mimocode/mimocode.jsonc'),mergeMiMo]];
  const results=await Promise.allSettled(jobs.map(([,file,merge])=>syncFile(file,merge,accounts,base)));
  return Object.fromEntries(results.map((r,i)=>[jobs[i][0],r.status==='fulfilled'?r.value:{state:r.reason?.code==='ENOENT'?'not-installed':'retry-needed',changed:false}]));
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const client=JSON.parse(await fs.readFile(new URL('./runtime/client.json',import.meta.url),'utf8')),base=`http://127.0.0.1:${client.port}`;
  const response=await fetch(base+'/v1/accounts',{headers:{authorization:'Bearer '+client.clientKey},signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw new Error('Account bridge is unavailable');
  const {accounts,sourceState}=await response.json();if(sourceState!=='ready')throw new Error('Helper list unavailable; client files were not changed');
  const result=await synchronizeSelectors({accounts,base});console.log(JSON.stringify({accounts:accounts.length,...result}));
  if(Object.values(result).some(r=>r.state==='retry-needed'))process.exitCode=1;
}
