import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {mergeZcode,mergeMiMo,syncFile,synchronizeSelectors} from './configure-selectors.mjs';
const base='http://127.0.0.1:18181',a={id:'a'.repeat(24),name:'first'},b={id:'b'.repeat(24),name:'new-account'};
const z=()=>({schemaVersion:1,config:{providerOrder:['other','workbuddy-local'],providerConfigRules:{providerRules:[{providerId:'other',config:{keep:true}},{providerId:'workbuddy-local',providerName:'default',config:{api:{baseUrl:base+'/v1'},access:{apiKey:'fixture-only'},personalModelIds:['hy3']}}]}}});
const m=()=>({provider:{other:{keep:true},'workbuddy-local':{name:'default',models:{hy3:{name:'Hy3'}},options:{baseURL:base+'/v1',apiKey:'fixture-only'}}}});
test('adding another Helper account updates both client menus without replacing existing choices',()=>{
 const firstZ=mergeZcode(z(),[a],base),firstM=mergeMiMo(m(),[a],base);
 firstZ.config.providerConfigRules.providerRules.at(-1).providerName='custom label';firstM.provider['workbuddy-account-'+a.id].name='custom label';
 const secondZ=mergeZcode(firstZ,[a,b],base),secondM=mergeMiMo(firstM,[a,b],base);
 assert.equal(secondZ.config.providerConfigRules.providerRules.at(-1).config.api.baseUrl,base+'/zcode/accounts/'+b.id+'/v1');
 assert.equal(secondM.provider['workbuddy-account-'+b.id].options.baseURL,base+'/accounts/'+b.id+'/v1');
 assert.equal(secondZ.config.providerConfigRules.providerRules[2].providerName,'custom label');assert.equal(secondM.provider['workbuddy-account-'+a.id].name,'custom label');
 assert.deepEqual(secondZ.config.providerConfigRules.providerRules[0],z().config.providerConfigRules.providerRules[0]);assert.deepEqual(secondM.provider.other,m().provider.other);
});
test('repeated synchronization is idempotent, removal affects only managed account providers',()=>{
 for(const [merge,config] of [[mergeZcode,z()],[mergeMiMo,m()]]){const first=merge(config,[a,b],base);assert.deepEqual(merge(first,[a,b],base),first);assert.deepEqual(merge(first,[],base),merge(config,[],base));}
});
test('invalid account lists or remote bridge origins never generate client routes',()=>{
 assert.throws(()=>mergeMiMo(m(),[a,a],base));assert.throws(()=>mergeZcode(z(),[{id:'../../x',name:'bad'}],base));assert.throws(()=>mergeMiMo(m(),[a],'http://example.com'));
});
test('unchanged polling does not rewrite files or create repeated backups',async t=>{
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'wb-selectors-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));const file=path.join(dir,'config.json');await fs.writeFile(file,JSON.stringify(z()));
 assert.equal((await syncFile(file,mergeZcode,[a],base)).changed,true);const saved=await fs.readFile(file,'utf8');assert.equal((await syncFile(file,mergeZcode,[a],base)).changed,false);assert.equal(await fs.readFile(file,'utf8'),saved);assert.equal((await fs.readdir(path.join(dir,'local-backups'))).length,1);
});
test('one missing client does not prevent updating the other client',async t=>{
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'wb-selectors-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));await fs.mkdir(path.join(dir,'.config/mimocode'),{recursive:true});await fs.writeFile(path.join(dir,'.config/mimocode/mimocode.jsonc'),JSON.stringify(m()));const r=await synchronizeSelectors({accounts:[a,b],base,userRoot:dir});assert.equal(r.zcode.state,'not-installed');assert.equal(r.mimo.state,'updated');
});
