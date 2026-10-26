"use strict";
(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.NightQuota=api;
})(typeof window==='undefined'?globalThis:window,function(){
 const milestones=[15,5];
 function createTracker(storage){
  const memory=new Map();
  function read(key){
   if(memory.has(key))return memory.get(key);
   try{
    const saved=JSON.parse(storage?.getItem(key)||'null');
    if(saved&&Number.isInteger(saved.previous)&&saved.previous>=0&&Array.isArray(saved.notified)){
     const state={previous:saved.previous,notified:saved.notified.filter(n=>milestones.includes(n))};
     memory.set(key,state);return state;
    }
   }catch{}
   return null;
  }
  function save(key,state){memory.set(key,state);try{storage?.setItem(key,JSON.stringify(state));}catch{}}
  return {observe(session){
   if(!session?.id||session.status!=='active')return null;
   const left=session.attempts_left,total=session.attempts_total;
   if(!Number.isInteger(left)||!Number.isInteger(total)||left<0||left>total)return null;
   const key=`night_quota_v1_${session.id}`,state=read(key);
   // A newly opened or first-restored game establishes a baseline, without a stale alert.
   if(!state){save(key,{previous:left,notified:milestones.filter(n=>n>=left)});return null;}
   const crossed=milestones.filter(n=>total>n&&state.previous>n&&left<=n&&!state.notified.includes(n));
   save(key,{previous:left,notified:[...new Set([...state.notified,...crossed])]});
   return crossed.length?Math.min(...crossed):null;
  }};
 }
 return {createTracker};
});
