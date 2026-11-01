import {Transform} from 'node:stream';
import {StringDecoder} from 'node:string_decoder';

// Some OpenAI-compatible clients close/reopen thinking on empty content or a
// repeated role delta. Emit each contiguous reasoning segment once instead.
export function createStreamCompat({hideReasoning=false}={}){
  const decoder=new StringDecoder('utf8'),thinking=new Map(),roles=new Set();
  let pending='',heldBytes=0;
  const encode=value=>'data: '+JSON.stringify(value)+'\n\n';
  const flush=(out,index)=>{
    const entry=thinking.get(index);if(!entry)return;
    out.push(encode({...entry.meta,choices:[{index,delta:entry.delta,finish_reason:null}]}));
    heldBytes-=entry.bytes;thinking.delete(index);
  };
  const frame=(out,raw)=>{
    const data=raw.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
    if(!data){out.push(raw+'\n\n');return;}
    if(data==='[DONE]'){for(const index of [...thinking.keys()])flush(out,index);out.push('data: [DONE]\n\n');return;}
    let value;try{value=JSON.parse(data);}catch{for(const index of [...thinking.keys()])flush(out,index);out.push(raw+'\n\n');return;}
    if(!Array.isArray(value.choices)||value.error){for(const index of [...thinking.keys()])flush(out,index);out.push(raw+'\n\n');return;}
    const {choices,...meta}=value,kept=[];
    for(const choice of choices){
      if(!choice.delta||typeof choice.delta!=='object'){flush(out,choice.index??0);kept.push(choice);continue;}
      const index=choice.index??0,delta={...choice.delta};
      if(hideReasoning)for(const key of ['reasoning_content','reasoning','reasoning_text','reasoning_details','thinking'])delete delta[key];
      if(delta.role){if(roles.has(index))delete delta.role;else roles.add(index);}
      if(delta.content===''||delta.content===null)delete delta.content;
      for(const key of ['reasoning_content','reasoning','reasoning_text']){
        if(typeof delta[key]!=='string')continue;
        const text=delta[key];delete delta[key];if(!text)continue;
        let entry=thinking.get(index);if(!entry){entry={meta,delta:{},bytes:0};thinking.set(index,entry);}
        entry.delta[key]=(entry.delta[key]||'')+text;const bytes=Buffer.byteLength(text);entry.bytes+=bytes;heldBytes+=bytes;
        if(heldBytes>8*1024*1024)throw new Error('Reasoning stream exceeds compatibility buffer');
      }
      // Role-only starts may pass without breaking the pending thinking block.
      if(Object.keys(delta).some(key=>key!=='role')||choice.finish_reason!=null)flush(out,index);
      if(Object.keys(delta).length||choice.finish_reason!=null)kept.push({...choice,delta});
    }
    if(kept.length||choices.length===0)out.push(encode({...meta,choices:kept}));
  };
  const drain=(out,final=false)=>{let match;while((match=/\r?\n\r?\n/.exec(pending))){const raw=pending.slice(0,match.index);pending=pending.slice(match.index+match[0].length);frame(out,raw);}if(pending.length>16*1024*1024)throw new Error('SSE frame too large');if(final){if(pending.trim())frame(out,pending);for(const index of [...thinking.keys()])flush(out,index);}};
  return new Transform({transform(chunk,encoding,callback){try{pending+=decoder.write(chunk);drain(this);callback();}catch(error){callback(error);}},flush(callback){try{pending+=decoder.end();drain(this,true);callback();}catch(error){callback(error);}}});
}
