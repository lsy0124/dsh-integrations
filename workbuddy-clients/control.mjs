import crypto from 'node:crypto';
import fs from 'node:fs/promises';

export function createControl({status,select,refresh,html}){
  const sessions=new Map();
  const security={'cache-control':'no-store','x-frame-options':'DENY','x-content-type-options':'nosniff','referrer-policy':'no-referrer'};
  return async function handle(req,res){
    if(!['/accounts','/control/status','/control/selection','/control/refresh'].includes(req.url))return false;
    const json=(code,body)=>{res.writeHead(code,{...security,'content-type':'application/json; charset=utf-8'});res.end(JSON.stringify(body));};
    if(req.method==='GET'&&req.url==='/accounts'){
      for(const [id,s] of sessions)if(s.expires<Date.now())sessions.delete(id);
      while(sessions.size>=32)sessions.delete(sessions.keys().next().value);
      const id=crypto.randomBytes(24).toString('hex'),csrf=crypto.randomBytes(24).toString('hex'),nonce=crypto.randomBytes(16).toString('hex');
      sessions.set(id,{csrf,expires:Date.now()+8*60*60*1000});
      const page=(html??await fs.readFile(new URL('./accounts.html',import.meta.url),'utf8')).replaceAll('__CSRF__',csrf).replaceAll('__NONCE__',nonce);
      res.writeHead(200,{...security,'content-type':'text/html; charset=utf-8','set-cookie':`wb_control=${id}; HttpOnly; SameSite=Strict; Path=/control; Max-Age=28800`,'content-security-policy':`default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`});res.end(page);return true;
    }
    const id=/(?:^|;\s*)wb_control=([a-f0-9]+)/.exec(req.headers.cookie||'')?.[1],session=sessions.get(id);
    if(!session||session.expires<Date.now()){json(401,{error:'请重新打开账号面板'});return true;}
    if(req.method==='GET'&&req.url==='/control/status'){json(200,status());return true;}
    if(req.method!=='POST'){json(405,{error:'不支持的操作'});return true;}
    if(req.headers['x-csrf-token']!==session.csrf||req.headers.origin!==`http://${req.headers.host}`){json(403,{error:'请从本机账号面板操作'});return true;}
    try{
      if(req.url==='/control/refresh'){await refresh();json(200,status());return true;}
      let raw='',bytes=0;for await(const chunk of req){bytes+=chunk.length;if(bytes>4096){json(413,{error:'请求过大'});return true;}raw+=chunk;}
      const input=JSON.parse(raw);if(input.accountId!==null&&(typeof input.accountId!=='string'||!/^[a-f0-9]{24}$/.test(input.accountId))){json(400,{error:'请选择有效账号'});return true;}
      await select(input.accountId);json(200,status());
    }catch{json(400,{error:'操作未完成。请刷新列表，确认账号已启用并且登录有效。'});}
    return true;
  };
}
