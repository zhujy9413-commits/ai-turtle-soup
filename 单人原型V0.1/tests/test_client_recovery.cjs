const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(require('path').join(__dirname,'../public/app.js'),'utf8');
const submit=source.slice(source.indexOf('async function submit(kind,demo=null)'),source.indexOf('async function showOpening()'));
const resolve=source.slice(source.indexOf('async function reconcilePending()'),source.indexOf('async function poll()'));
function sandbox(mode){
 const s={session:{id:'test-session',status:'active'},sending:false,qDraft:'原问题',solutionDraft:'',pendingSubmit:null,nextFault:null,calls:[],toasts:[],
  crypto:{randomUUID:()=> 'request-12345678'},localStorage:{setItem(){}},readPending(){return s.pendingSubmit},pendingKey(){return 'test'},
  render(){},isLive(){return true},endpoint(action){return '/test/'+(action||'')},forgetPending(){s.pendingSubmit=null},saveDraft(){},
  updateSession(value){s.session=value},toast(value){s.toasts.push(value)},
  async api(url){s.calls.push(url);if(url.endsWith('resolve')){if(mode==='offline')throw Error('still offline');return {accepted:mode==='accepted',cancelled:mode!=='accepted',session:{id:'test-session',status:'active'}}}
   const error=Error('test failure');if(mode==='definite'){error.httpStatus=503;error.accepted=false;error.serverSession={id:'test-session',status:'paused'}}throw error}
 };vm.createContext(s);vm.runInContext(resolve+submit,s);return s;
}
test('definite pre-submit rejection releases pending even for HTTP 503',async()=>{const s=sandbox('definite');await s.submit('question');assert.equal(s.pendingSubmit,null);assert.equal(s.session.status,'paused');assert.equal(s.calls.length,1)});
test('unknown transport is reconciled as cancelled without replaying submit',async()=>{const s=sandbox('cancelled');await s.submit('question');assert.equal(s.pendingSubmit,null);assert.equal(s.qDraft,'原问题');assert.equal(s.calls.filter(x=>x.endsWith('submit')).length,1);assert.equal(s.calls.length,2)});
test('accepted lost response clears matching draft without duplicate request',async()=>{const s=sandbox('accepted');await s.submit('question');assert.equal(s.pendingSubmit,null);assert.equal(s.qDraft,'');assert.equal(s.calls.length,2)});
test('unreachable reconciliation keeps unknown request protected',async()=>{const s=sandbox('offline');await s.submit('question');assert.ok(s.pendingSubmit);s.qDraft='新问题';await s.submit('question');assert.equal(s.calls.length,2);assert.ok(s.toasts.some(x=>x.includes('上次提交尚在确认中')))});
