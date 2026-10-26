const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createTracker}=require('../public/quota.js');
function storage(){const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v)};}
const game=(left,options={})=>({id:'game-a',status:'active',attempts_total:40,attempts_left:left,...options});
test('15 and 5 each notify once, with no 20 milestone',()=>{
 const t=createTracker(storage());assert.equal(t.observe(game(40)),null);
 assert.equal(t.observe(game(20)),null);assert.equal(t.observe(game(16)),null);
 assert.equal(t.observe(game(15)),15);assert.equal(t.observe(game(15)),null);
 assert.equal(t.observe(game(6)),null);assert.equal(t.observe(game(5)),5);
 assert.equal(t.observe(game(5)),null);
});
test('refresh and refunds cannot replay an announced milestone',()=>{
 const s=storage(),t=createTracker(s);t.observe(game(16));assert.equal(t.observe(game(15)),15);
 const restored=createTracker(s);assert.equal(restored.observe(game(15)),null);
 restored.observe(game(16));assert.equal(restored.observe(game(15)),null);
});
test('starting at 15 or first restoring a low game gives no immediate alert',()=>{
 const t=createTracker(storage());assert.equal(t.observe(game(15,{attempts_total:15})),null);
 assert.equal(t.observe(game(14,{attempts_total:15})),null);
 assert.equal(t.observe(game(5,{attempts_total:15})),5);
 const restored=createTracker(storage());assert.equal(restored.observe(game(4)),null);
});
test('reserved processing counts and uncharged failures do not notify',()=>{
 const t=createTracker(storage());t.observe(game(16));
 assert.equal(t.observe(game(15,{status:'processing'})),null);
 assert.equal(t.observe(game(16)),null);
 assert.equal(t.observe(game(15,{status:'processing'})),null);
 assert.equal(t.observe(game(15)),15);
 assert.equal(t.observe(game(5,{status:'completed'})),null);
});
test('skipped polling across both thresholds announces only the lower one',()=>{
 const t=createTracker(storage());t.observe(game(20));assert.equal(t.observe(game(4)),5);
 assert.equal(t.observe(game(4)),null);t.observe(game(16));assert.equal(t.observe(game(15)),null);
});
test('separate games track separately, and short games have no starting warning',()=>{
 const t=createTracker(storage());t.observe(game(16));t.observe(game(6,{id:'game-b',attempts_total:6}));
 assert.equal(t.observe(game(15)),15);assert.equal(t.observe(game(5,{id:'game-b',attempts_total:6})),5);
 assert.equal(t.observe(game(5,{id:'game-c',attempts_total:5})),null);
});
test('malformed or unavailable storage falls back to the current page memory',()=>{
 const t=createTracker({getItem(){return '{';},setItem(){throw Error('blocked');}});
 assert.equal(t.observe(game(16)),null);assert.equal(t.observe(game(15)),15);
 assert.equal(t.observe(game(15)),null);assert.equal(t.observe(game('5')),null);
});
