import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {createStreamCompat} from './stream-compat.mjs';
const event=(delta,finish=null)=>'data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:finish}]})+'\r\n\r\n';
async function read(text,options){const bytes=Buffer.from(text),parts=[];for(let n=0;n<bytes.length;n+=7)parts.push(bytes.subarray(n,n+7));let out='';for await(const chunk of Readable.from(parts).pipe(createStreamCompat(options)))out+=chunk;return out.split('\n\n').filter(Boolean).map(frame=>frame.slice(6)).filter(data=>data!=='[DONE]').map(JSON.parse);}
test('fragmented UTF-8 reasoning becomes one block; repeated role and empty content disappear',async()=>{
 const values=await read(event({role:'assistant',reasoning_content:'先看',content:''})+event({role:'assistant',reasoning_content:'一下',content:null})+event({role:'assistant',content:'你好'})+event({},'stop')+'data: [DONE]\n\n');
 const choices=values.flatMap(v=>v.choices);assert.equal(choices.filter(c=>c.delta.role).length,1);assert.deepEqual(choices.filter(c=>c.delta.reasoning_content).map(c=>c.delta.reasoning_content),['先看一下']);assert.equal(choices.find(c=>c.delta.content)?.delta.content,'你好');assert.equal(choices.at(-1).finish_reason,'stop');
});
test('tool calls, reasoning boundaries and usage remain intact',async()=>{
 const tool=[{index:0,id:'t',type:'function',function:{name:'read',arguments:'{}'}}];const values=await read(event({reasoning_content:'inspect'})+event({tool_calls:tool},'tool_calls')+'data: '+JSON.stringify({choices:[],usage:{total_tokens:8}})+'\n\n');
 assert.equal(values[0].choices[0].delta.reasoning_content,'inspect');assert.deepEqual(values[1].choices[0].delta.tool_calls,tool);assert.equal(values[2].usage.total_tokens,8);
});
test('upstream errors are forwarded instead of hidden',async()=>{
 const values=await read(event({reasoning_content:'pending'})+'data: {"error":{"message":"quota"}}\n\n');assert.equal(values[0].choices[0].delta.reasoning_content,'pending');assert.equal(values[1].error.message,'quota');
});
test('ZCode hides reasoning fields while retaining text, tools, finish and usage',async()=>{
 const text=event({role:'assistant',reasoning_content:'private',reasoning_details:[{text:'private'}],content:''})+event({reasoning:'private',thinking:'private'})+event({content:'answer',tool_calls:[{index:0,id:'t',function:{name:'read',arguments:'{}'}}]},'tool_calls')+'data: {"choices":[],"usage":{"total_tokens":9}}\n\n';
 const values=await read(text,{hideReasoning:true});assert.doesNotMatch(JSON.stringify(values),/private|reasoning_content|reasoning_details|thinking/);assert.equal(values.flatMap(v=>v.choices).find(c=>c.delta.content).delta.content,'answer');assert.equal(values.flatMap(v=>v.choices).at(-1).finish_reason,'tool_calls');assert.equal(values.at(-1).usage.total_tokens,9);
});
