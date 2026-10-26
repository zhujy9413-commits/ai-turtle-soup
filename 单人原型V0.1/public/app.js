"use strict";
const $=s=>document.querySelector(s), esc=v=>String(v??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons={arrow:'<path d="M4 12h15m-6-6 6 6-6 6"/>',back:'<path d="m14 5-7 7 7 7M7 12h13"/>',close:'<path d="m5 4 14 15-2 1L4 7ZM18 4l2 2L7 20l-3-2Z"/><path d="M7 17v5m10-6v4"/><circle cx="20" cy="13" r=".65"/>',sound:'<path d="M10 5 5 9H2v6h3l5 4V5Zm4 3a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',mute:'<path d="M10 5 5 9H2v6h3l5 4V5Zm5 4 6 6m0-6-6 6"/>',book:'<path d="M12 5c-4-3-7-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-3-1-6-2-10 1Zm0 0v15"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',bowl:'<path d="M3 11h18c-1 8-4 10-9 10S4 19 3 11Zm5-8c-4 3 4 3 0 6m8-6c-4 3 4 3 0 6"/>',shuffle:'<path d="M3 5h3c5 0 7 14 12 14h3m-4-4 4 4-4 3M3 19h3c2 0 3-3 5-7m3-4c1-2 2-3 4-3h3m-4-3 4 3-4 4"/>',door:'<path d="M6 22V2h12v20M9 5h6v14H9m5-7h1M3 22h18"/>',bed:'<path d="M2 20V8m0 7h20v5m0-5V9H8v6M2 9h6M6 5h14v4"/>',key:'<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-3-3 3-3m-6 0 3-3"/>'};
icons.search='<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/>';
const icon=n=>`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[n]||icons.info}</svg>`;
let stories=[],selected=null,filter='全部档案',session=null,screen='home',modal=null,confirmPurpose='reveal',feedbackId=null;
let selectionTouched=false,stampedSelection=null,libraryQuery='';const seenAnswerIds=new Set();
const selectedSettings=new Map();
function configFor(story){return selectedSettings.get(story.id)||{minutes:story.minutes,attempts:story.attempts};}
function configFields(story,dialog=false){const c=configFor(story),prefix=dialog?'setting-':'';return `<div class="field-row"><label for="${prefix}minutes">推理时间<input id="${prefix}minutes" data-setting="minutes" type="number" min="1" max="60" value="${esc(c.minutes)}" inputmode="numeric"><span class="unit">分钟 · 最多60分钟</span></label><label for="${prefix}attempts">问答额度<input id="${prefix}attempts" data-setting="attempts" type="number" min="1" max="100" value="${esc(c.attempts)}" inputmode="numeric"><span class="unit">提问与未通过还原共用</span></label></div>`;}
function recordSetting(input){const story=stories.find(s=>s.id===selected);if(!story)return;const c={...configFor(story),[input.dataset.setting]:input.value};selectedSettings.set(story.id,c);document.querySelectorAll(`[data-setting="${input.dataset.setting}"]`).forEach(el=>{if(el!==input)el.value=input.value;});const summary=$('#selection-setting-summary');if(summary)summary.textContent=`${c.minutes}分钟 / ${c.attempts}次`;}
function testTools(){const busy=sending||session?.status!=='active';return `<p>下一次提交可模拟失败，不会调用模型。</p><div class="chips"><button class="chip" data-act="fault" data-fault="timeout" ${busy?'disabled':''}>模拟一次超时</button><button class="chip" data-act="fault" data-fault="bad_output" ${busy?'disabled':''}>模拟非法输出</button><button class="chip" data-act="preset" data-text="我没说清问实际还是假设，你能先确认范围吗？">范围澄清示例</button></div>`;}

const quotaTracker=window.NightQuota.createTracker({getItem:k=>localStorage.getItem(k),setItem:(k,v)=>localStorage.setItem(k,v)});
let quotaNotice=null,quotaTimer;
const quotaDefault='随时回看 · 查看期间继续计时';
function quotaHint(){const n=quotaNotice?.id===session?.id?quotaNotice:null;return `<span id="quota-reminder" role="status" aria-live="polite" aria-atomic="true" class="${n?'quota-reminder'+(n.threshold===5?' quota-urgent':''):''}">${n?esc(n.text):quotaDefault}</span>`;}
function observeQuota(s){
 if(quotaNotice&&(quotaNotice.id!==s.id||s.status!=='active'&&s.status!=='processing')){clearTimeout(quotaTimer);quotaNotice=null;}
 const threshold=quotaTracker.observe(s);if(threshold===null)return;
 clearTimeout(quotaTimer);quotaNotice={id:s.id,threshold,text:threshold===15?'还剩15次提问与还原机会。':'只剩5次机会，提交前再确认一下。'};
 const shown=quotaNotice;
 quotaTimer=setTimeout(()=>{if(quotaNotice!==shown)return;quotaNotice=null;const hint=$('#quota-reminder');if(hint){hint.className='';hint.textContent=quotaDefault;}},threshold===5?8500:6500);
}

let tutorialStep=0,dialogKey='',dialogReturnFocus=null,dialogScrollY=0;
let qDraft='',solutionDraft='',sending=false,nextFault=null,offline=false,surfaceOpen=true,signature='',syncAt=Date.now(),remainingAtSync=0,lastPoll=0,polling=false;
let musicMuted=new URLSearchParams(location.search).get('quiet')==='1'||(localStorage.getItem('soup_muted')??localStorage.getItem('soup_intro_muted'))!=='false',musicVolume=Number(localStorage.getItem('soup_volume')||.22),toastTimer,draftTimer;
const music=$('#music');
let musicPending=false,openingActive=false,musicVolumeFrame=0;
if(!Number.isFinite(musicVolume))musicVolume=.22;
musicVolume=Math.max(0,Math.min(1,musicVolume));music.volume=musicVolume;
function musicIsOn(){return !musicMuted&&!music.paused&&musicVolume>0;}
function syncMusicControl(){
 const b=$('#app [data-act="music"]');if(!b)return;
 const on=musicIsOn();b.setAttribute('aria-label',on?'静音背景音乐':'开启背景音乐');b.setAttribute('aria-pressed',String(on));b.innerHTML=icon(on?'sound':'mute');
}
function syncMusicVolume(milliseconds=0){
 cancelAnimationFrame(musicVolumeFrame);
 const target=musicVolume*(openingActive?.55:1),from=music.volume,start=performance.now();
 if(!milliseconds){music.volume=target;return;}
 function frame(now){const t=Math.max(0,Math.min(1,(now-start)/milliseconds));music.volume=Math.max(0,Math.min(1,from+(target-from)*t));if(t<1)musicVolumeFrame=requestAnimationFrame(frame);}
 musicVolumeFrame=requestAnimationFrame(frame);
}
async function playBackgroundMusic(notify=false){
 if(musicMuted||musicVolume<=0)return false;
 if(!music.paused){syncMusicVolume();syncMusicControl();return true;}
 music.volume=0;
 try{await music.play();if(musicMuted){music.pause();return false;}musicPending=false;syncMusicVolume(1000);syncMusicControl();return true;}
 catch{musicPending=true;syncMusicControl();if(notify)toast('音乐尚未播放，请再点一次声音按钮。');return false;}
}
function pauseBackgroundMusic(){cancelAnimationFrame(musicVolumeFrame);musicPending=false;music.pause();syncMusicControl();}
function changeMusicVolume(value){
 musicVolume=Math.max(0,Math.min(1,Number(value)||0));localStorage.setItem('soup_volume',String(musicVolume));syncMusicVolume();
 musicMuted=musicVolume===0;localStorage.setItem('soup_muted',String(musicMuted));
 if(musicMuted)pauseBackgroundMusic();else void playBackgroundMusic();
 syncMusicControl();
}
music.addEventListener('playing',syncMusicControl);music.addEventListener('pause',syncMusicControl);
// Restore a previously chosen sound preference with a real gesture if autoplay was blocked.
document.addEventListener('pointerdown',e=>{if(musicPending&&!musicMuted&&!$('#night-intro')&&!e.target.closest('[data-act="music"]'))void playBackgroundMusic();},{capture:true});
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3800);}
async function api(path,data){const c=new AbortController(),t=setTimeout(()=>c.abort(),12000);try{const r=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:c.signal});const j=await r.json();if(!r.ok)throw Error(j.error||'操作未完成，请重试。');return j;}finally{clearTimeout(t);}}
function endpoint(action){return `/api/sessions/${session.id}${action?'/'+action:''}`;}
const active=s=>s&&['opening','active','processing'].includes(s.status);
const ending=s=>s&&['revealing','reviewing','completed'].includes(s.status);
function remaining(){return Math.max(0,remainingAtSync-(session?.status==='active'?(Date.now()-syncAt)/1000:0));}
function clockText(value){const n=Math.max(0,Math.ceil(value));return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;}
function topbar(){return `<header class="topbar"><button class="brand" data-act="brand" aria-label="夜汤首页"><span class="brand-lockup"><img class="brand-logo" src="/art/yetang-logo.png?v=0.2" alt="夜汤" width="2172" height="724"><small>海龟汤 · 夜间档案</small></span></button><div class="top-actions"><span class="status-pill">体验版</span><button class="subtle-button" data-act="rules" aria-label="玩法规则">${icon('book')}<span class="desktop-label">玩法规则</span></button><div class="volume-wrap"><button class="subtle-button" data-act="music" aria-label="${musicIsOn()?'静音背景音乐':'开启背景音乐'}" aria-pressed="${musicIsOn()}">${icon(musicIsOn()?'sound':'mute')}</button><label for="volume">音乐音量</label><input id="volume" type="range" min="0" max="1" step=".02" value="${musicVolume}" aria-label="音乐音量"></div></div></header>`;}
function footer(){return `<footer class="footer"><span>一段情景。一个真相。由你把线索连起来。</span>${!session?'<button class="replay-intro" data-act="replay-intro">重看开场 ↗</button>':''}<span>夜汤 · 夜间档案</span></footer>`;}
function home(){
 const card=stories.find(s=>s.id===selected)||stories[0];if(!card)return '<p class="empty">档案暂未载入，请刷新重试。</p>';
 const visible=stories.filter(s=>(filter==='全部档案'||s.place===filter)&&(!libraryQuery||`${s.title}${s.intro}${s.place}`.includes(libraryQuery.trim())));
 return `<main class="shell home-page ${selectionTouched?'has-selection-dock':''}">${topbar()}${session&&active(session)?`<div class="resume"><div><strong>还有一份打开的档案</strong><p>${esc(session.title)} · 计时仍按本局规则继续</p></div><button data-act="continue">继续推理 ${icon('arrow')}</button></div>`:''}<section class="hero night-hero" aria-labelledby="night-title"><div class="hero-visual" aria-hidden="true"></div><div class="hero-content"><div class="eyebrow">夜汤 · 每一个细节，都有另一面</div><h1 id="night-title"><span class="title-preface">有些真相，</span><br><span class="title-main">藏在<em>灯照不到的地方。</em></span></h1><p class="desc">一段反常的情景，一场只靠提问的推理。<br>把那些不对劲的细节，慢慢拼成完整的故事。</p><div class="hero-entry"><button class="primary" data-act="browse">挑选一碗汤 ${icon('arrow')}</button><button class="subtle-button" data-act="tutorial">第一次玩 ${icon('book')}</button></div><div class="hero-note"><span>单人推理</span><span class="line"></span><span>三层提示</span><span class="line"></span><span>由你揭开真相</span></div></div><div class="hero-marginal" aria-hidden="true"><span>THE OTHER SIDE OF THE STORY</span><span>向下 · 打开档案 ↓</span></div></section><section id="library" class="night-library"><div class="library-head"><div class="section-title"><div><p class="library-kicker">夜汤 · 未结档案</p><h2>今夜，翻开哪一页。</h2><p class="library-caption">每一份平静的记录，都藏着一个说不通的地方。</p></div><small>${String(stories.length).padStart(2,'0')} 份档案待解</small></div><div class="library-controls"><label class="library-search">${icon('search')}<span class="sr-only">搜索档案</span><input id="library-search" type="search" placeholder="找一份档案…" value="${esc(libraryQuery)}" autocomplete="off"></label><div class="filters" aria-label="场景筛选">${['全部档案','旧屋','病房','合租'].map(v=>`<button class="filter ${filter===v?'active':''}" data-act="filter" data-value="${v}" aria-pressed="${filter===v}">${v}</button>`).join('')}</div></div></div><div class="library-layout"><div class="case-grid">${visible.length?visible.map(s=>`<button class="case-card ${selected===s.id?'selected':''}" data-act="select" data-id="${s.id}" data-place="${s.place}" aria-pressed="${selected===s.id}"><div class="case-art" aria-hidden="true"><span class="scene-image scene-${s.id}"></span><span class="stamp">ARCHIVE NO. ${s.number}</span><span class="number">${s.number}</span><span class="case-open">${selected===s.id?'已选中 · SELECTED':'打开档案 ↗'}</span></div>${selected===s.id?`<img class="case-select-mark ${stampedSelection===s.id?'is-stamping':''}" src="/art/selection-handprint.png" width="1024" height="1536" alt="" aria-hidden="true">`:''}<div class="case-copy"><div class="case-meta"><span>${esc(s.place)} · ${esc(s.fear)}</span><span>${s.played?'已体验':'未打开'}</span></div><h3>${esc(s.title)}</h3><p>${esc(s.intro)}</p><div class="case-bottom"><span>${esc(String(s.difficulty).replace(/候选$/,''))}</span><strong>${selected===s.id?'已选中':'查看档案'} ↗</strong></div></div></button>`).join(''):'<div class="empty">没有符合条件的档案。</div>'}</div><aside class="config" id="config"><div><span class="config-label">准备这一局 / READY TO BEGIN</span><h3>${esc(card.title)}</h3><p class="notice">内容提醒：${esc(card.notice)}</p>${card.played?'<p class="mini-note">这份档案已体验，可以再次进入。</p>':''}</div>${configFields(card)}<div class="config-buttons"><button class="primary" data-act="start" ${sending?'disabled':''}>${sending?'正在打开档案':'开始这一局'} ${icon('arrow')}</button><button class="random" data-act="random">${icon('shuffle')} 随机选择未体验的档案</button></div><p class="mini-note">完整汤面展示后开始计时。</p></aside></div><div class="prototype-note">${icon('info')}<span>当前体验版仅支持预设问题与还原示例，自由提问和自由还原暂未开放。</span></div></section>${footer()}${selectionTouched?`<div class="selection-dock" role="region" aria-label="已选档案与开始游戏"><div class="selection-dock-title"><span>已选档案</span><strong>${esc(card.title)}</strong><small id="selection-setting-summary">${esc(configFor(card).minutes)}分钟 / ${esc(configFor(card).attempts)}次</small></div><div class="selection-dock-actions"><button class="subtle-button setting-trigger" data-act="settings-open">本局设置</button><button class="primary" data-act="start" ${sending?'disabled':''}>${sending?'正在打开档案':'开始这一局'} ${icon('arrow')}</button></div></div>`:''}</main>`;
}
function message(m){const answerKey=`${session?.id}:${m.id}`;const yes=m.role==='host'&&m.kind==='answer'&&String(m.text).trim()==='是';const fresh=yes&&!seenAnswerIds.has(answerKey);if(yes)seenAnswerIds.add(answerKey);const label=m.role==='player'?'我的还原'+(m.kind==='question'?'问题':''):m.role==='host'?'主持人 · 模拟':'系统';return `<div class="message ${m.role} ${m.kind}${yes?' answer-yes':''}${fresh?' answer-reveal':''}" data-message-id="${m.id}"><div class="message-label"><span>${m.role==='player'?(m.kind==='question'?'我的提问':'我的还原'):label}</span>${m.scope==='hypothetical'?'<span class="scope-tag">假设讨论</span>':''}${m.kind==='hint'?`<span>第 ${m.level} 层提示</span>`:''}${m.role==='host'?`<button class="feedback-link" data-act="message-feedback" data-id="${m.id}" aria-label="反馈这条回答">反馈</button>`:''}</div><div class="message-body">${esc(m.text)}</div></div>`;}
function game(){const busy=sending||session.status!=='active';return `<main class="shell game-page">${topbar()}<div class="game-nav"><div class="game-title"><button class="back" data-act="interrupt-open">${icon('back')} 返回档案</button><small>THE TRUTH IS IN THE DETAILS</small><h1>${esc(session.title)}</h1></div><div class="game-stats"><div class="stat ${session.frozen?'frozen':''}" id="time-stat"><small id="clock-label">${session.frozen?'计时已暂停':'剩余时间'}</small><strong id="clock">${clockText(remaining())}</strong></div><div class="stat"><small>剩余次数${session.reserved?' · 预留1次':''}</small><strong>${session.attempts_left}<span class="unit"> / ${session.attempts_total}</span></strong></div></div><div class="game-tools"><button class="surface-trigger" data-act="surface" aria-haspopup="dialog">${icon('book')} 查看汤面</button>${quotaHint()}</div></div><div class="game-board"><aside class="case-sheet"><div class="story-paper"><div class="sheet-top"><span>故事记录</span><span>待解档案</span></div><details class="surface-details" id="surface-details" ${surfaceOpen?'open':''}><summary>汤面</summary><h2 class="paper-heading">${esc(session.title)}</h2><div class="surface">${esc(session.surface)}</div></details><div class="sheet-actions"><button class="subtle-button" data-act="rules">${icon('book')} 规则</button><button class="subtle-button" data-act="reveal-open" ${busy?'disabled':''}>查看汤底</button></div></div>${session.hints.length?`<div class="hint-log"><h3>已公开的提示</h3>${session.hints.map((h,i)=>`<p><b>0${i+1}</b><br>${esc(h)}</p>`).join('')}</div>`:''}</aside><section class="conversation" aria-label="推理记录"><div class="conversation-head"><span>推理记录 / NOTES</span><small>正式提问与还原分开提交</small></div><div class="messages" id="messages" aria-live="polite">${session.messages.length?session.messages.map(message).join(''):`<div class="chat-empty">${icon('bowl')}<h3>汤面已经交到你手里。</h3><p>从一个是非问题开始，慢慢接近事情的另一面。</p></div>`}</div>${session.frozen?'<div class="waiting"><span class="pulse"></span>主持人正在判断，计时已暂停</div>':''}<div class="composer"><div class="composer-top"><label for="question">正式提问</label><span>${nextFault?'下次提交：模拟异常':'一次一个是非问题'}</span></div><div class="input-row"><textarea id="question" rows="1" maxlength="3000" placeholder="选择下方示例，或输入预设问法…" ${busy?'disabled':''}>${esc(qDraft)}</textarea><button class="primary" data-act="question" ${busy?'disabled':''}>提问 ${icon('arrow')}</button></div><div class="chips" aria-label="示例问题">${session.starters.map(q=>`<button class="chip" data-act="preset" data-text="${esc(q)}" ${busy?'disabled':''}>${esc(q)}</button>`).join('')}</div><div class="chat-actions"><button class="secondary" data-act="hint" ${busy||session.hint_count>=3?'disabled':''}>获取提示 · 剩余 ${3-session.hint_count} 层</button><button class="secondary" data-act="solution-open" ${busy?'disabled':''}>提交还原 ${icon('arrow')}</button></div><div class="composer-note"><span>模拟主持 · 未支持、澄清和失败不扣次</span><button data-act="tools-open">模拟测试工具</button></div></div></section></div>${footer()}</main>`;}
function result(){const titles={success:'真相，终于连了起来。',manual:'答案，就藏在这些细节里。',timeout:'这一局，时间到了。',attempts:'这一局，次数用尽了。'};const reasons={success:'还原成立 · 模拟判定',manual:'主动揭晓',timeout:'时间耗尽',attempts:'次数耗尽'};return `<main class="shell">${topbar()}<div class="result"><header class="result-top"><div class="eyebrow">CASE ARCHIVE / ${esc(session.title)}</div><h1>${titles[session.end_reason]||'这一份档案，暂时合上。'}</h1><p>把看似不相干的细节，放回它们原来的位置。</p><span class="outcome-tag">${reasons[session.end_reason]||'收尾中'}</span></header><section class="result-panel truth"><h2>汤底 · 完整故事</h2>${(session.truth||[]).map(p=>`<p>${esc(p)}</p>`).join('')}</section><section class="result-panel"><h2>把线索连起来</h2>${session.review?`<ol class="cause-list">${session.review.causes.map(p=>`<li>${esc(p)}</li>`).join('')}</ol><p class="review-note">${esc(session.review.summary)}</p>${session.review.highlights.length?`<ul class="highlights">${session.review.highlights.map(h=>`<li>${esc(h.question)} → ${esc(h.answer)}${h.scope==='hypothetical'?'（假设讨论）':''}</li>`).join('')}</ul>`:''}<p class="review-note">${esc(session.review.note)}</p>`:'<div class="waiting"><span class="pulse"></span>正在整理本局记录，复盘完成后结束这一局。</div>'}</section><section class="result-panel"><details class="result-history"><summary>回看本局推理记录 · ${session.messages.filter(m=>m.role==='player').length} 次提交</summary><div class="messages">${session.messages.map(message).join('')||'<p class="review-note">这局尚未留下提问记录。</p>'}</div></details></section><section class="result-panel"><h2>留下一点感受</h2><p class="review-note">哪里卡住了，或者哪一刻让你起了鸡皮疙瘩？可以稍后再写。</p><div class="feedback-box"><textarea id="review-feedback" rows="2" maxlength="1000" placeholder="可选：体验反馈或需要修正的问题"></textarea><button class="secondary" data-act="review-feedback">保存反馈</button></div></section><div class="return-row"><button class="primary" data-act="home-completed" ${session.status!=='completed'?'disabled':''}>${session.status==='completed'?'收好档案，再来一局':'正在完成复盘…'} ${icon('arrow')}</button></div></div>${footer()}</main>`;}
function dialog(){if(!modal)return '';let title='',body='';
 if(modal==='tutorial'){
  const steps=[
   {title:'一碗汤的两面',text:'你先看到一段反常的情景，叫作“汤面”。背后的完整故事叫作“汤底”，需要靠你一点点推理出来。',sample:'汤面给你现象，推理补全经过。'},
   {title:'一次问清一个问题',text:'用是非问题验证猜想。主持人通常只回答“是、否、无关、不清楚”；一句里混入多个问题时，需要先拆开。',sample:'例如“这盏灯使用了电池吗？”是一个明确的是非问题。'},
   {title:'卡住了，可以取一层提示',text:'每道汤准备了三层提示。单人点击即可获取下一层，不扣提问次数；你也可以随时点击“查看汤面”回看原文。',sample:'系统处理时计时暂停，阅读汤面、规则和提示时计时继续。'},
   {title:'把线索连成完整故事',text:'有了完整解释，点击“提交还原”，把人物、经过和原因连起来。也可以提前查看汤底，确认后进入揭晓与复盘。',sample:'这版是模拟主持。先用局内示例体验；自由问法与自由还原还没有接入真实模型。'}
  ];const step=steps[tutorialStep];title=step.title;
  body=`<div class="tutorial-progress" aria-label="教学进度">${steps.map((_,i)=>`<span class="${i===tutorialStep?'current':''}" aria-hidden="true"></span>`).join('')}<b>第 ${tutorialStep+1} / ${steps.length} 步</b></div><p class="tutorial-copy">${step.text}</p><div class="tutorial-example">${step.sample}</div>${tutorialStep===1?'<div class="rule-four"><span>是</span><span>否</span><span>无关</span><span>不清楚</span></div>':''}<div class="dialog-actions tutorial-actions"><button class="subtle-button" data-act="tutorial-skip">跳过教学</button><div>${tutorialStep?'<button class="secondary" data-act="tutorial-prev">上一步</button>':''}<button class="primary" data-act="${tutorialStep===3?'tutorial-finish':'tutorial-next'}">${tutorialStep===3?'去选题':'下一步'} ${icon('arrow')}</button></div></div>`;
 }
 else if(modal==='rules'){title='玩法规则';body=`<p>通过是非提问，把汤面背后的完整故事还原出来。</p><div class="rule-four"><span>是</span><span>否</span><span>无关</span><span>不清楚</span></div><ol class="rule-list"><li><b>正式提问：</b>一次一个问题。需要澄清时不扣次数，重述时保留原有上下文。</li><li><b>提交还原：</b>在独立窗口讲清因果。未通过的有效还原消耗一次次数，完整还原后进入揭晓与复盘。</li><li><b>三层提示：</b>单人点击即可获得下一层，不扣问答次数。提示不会自动送出。</li><li><b>查看汤面：</b>局内顶部有固定入口，关闭弹窗后留在原来的对话位置。</li><li><b>计时：</b>完整汤面公开后开始；系统处理时暂停，阅读、改写、看规则、看汤面和切后台时继续。</li><li><b>提前揭晓：</b>二次确认后结束推理，仍进入复盘。</li></ol><div class="simulation-label">本版为模拟主持。示例之外的自由提问和自由还原暂不交给真实模型判断。</div>`;}
 else if(modal==='surface'){title='汤面 · '+session.title;body=`<div class="surface modal-surface">${esc(session.surface)}</div>${session.hints.length?`<div class="dialog-hints"><h3>已公开的提示</h3>${session.hints.map((h,i)=>`<p>${i+1}. ${esc(h)}</p>`).join('')}</div>`:''}<p class="surface-note">这里只展示汤面原文。查看期间继续计时，关闭后回到原来的推理位置。</p><div class="surface-reveal"><button class="subtle-button" data-act="reveal-open" ${sending||session.status!=='active'?'disabled':''}>提前查看汤底 ${icon('arrow')}</button></div>`;}
 else if(modal==='settings'){const story=stories.find(s=>s.id===selected);title='本局设置';body=`<p>${esc(story.title)}</p>${configFields(story,true)}<p>完整汤面公开后开始计时。</p><div class="dialog-actions"><button class="primary" data-act="close">保存设置 ${icon('arrow')}</button></div>`;}
 else if(modal==='tools'){title='模拟测试工具';body=testTools();}
 else if(modal==='solution'){title='还原整个故事';body=`<p>把你确认的事情连成因果。正式提交与普通提问分开；未通过的有效还原会消耗一次剩余次数。</p><label class="sr-only" for="solution">故事还原</label><textarea id="solution" rows="6" maxlength="3000" placeholder="事情原本是这样发生的…">${esc(solutionDraft)}</textarea><details class="solution-tools"><summary>模拟测试工具 · 检查判定分支</summary><p>下面的按钮会指定模拟结果，用于验收流程，不代表模型判断或真实推理成绩。</p><div class="chips"><button class="chip" data-act="demo-solution" data-outcome="complete">模拟：完整还原</button><button class="chip" data-act="demo-solution" data-outcome="incomplete">模拟：未完整</button><button class="chip" data-act="demo-solution" data-outcome="conflict">模拟：关键冲突</button></div></details><div class="dialog-actions"><button class="subtle-button" data-act="close">继续想一想</button><button class="primary" data-act="solution">提交还原 ${icon('arrow')}</button></div>`;}
 else if(modal==='confirm'){const leave=confirmPurpose==='interrupt';title=leave?'先合上这份档案？':'现在查看汤底？';body=`<p>${leave?'退出会将这一局记为中断，保留已有记录。刷新或暂时切后台可以回来继续，无需退出。':'确认后会结束这一局的推理，公开完整故事，并进入复盘。这次会记录为“主动揭晓”。'}</p>${!leave?'<details class="solution-tools"><summary>模拟测试工具</summary><label><input id="review-fault" type="checkbox"> 模拟复盘失败，检查固定讲解降级</label></details>':''}<div class="dialog-actions"><button class="subtle-button" data-act="close">${leave?'继续这一局':'继续推理'}</button><button class="primary danger" data-act="confirm">${leave?'确认退出':'确认揭晓'}</button></div>`;}
 else if(modal==='feedback'){title='反馈这条回答';body=`<p>反馈会保留原始问答，便于定位问题。</p><textarea id="message-feedback" rows="4" maxlength="1000" placeholder="这条回答哪里不对，或哪里让你困惑？"></textarea><div class="dialog-actions"><button class="subtle-button" data-act="close">取消</button><button class="primary" data-act="save-feedback">保存反馈</button></div>`;}
 return `<dialog class="overlay" id="dialog" data-kind="${modal}" aria-labelledby="dialog-title"><div class="dialog-paper"><header class="dialog-head"><h2 id="dialog-title">${title}</h2><button class="icon-button blood-close" data-act="close" aria-label="关闭弹层">${icon('close')}</button></header><div class="dialog-body">${body}</div></div></dialog>`;
}
function focusSelector(el){if(!el)return null;if(el.id)return '#'+CSS.escape(el.id);if(el.dataset?.act)return `button[data-act="${CSS.escape(el.dataset.act)}"]${el.dataset.id?`[data-id="${CSS.escape(el.dataset.id)}"]`:''}`;return null;}
function syncDialog(){
 const key=modal?`${modal}:${modal==='tutorial'?tutorialStep:''}`:'';if(key===dialogKey)return;dialogKey=key;
 let root=$('#modal-root');if(!root){root=document.createElement('div');root.id='modal-root';document.body.appendChild(root);}
 root.innerHTML=dialog();document.documentElement.classList.toggle('modal-open',Boolean(modal));
 if(modal){const d=$('#dialog');d.showModal();d.addEventListener('cancel',e=>{e.preventDefault();closeDialog();});}
}
function openDialog(type){if(!modal){dialogReturnFocus=focusSelector(document.activeElement);dialogScrollY=window.scrollY;}modal=type;if(type==='tutorial')tutorialStep=0;syncDialog();window.scrollTo(0,dialogScrollY);}
function closeDialog(){modal=null;syncDialog();const el=dialogReturnFocus&&$(dialogReturnFocus);el?.focus({preventScroll:true});window.scrollTo(0,dialogScrollY);}
document.addEventListener('keydown',e=>{
 if(e.key!=='Tab')return;const d=$('#dialog');if(!d?.open)return;
 const controls=[...d.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')].filter(el=>el.getClientRects().length),first=controls[0],last=controls.at(-1),active=document.activeElement;
 if(!first)return;
 if(e.shiftKey&&(active===first||!d.contains(active))){e.preventDefault();last.focus();}
 else if(!e.shiftKey&&(active===last||!d.contains(active))){e.preventDefault();first.focus();}
});
document.addEventListener('animationend',e=>{if(e.animationName==='archiveStamp'&&e.target.matches('.case-select-mark')){stampedSelection=null;e.target.classList.remove('is-stamping');}});
function finishTutorial(){closeDialog();requestAnimationFrame(()=>$('#library')?.scrollIntoView({behavior:'smooth',block:'start'}));}
function render(){const focus=document.activeElement?.id,start=document.activeElement?.selectionStart,end=document.activeElement?.selectionEnd,feed=$('#messages'),oldScroll=feed?.scrollTop||0,atBottom=!feed||feed.scrollHeight-feed.scrollTop-feed.clientHeight<80,viewportY=window.scrollY;
 const dialogHasFocus=Boolean(document.activeElement?.closest('#modal-root'));
 $('#app').innerHTML=`${offline?'<div class="offline">连接暂时中断，现场已保留；正在尝试恢复。</div>':''}${screen==='home'?home():ending(session)?result():game()}`;
 const sd=$('#surface-details');if(sd)sd.addEventListener('toggle',()=>{surfaceOpen=sd.open;});
 const f=!dialogHasFocus&&focus&&document.getElementById(focus);if(f&&['TEXTAREA','INPUT'].includes(f.tagName)&&!f.disabled){f.focus({preventScroll:true});if(typeof start==='number'&&f.type!=='number')try{f.setSelectionRange(start,end);}catch{}}
 if($('#messages'))$('#messages').scrollTo({top:atBottom?$('#messages').scrollHeight:oldScroll,behavior:'instant'});
 syncDialog();window.scrollTo(0,viewportY);tick();}
function updateSession(s,force=false){if(!s)return;const beganEnding=Boolean(session)&&!ending(session)&&ending(s);session=s;observeQuota(s);if(beganEnding){dialogScrollY=0;dialogReturnFocus=null;window.scrollTo(0,0);}remainingAtSync=s.remaining_seconds;syncAt=Date.now();const sig=[s.id,s.status,s.messages.length,s.attempts_left,s.hint_count,Boolean(s.review),offline].join(':');if(force||sig!==signature){signature=sig;render();}}
function tick(){const t=$('#clock');if(!t||!session)return;t.textContent=clockText(remaining());$('#time-stat')?.classList.toggle('low-time',remaining()<60&&!session.frozen);}
function restoreDraft(s){for(const m of s.messages||[])seenAnswerIds.add(`${s.id}:${m.id}`);let saved=null;try{saved=JSON.parse(localStorage.getItem(`soup_draft_${s.id}`));}catch{}qDraft=saved?.question??s.question_draft;solutionDraft=saved?.solution??s.solution_draft;if(s.pending_kind==='question')qDraft='';if(s.pending_kind==='solution')solutionDraft='';}
function saveDraft(){clearTimeout(draftTimer);if(!session)return;const id=session.id,question=qDraft,solution=solutionDraft;localStorage.setItem(`soup_draft_${id}`,JSON.stringify({question,solution}));draftTimer=setTimeout(()=>api(`/api/sessions/${id}/draft`,{question_draft:question,solution_draft:solution}).catch(()=>{}),350);}
async function poll(){if(!session||screen==='home'||polling)return;const delay=['processing','revealing','reviewing'].includes(session.status)?650:3000;if(Date.now()-lastPoll<delay)return;lastPoll=Date.now();polling=true;try{const {session:s}=await api(endpoint());const wasOffline=offline;offline=false;updateSession(s,wasOffline);}catch{if(!offline){offline=true;render();}}finally{polling=false;}}
async function start(){if(sending)return;const c=configFor(stories.find(s=>s.id===selected)),minutes=Number(c.minutes),attempts=Number(c.attempts);sending=true;try{const data=await api('/api/sessions',{story_id:selected,minutes,attempts});session=data.session;qDraft='';solutionDraft='';surfaceOpen=true;screen='game';signature='';modal=null;updateSession(session,true);window.scrollTo(0,0);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const ready=await api(endpoint('ready'),{});updateSession(ready.session,true);}catch(e){toast(e.message);}finally{sending=false;render();}}
async function submit(kind,demo=null){if(!session||session.status!=='active'||sending)return;let text=kind==='question'?qDraft:solutionDraft;if(kind==='solution'&&demo&&!text)text=`（模拟测试）${{complete:'完整还原',incomplete:'未完整还原',conflict:'关键因果冲突'}[demo]}`;if(kind!=='hint'&&!text.trim()){toast('请先填写内容，或选择一个模拟测试分支。');return;}sending=true;render();try{const data=await api(endpoint('submit'),{kind,text,request_id:crypto.randomUUID(),demo_outcome:demo,fault:nextFault});nextFault=null;if(kind==='question')qDraft='';if(kind==='solution'){solutionDraft='';modal=null;}saveDraft();updateSession(data.session,true);}catch(e){toast(e.message);}finally{sending=false;render();}}
async function showOpening(){
 if(session||!window.NightIntro)return;
 const returnToReplay=document.activeElement?.dataset.act==='replay-intro';
 const wasPlaying=!music.paused;pauseBackgroundMusic();openingActive=true;
 let madeChoice=false;
 const openingResult=await window.NightIntro.open({volume:musicVolume,muted:musicMuted,
  onVolume:v=>changeMusicVolume(v),
  onPause:()=>pauseBackgroundMusic(),onResume:()=>{if(!musicMuted)void playBackgroundMusic();},
  onMute:v=>{
   madeChoice=true;musicMuted=v;localStorage.setItem('soup_intro_muted',String(v));localStorage.setItem('soup_muted',String(v));
   if(v)pauseBackgroundMusic();else{if(musicVolume===0){musicVolume=.22;localStorage.setItem('soup_volume',String(musicVolume));}void playBackgroundMusic();}
  }});
 openingActive=false;if(openingResult.reason==='leave')return;syncMusicVolume(900);
 if(!document.hidden&&music.paused&&!musicMuted&&(madeChoice||wasPlaying))void playBackgroundMusic();
 render();if(returnToReplay)$('[data-act="replay-intro"]')?.focus({preventScroll:true});
}
async function setMusic(){
 musicMuted=musicIsOn();localStorage.setItem('soup_muted',String(musicMuted));
 if(musicMuted)pauseBackgroundMusic();else{if(musicVolume===0){musicVolume=.22;localStorage.setItem('soup_volume',String(musicVolume));const slider=$('#volume');if(slider)slider.value=String(musicVolume);}await playBackgroundMusic(true);}
 syncMusicControl();
}
document.addEventListener('input',e=>{if(e.target.matches('[data-setting]')){recordSetting(e.target);}else if(e.target.id==='library-search'){libraryQuery=e.target.value;render();}else if(e.target.id==='question'){qDraft=e.target.value;saveDraft();}else if(e.target.id==='solution'){solutionDraft=e.target.value;saveDraft();}else if(e.target.id==='volume'){changeMusicVolume(e.target.value);}});
document.addEventListener('keydown',e=>{if(e.target.id==='question'&&e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();submit('question');}});
document.addEventListener('click',async e=>{const b=e.target.closest('button[data-act]');if(!b||b.disabled)return;const act=b.dataset.act;
 try{
  if(act==='replay-intro')await showOpening();
  else if(act==='rules')openDialog('rules');
  else if(act==='tutorial')openDialog('tutorial');
  else if(act==='tutorial-next'){tutorialStep=Math.min(3,tutorialStep+1);syncDialog();}
  else if(act==='tutorial-prev'){tutorialStep=Math.max(0,tutorialStep-1);syncDialog();}
  else if(act==='tutorial-skip'||act==='tutorial-finish')finishTutorial();
  else if(act==='surface')openDialog('surface');
  else if(act==='settings-open')openDialog('settings');
  else if(act==='tools-open')openDialog('tools');
  else if(act==='close')closeDialog();
  else if(act==='music')await setMusic();
  else if(act==='brand'){if(active(session)&&screen!=='home'){confirmPurpose='interrupt';openDialog('confirm');}else{screen='home';modal=null;render();}}
  else if(act==='browse')$('#library').scrollIntoView({behavior:'smooth',block:'start'});
  else if(act==='filter'){filter=b.dataset.value;render();}
  else if(act==='select'){if(selected!==b.dataset.id||!selectionTouched)stampedSelection=b.dataset.id;selected=b.dataset.id;selectionTouched=true;render();$(`#app [data-act="select"][data-id="${selected}"]`)?.focus({preventScroll:true});}
  else if(act==='random'){const candidates=stories.filter(s=>!s.played);if(!candidates.length){toast('这些档案都已体验过。可以明确选择一份档案再次开始。');return;}selected=candidates[Math.floor(Math.random()*candidates.length)].id;filter='全部档案';libraryQuery='';selectionTouched=true;stampedSelection=selected;render();toast('已随机选中，确认规则后即可开始。');}
  else if(act==='start')await start();
  else if(act==='continue'){screen='game';const {session:s}=await api(endpoint());restoreDraft(s);updateSession(s,true);}
  else if(act==='preset'){if(modal==='tools')closeDialog();qDraft=b.dataset.text;saveDraft();const q=$('#question');if(q){q.value=qDraft;q.focus({preventScroll:true});}}
  else if(act==='question')await submit('question');
  else if(act==='hint')await submit('hint');
  else if(act==='solution-open')openDialog('solution');
  else if(act==='solution')await submit('solution');
  else if(act==='demo-solution')await submit('solution',b.dataset.outcome);
  else if(act==='fault'){nextFault=b.dataset.fault;closeDialog();toast('下一次提交将模拟系统异常，不会调用模型。');render();}
  else if(act==='reveal-open'){confirmPurpose='reveal';openDialog('confirm');}
  else if(act==='interrupt-open'){confirmPurpose='interrupt';openDialog('confirm');}
  else if(act==='confirm'){if(confirmPurpose==='interrupt'){await api(endpoint('interrupt'),{});session=null;screen='home';modal=null;render();}else{const {session:s}=await api(endpoint('reveal'),{confirmed:true,review_fault:Boolean($('#review-fault')?.checked)});modal=null;updateSession(s,true);}}
  else if(act==='home-completed'){localStorage.setItem('soup_closed_session',session.id);const data=await api('/api/stories');stories=data.stories;screen='home';session=null;modal=null;signature='';render();window.scrollTo(0,0);}
  else if(act==='message-feedback'){feedbackId=b.dataset.id;openDialog('feedback');}
  else if(act==='save-feedback'){await api(endpoint('feedback'),{text:$('#message-feedback').value,message_id:feedbackId});modal=null;render();toast('已保存在本机，谢谢你的反馈。');}
  else if(act==='review-feedback'){await api(endpoint('feedback'),{text:$('#review-feedback').value});$('#review-feedback').value='';toast('反馈已保存在本机，可以继续下一局。');}
 }catch(err){toast(err.message||'这次操作未完成，请重试。');}
});
async function boot(){try{const [library,current]=await Promise.all([api('/api/stories'),api('/api/current')]);stories=library.stories;selected=stories[0]?.id;const s=current.session;if(s&&(s.status!=='completed'||localStorage.getItem('soup_closed_session')!==s.id)){session=s;screen='game';restoreDraft(s);updateSession(s,true);if(s.status==='opening'){await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));updateSession((await api(endpoint('ready'),{})).session,true);}}else{render();if(window.NightIntro&&!window.NightIntro.hasSeen())await showOpening();}if(!document.hidden&&music.paused&&!musicMuted&&!$('#night-intro'))void playBackgroundMusic();}catch(e){$('#app').innerHTML='<div class="boot"><span class="boot-mark">汤</span><p>档案暂未载入</p><small>请确认本地服务仍在运行，然后刷新。</small></div>';toast(e.message);}}
setInterval(()=>{tick();poll();},350);document.addEventListener('visibilitychange',()=>{if(!document.hidden){lastPoll=0;poll();}});boot();
