import {readHelperAccounts} from './helper-pool.mjs';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
process.env.DSH_HOME=path.join(process.env.APPDATA,'dsh-desktop','harness');
const {WorkBuddyUpstreamClient}=await import(pathToFileURL(path.join(process.env.DSH_HOME,'profiles/web/node_modules/dsh-connect-workbuddy/lib/index.js')));
const client=new WorkBuddyUpstreamClient();
for(const a of await readHelperAccounts()){
  try{const result=await client.fetchCredits({...a,expiresAtMs:Number(a.expiresAt)*1000,source:'dsh',filePath:'helper-memory'});console.log(JSON.stringify({name:a.nickname,ok:true,balance:result.total}));}
  catch(error){const message=String(error.message);console.log(JSON.stringify({name:a.nickname,ok:false,errorType:error.name,codes:message.match(/\b\d{3,5}\b/g),sessionDead:/12153|offline user session/i.test(message),auth:/401|unauthor/i.test(message)}));}
}
