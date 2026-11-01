'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const avatars = ['witness','keeper','visitor','reader','watcher','wanderer'];
const avatarNames = ['听雨的人','守档人','晚归客','读信人','夜班人','远行客'];
const state = {profile:null, room:null, catalog:[], publicRooms:[], tab:'records', busy:false, offline:false, surface:true, lastReadChat:0, epoch:0, avatar:'witness', readingAt:{}, tokens:{}, seenAnswers:new Set()};
const drafts = {name:'',code:new URLSearchParams(location.search).get('room') || '',question:'',solution:'',chat:'',feedback:'',story:'',minutes:'25',attempts:'40',visibility:'private'};
let toastTimer, pollBusy = false, previousRoom = null;
const portrait = (p, cls='') => `<img class="${cls}" src="/avatars/${esc(p.avatar)}.svg" alt="${esc(avatarNames[avatars.indexOf(p.avatar)] || '预设头像')}">`;
const memberName = p => `<span class="member-head">${portrait(p)}<span>${esc(p.name)}</span></span>`;
function toast(text){ $('#room-toast').textContent=text; $('#room-toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#room-toast').classList.remove('visible'),4500); }
async function api(path,data){
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  const value=await response.json(); if(!response.ok) throw new Error(value.error || '这次操作未完成。'); return value;
}
function applyRoom(room){
  if(room && previousRoom!==room.id){ drafts.story=room.story_id; drafts.minutes=String(room.minutes); drafts.attempts=String(room.attempts); state.lastReadChat=0; previousRoom=room.id; }
  if(room?.session){
    const key='night-room-drafts-'+room.session.id+'-'+room.me;
    if(state.draftKey!==key){ state.draftKey=key; try{Object.assign(drafts,JSON.parse(localStorage.getItem(key)||'{}'));}catch(_){} }
  }
  state.room=room;
}
function render(fromPoll=false){
  const r0=state.room,s0=r0?.session;
  const readingStep=s0?.status==='opening'?Math.min(20,Math.floor((Date.now()-(state.readingAt[s0.id]||Date.now()))/900)):0;
  const voteStep=r0?.vote?.state==='open'?Math.ceil(r0.vote.expires_at-r0.server_time):0;
  const signature=JSON.stringify({room:r0,publicRooms:state.publicRooms,tab:state.tab,busy:state.busy,offline:state.offline,surface:state.surface,solutionOpen:state.solutionOpen,avatar:state.avatar,readingStep,voteStep},(key,value)=>['remaining_seconds','server_time'].includes(key)?undefined:value);
  if(fromPoll&&signature===state.renderedSignature){const clock=$('.clock');if(clock&&s0)clock.textContent=String(Math.floor(s0.remaining_seconds/60)).padStart(2,'0')+':'+String(Math.floor(s0.remaining_seconds%60)).padStart(2,'0');return;}
  state.renderedSignature=signature;
  const active=document.activeElement, focus=active?.id, selection=typeof active?.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
  const oldFeed=$('.feed'), scroll=oldFeed?{top:oldFeed.scrollTop,bottom:oldFeed.scrollHeight-oldFeed.scrollTop-oldFeed.clientHeight<50}:null;
  const r=state.room;
  const phase=r?r.id+'/'+(r.session?r.session.id:'waiting'):'lobby';
  const changedPhase=state.renderedPhase!==phase;state.renderedPhase=phase;
  $('#room-app').innerHTML=header()+ (state.offline?'<div class="connection" role="alert">连接暂时中断，草稿留在这里。恢复连接后会重新同步记录。</div>':'')+(r?(r.session?gameView(r):waiting(r)):lobby());
  if(changedPhase)window.scrollTo({top:0,behavior:'instant'});
  if(focus&&!changedPhase){const next=document.getElementById(focus);if(next && !next.disabled){next.focus({preventScroll:true});if(selection && next.setSelectionRange)next.setSelectionRange(...selection);}}
  const feed=$('.feed');if(feed){if(scroll&&!scroll.bottom)feed.scrollTop=scroll.top;else feed.scrollTop=feed.scrollHeight;}
}
function header(){
  const r=state.room, me=r?.members.find(m=>m.id===r.me)||state.profile;
  return `<header class="topbar"><img class="logo" src="/art/yetang-logo.png" alt="夜汤"><div class="top-right"><span class="tag">同席推理 · 流程体验</span>${me?memberName(me):''}${r?'<button class="link" data-action="leave">离开房间</button>':''}</div></header>`;
}
function lobby(){
  return `<section class="intro"><div class="eyebrow">门已打开，等一位同行的人</div><h1>今夜，和谁一起揭开真相？</h1><p>留一个名字，选一张面孔。<br>在同一份档案前，拼出各自遗漏的那一部分。</p></section>
  <div class="lobby-grid"><section class="panel"><h2>留下你的名字</h2><div class="profile-preview">${portrait({avatar:state.avatar})}<div><label for="name">游戏名字</label><input id="name" data-draft="name" maxlength="12" value="${esc(drafts.name)}" autocomplete="nickname" placeholder="今夜，如何称呼你"></div></div><label>选择一张面孔</label><div class="avatar-picker">${avatars.map((a,i)=>`<button class="avatar-choice ${state.avatar===a?'selected':''}" data-action="avatar" data-avatar="${a}" aria-label="${avatarNames[i]}" aria-pressed="${state.avatar===a}">${portrait({avatar:a})}</button>`).join('')}</div>
  <form id="create-form"><div class="form-row"><div><label for="visibility">谁能找到这一席</label><select id="visibility" data-draft="visibility"><option value="private" ${drafts.visibility==='private'?'selected':''}>仅凭房间码加入</option><option value="public" ${drafts.visibility==='public'?'selected':''}>在大厅公开等待</option></select></div><button ${state.busy?'disabled':''}>创建房间 →</button></div></form>
  <form class="join-form" id="join-form"><label for="code">已有房间码</label><div class="form-row"><input id="code" data-draft="code" value="${esc(drafts.code)}" maxlength="6" placeholder="输入六位房间码" autocomplete="off"><button class="quiet" ${state.busy?'disabled':''}>加入 →</button></div></form></section>
  <section class="panel"><div class="panel-title"><h2>等待中的房间</h2><button class="link" data-action="refresh">刷新</button></div><p class="dim">公开等待的席位会出现在这里。私密房间需要房间码。</p>${state.publicRooms.length?state.publicRooms.map(r=>`<div class="public-room"><div class="row">${portrait(r.host)}<div><p>${esc(r.host.name)} 的房间</p><small>${r.count} / ${r.capacity} 位入座</small></div></div><button class="quiet" data-action="join-public" data-code="${r.code}">入座</button></div>`).join(''):'<div class="empty">暂时还没有人在这里等候。<br>可以先开一席，邀请朋友进来。</div>'}</section></div>
  <p class="notice">本次体验组房与协作流程。示例提问会收到固定回答，自由提问尚未接入模型，不消耗已批准的模型额度。</p>`;
}
function seats(r){return `<div class="seats">${r.members.map(m=>`<div class="seat">${portrait(m)}<div><p>${esc(m.name)}${m.id===r.me?' <small>（我）</small>':''}</p><small>${m.host?'房主 · ':''}${m.online?'在席':'暂时离线'}</small><small class="ready-mark">${m.ready?'已准备':'尚未准备'}</small></div></div>`).join('')}${r.members.length<r.capacity?'<div class="seat empty-seat">空着的席位，等一位同行人</div>':''}</div>`;}
function heading(r,title){return `<div class="room-heading"><div><div class="eyebrow">${r.visibility==='private'?'凭码入座':'公开等待'} · ${r.members.length} / ${r.capacity} 人</div><h1>${esc(title)}</h1></div><div><small>房间码</small><div class="room-code">${r.code}</div><button class="link" data-action="copy">复制房间码</button></div></div>`;}
function waiting(r){
  const me=r.members.find(m=>m.id===r.me), card=state.catalog.find(c=>c.id===r.story_id);
  const configDirty=me.host&&(drafts.story!==r.story_id||Number(drafts.minutes)!==r.minutes||Number(drafts.attempts)!==r.attempts);
  return heading(r,'同一席，等你准备好')+`<div class="waiting-grid"><section class="panel"><h2>在席的人</h2>${seats(r)}<div class="actions"><button class="${me.ready?'quiet':''}" data-action="prepare" ${state.busy||configDirty?'disabled':''}>${me.ready?'取消准备':'我准备好了'}</button></div><p class="dim">全员准备后，由房主打开档案。读完汤面后再开始计时。</p>${discussion(r,false)}</section><section class="panel"><div class="eyebrow">这一局的档案</div><div class="story-card paper"><h3>${esc(card?.title||r.story_id)}</h3><p>${esc(card?.intro||'故事还没有展开。')}</p><span class="stamp">待阅</span></div>${me.host?`<form id="configure-form" class="host-config"><div><label for="story">选择档案</label><select id="story" data-draft="story">${state.catalog.map(c=>`<option value="${c.id}" ${drafts.story===c.id?'selected':''}>${esc(c.title)}</option>`).join('')}</select></div><div class="config-row"><div><label for="minutes">推理时长 · 分钟</label><input id="minutes" type="number" data-draft="minutes" min="1" max="60" value="${esc(drafts.minutes)}"></div><div><label for="attempts">共享机会 · 次</label><input id="attempts" type="number" data-draft="attempts" min="1" max="100" value="${esc(drafts.attempts)}"></div></div><button class="quiet" ${state.busy?'disabled':''}>保存配置</button></form>`:`<p>这一局共有 ${r.minutes} 分钟、${r.attempts} 次机会。<br><small>题目和规则由房主选择；每个人都能查看。</small></p>`}<div class="actions ${me.host?'host-start':''}">${me.host?`<button data-action="start" ${state.busy||configDirty||r.members.length<2||r.members.some(m=>!m.ready)?'disabled':''}>打开这份档案 →</button>`:'<span class="dim">准备好后，等待房主打开档案。</span>'}</div></section></div>`;
}
function discussion(r,inGame=true){
  if(state.tab==='chat'||!inGame)state.lastReadChat=r.chat.at(-1)?.id||0;
  return `<div class="${inGame?'':'waiting-chat'}"><div class="feed" aria-label="讨论记录">${r.chat.length?r.chat.map(m=>`<article class="message ${m.author.id===r.me?'player':''}"><div class="byline">${memberName(m.author)}</div><div class="bubble">${esc(m.text)}</div></article>`).join(''):'<p class="dim">把你的猜想留在这里。讨论不会提交给主持人，也不消耗机会。</p>'}</div><form class="composer" id="chat-form"><label for="chat">同席讨论 <span class="dim">Enter 发送 · Shift+Enter 换行</span></label><div class="form-row"><textarea id="chat" data-draft="chat" maxlength="500" placeholder="你注意到了什么？">${esc(drafts.chat)}</textarea><button class="quiet" ${state.busy?'disabled':''}>发送</button></div></form></div>`;
}
function votePanel(r){
  const v=r.vote;if(!v)return '';
  const yes=Object.values(v.ballots).filter(Boolean).length, own=v.ballots[r.me];
  if(v.state==='open'||v.state==='approved')return `<section class="vote-panel" aria-label="共同决定"><p>${v.kind==='hint'?'有人申请下一层提示':'有人申请提前揭晓'} · ${yes} / ${v.required} 票${v.state==='approved'?' · 等待当前提问完成':` · 剩余 ${Math.max(0,Math.ceil(v.expires_at-r.server_time))} 秒`}</p><small>${v.kind==='hint'?'按发起时在线人数，严格过半通过。':'提前揭晓需要发起时全体在线玩家同意。'}</small><div class="row">${own===undefined&&v.eligible.includes(r.me)?`<button data-action="vote-yes">同意</button><button class="quiet" data-action="vote-no">${v.kind==='hint'?'暂不需要':'继续推理'}</button>`:`<span class="dim">${own===true?'你已同意':'你已选择继续推理'}</span>`}</div></section>`;
  const labels={applied:v.kind==='hint'?'下一层提示已向所有人公开。':'全员已同意，正在揭晓。',expired:'投票时间已到，这次没有公开新内容。',rejected:'这次投票未通过，可以继续推理。',cancelled:'本局已经结束。'};
  return `<p class="dim">${labels[v.state]||''}</p>`;
}
function records(s){
  return `<div class="feed" aria-label="推理记录">${s.messages.length?s.messages.map(m=>{
    const fresh=m.kind==='answer'&&!state.seenAnswers.has(m.id);if(m.kind==='answer')state.seenAnswers.add(m.id);
    const author=m.author?memberName(m.author):(m.role==='host'?'主持人':'系统');
    const text=m.kind==='demo_notice'?'这条自由提交暂未判定，本次未扣次数。可以使用示例提问体验同步流程。':m.text;
    return `<article class="message ${esc(m.role)}"><div class="byline">${author}${m.kind==='solution'?' · 提交还原':''}</div><div class="bubble ${m.kind==='answer'?'answer':''} ${m.kind==='answer'&&m.text==='是'?'blood':''} ${fresh?'fresh':''}">${esc(text)}</div></article>`;
  }).join(''):'<div class="empty">档案已经打开。<br>从你最想确认的那个疑点开始。</div>'}</div>`;
}
function surfaceLines(s){
  const elapsed=Date.now()-state.readingAt[s.id];
  return s.surface.split(/\n/).map((line,i)=>`<p class="surface-line ${s.status==='opening'&&elapsed<i*900?'line-pending':''}">${esc(line)}</p>`).join('');
}
function gameView(r){
  const s=r.session, ended=['revealing','reviewing','completed','interrupted'].includes(s.status), me=r.members.find(m=>m.id===r.me);
  const minutes=String(Math.floor(s.remaining_seconds/60)).padStart(2,'0'), seconds=String(Math.floor(s.remaining_seconds%60)).padStart(2,'0');
  const active=['active','processing'].includes(s.status), unread=r.chat.filter(m=>m.id>state.lastReadChat&&m.author.id!==r.me).length;
  if(!state.readingAt[s.id])state.readingAt[s.id]=Date.now();
  const readWait=Math.max(0,Math.ceil((state.readingAt[s.id]+Math.max(2400,s.surface.split(/\n/).length*900)-Date.now())/1000));
  return heading(r,s.title)+`<div class="compact-members">${r.members.map(m=>`${memberName(m)}<small>${m.host?'房主':''}${s.status==='opening'?(m.read?' · 已读':' · 阅读中'):''}</small>`).join('')}</div><div class="game-grid"><aside class="surface-panel ${state.surface?'open':''}"><button class="mobile-summary" data-action="surface">${esc(s.title)} · 汤面与提示 <span>${state.surface?'收起 −':'展开 +'}</span></button><section class="surface paper"><div class="eyebrow">汤面 / 档案正文</div><h2>${esc(s.title)}</h2>${surfaceLines(s)}${s.hints.length?`<div class="hints"><h3>已公开的提示</h3>${s.hints.map((h,i)=>`<p><small>第${i+1}层</small><br>${esc(h)}</p>`).join('')}</div>`:''}<span class="stamp">${ended?'已揭晓':'查阅中'}</span></section></aside><div><div class="statusbar"><div><small>剩余时间${s.frozen?' · 暂停计时':''}</small><span class="clock">${minutes}:${seconds}</span></div><div><small>共享机会${r.reserved?' · '+r.reserved+'次在途':''}</small><span class="quota">${s.attempts_left}<small class="quota-total"> / ${s.attempts_total}</small></span></div><div class="status">${s.status==='opening'?'等全员读完后开始计时':s.status==='processing'?'主持正在判断，讨论可以继续':ended?'这一局的故事已经展开':'可以正式提问，或和同伴交换猜想'}</div></div>${r.notice?`<p class="notice">${esc(r.notice)}</p>`:''}${votePanel(r)}<section class="panel workspace">${s.status==='opening'?`<div class="reading"><h3>先读一遍，别急着开始</h3><p>全员读完后，${r.minutes}分钟的推理才开始。${me.read?'你已读完，等待同伴。':''}</p><button data-action="read" ${me.read||readWait||state.busy?'disabled':''}>${me.read?'已读完 · 等待同伴':readWait?'汤面正在展开……':'读完了，开始推理'}</button></div>`:''}<nav class="tabs"><button data-action="tab-records" class="${state.tab==='records'?'active':''}">推理记录</button><button data-action="tab-chat" class="${state.tab==='chat'?'active':''}">同席讨论${unread&&state.tab!=='chat'?`<span class="badge">${unread}</span>`:''}</button></nav>${state.tab==='chat'?discussion(r):records(s)+(r.queue.length?`<div class="queue-note">${r.queue.map((q,i)=>`${i+1}. ${esc(q.author.name)} 的${q.kind==='solution'?'还原':'提问'}正在排队`).join('<br>')}</div>`:'')+(!ended?`<form class="composer" id="question-form"><label for="question">正式提问 <span class="dim">一次确认一个是非问题</span></label><div class="form-row"><textarea id="question" data-draft="question" maxlength="3000" placeholder="写下你想确认的问题……" ${!active?'disabled':''}>${esc(drafts.question)}</textarea><button ${!active||state.busy?'disabled':''}>提问 →</button></div><div class="examples">${s.starters.slice(0,2).map((q,i)=>`<button type="button" data-action="example" data-index="${i}" ${!active?'disabled':''}>示例：${esc(typeof q==='string'?q:q.text||q.question||'')}</button>`).join('')}</div></form>`:'')}</section>${!ended?`<div class="bottom-actions"><button class="quiet" data-action="hint" ${!active||s.hint_count>=3||state.busy?'disabled':''}>申请提示 · 剩余 ${3-s.hint_count} 层</button><button class="link" data-action="solution">提交还原 →</button><button class="link" data-action="reveal" ${!active||state.busy?'disabled':''}>提前揭晓</button></div>${state.solutionOpen?`<form class="panel" id="solution-form"><h3>还原你推理出的经过</h3><p class="dim">讲清关键经过和原因即可。由全房共享这次提交，未通过会消耗一次机会。</p><textarea id="solution" data-draft="solution" maxlength="3000" placeholder="把线索连起来……">${esc(drafts.solution)}</textarea><div class="actions"><button ${!active||state.busy?'disabled':''}>提交这份还原</button><button type="button" class="quiet" data-action="solution">收起</button></div></form>`:''}`:''}${ended?ending(r):''}</div></div>`;
}
function ending(r){
 const s=r.session;return `<section class="panel reveal"><div class="eyebrow">${s.end_reason==='success'?'还原成立':s.end_reason==='manual'?'共同揭晓':'这一局已结束'}</div><h2>故事的另一面</h2>${(s.truth||[]).map(t=>`<p>${esc(t)}</p>`).join('')}${s.review?`<div class="review-box"><h3>这一局留下的线索</h3><p>${esc(s.review.summary)}</p>${(s.review.highlights||[]).map(h=>`<div class="highlight"><p>${typeof h==='string'?esc(h):esc(h.question)+' → '+esc(h.answer)+'<br><small>'+esc(h.note)+'</small>'}</p></div>`).join('')}<h3>把因果连起来</h3>${(s.review.causes||[]).map(c=>`<p>${esc(c)}</p>`).join('')}</div>`:'<p class="dim">正在整理本局记录……</p>'}<form class="feedback" id="feedback-form"><label for="feedback">有什么想留下的话？</label><textarea id="feedback" data-draft="feedback" maxlength="1000" placeholder="哪里有意思，哪里卡住了……">${esc(drafts.feedback)}</textarea><button class="quiet" ${state.busy?'disabled':''}>保存反馈</button></form>${r.members.find(m=>m.id===r.me).host?`<div class="actions"><button data-action="return" ${s.status!=='completed'||state.busy?'disabled':''}>回到房间，再选一份档案 →</button></div>`:'<p class="dim">等房主返回房间后，可以一起再开一局。</p>'}</section>`;
}
function token(kind,text){const key=kind+':'+text;return state.tokens[key]||(state.tokens[key]=crypto.randomUUID());}
async function perform(fn){if(state.busy)return;state.busy=true;state.epoch++;render();try{await fn();state.offline=false;}catch(error){toast(error.message);}finally{state.busy=false;render();}}
async function action(name,data={}){const value=await api('/api/rooms/'+state.room.id+'/'+name,data);if(value.room)applyRoom(value.room);return value;}
async function saveProfile(){state.profile=await api('/api/profile',{name:drafts.name,avatar:state.avatar});}
document.addEventListener('input',event=>{const key=event.target.dataset.draft;if(key){drafts[key]=event.target.value;if(state.draftKey&&['question','solution'].includes(key))try{localStorage.setItem(state.draftKey,JSON.stringify({question:drafts.question,solution:drafts.solution}));}catch(_){}}});
document.addEventListener('change',event=>{const key=event.target.dataset.draft;if(key)drafts[key]=event.target.value;});
document.addEventListener('keydown',event=>{if(event.target.matches('textarea')&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.target.id!=='solution'&&event.target.id!=='feedback'){event.preventDefault();event.target.form?.requestSubmit();}});
document.addEventListener('submit',event=>{event.preventDefault();const id=event.target.id;perform(async()=>{
 if(id==='create-form'){await saveProfile();applyRoom((await api('/api/rooms',{visibility:drafts.visibility})).room);}
 if(id==='join-form'){await saveProfile();applyRoom((await api('/api/rooms/join',{code:drafts.code})).room);}
 if(id==='configure-form'){await action('configure',{story_id:drafts.story,minutes:Number(drafts.minutes),attempts:Number(drafts.attempts)});toast('配置已保存，请大家重新准备。');}
 if(id==='chat-form'){const text=drafts.chat;await action('chat',{text,request_id:token('chat',text)});if(drafts.chat===text)drafts.chat='';delete state.tokens['chat:'+text];}
 if(id==='question-form'||id==='solution-form'){const kind=id==='question-form'?'question':'solution',text=drafts[kind];await action('submit',{kind,text,request_id:token(kind,text)});if(drafts[kind]===text)drafts[kind]='';delete state.tokens[kind+':'+text];if(kind==='solution')state.solutionOpen=false;if(state.draftKey)try{localStorage.setItem(state.draftKey,JSON.stringify({question:drafts.question,solution:drafts.solution}));}catch(_){} }
 if(id==='feedback-form'){await action('feedback',{text:drafts.feedback});drafts.feedback='';toast('反馈已保存。');}
});});
document.addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const what=button.dataset.action;
 if(what==='avatar'){state.avatar=button.dataset.avatar;render();return;}
 if(what.startsWith('tab-')){state.tab=what==='tab-chat'?'chat':'records';render();return;}
 if(what==='solution'){state.solutionOpen=!state.solutionOpen;render();return;}
 if(what==='surface'){state.surface=!state.surface;render();return;}
 if(what==='example'){const q=state.room.session.starters[Number(button.dataset.index)];drafts.question=typeof q==='string'?q:q.text||q.question||'';render();$('#question')?.focus();return;}
 if(what==='copy'){navigator.clipboard.writeText(state.room.code).then(()=>toast('房间码已复制，发给同行的人即可。')).catch(()=>toast('房间码：'+state.room.code));return;}
 perform(async()=>{
  if(what==='refresh')state.publicRooms=(await api('/api/rooms')).rooms;
  if(what==='join-public'){await saveProfile();applyRoom((await api('/api/rooms/join',{code:button.dataset.code})).room);}
  if(what==='prepare'){const me=state.room.members.find(m=>m.id===state.room.me);await action('prepare',{ready:!me.ready});}
  if(what==='start')await action('start');
  if(what==='read')await action('read');
  if(what==='hint'||what==='reveal')await action('vote',{kind:what});
  if(what==='vote-yes'||what==='vote-no')await action('vote',{kind:state.room.vote.kind,vote_id:state.room.vote.id,agree:what==='vote-yes'});
  if(what==='return'){await action('return');state.solutionOpen=false;drafts.question='';drafts.solution='';}
  if(what==='leave'&&confirm('确定离开这一席吗？其他玩家可以继续，房主离开后会转交房主。')){await action('leave');applyRoom(null);previousRoom=null;state.draftKey=null;state.publicRooms=(await api('/api/rooms')).rooms;}
 });
});
async function poll(){
 if(pollBusy||state.busy||!state.profile)return;pollBusy=true;const epoch=state.epoch;
 try{const value=state.room?await api('/api/rooms/'+state.room.id):await api('/api/room-current');if(epoch!==state.epoch)return;applyRoom(value.room);state.offline=false;if(!state.room)state.publicRooms=(await api('/api/rooms')).rooms;render(true);}
 catch(error){if(epoch===state.epoch){state.offline=true;render();}}finally{pollBusy=false;}
}
async function boot(){try{const [profile,catalog,current]=await Promise.all([api('/api/profile'),api('/api/stories'),api('/api/room-current')]);state.profile=profile;drafts.name=profile.name;state.avatar=profile.avatar;state.catalog=catalog.stories;applyRoom(current.room);state.publicRooms=(await api('/api/rooms')).rooms;render();setInterval(poll,1200);}catch(error){$('#room-app').textContent='暂时没能打开房间：'+error.message;}}
boot();
