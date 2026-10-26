"use strict";
// 开场：声音选择 → 故事（可跳过）→ 钥匙 → 开锁与淡入。开场不创建对局。
window.NightIntro=(()=>{
 const seenKey='night_intro_seen_v2',duration=8.2,lineTimes=[.25,1.8,3.8];
 let current=null;
 const hasSeen=()=>{try{return sessionStorage.getItem(seenKey)==='1';}catch{return false;}};
 const markSeen=()=>{try{sessionStorage.setItem(seenKey,'1');}catch{}};
 function open({volume=.22,muted=true,onVolume=()=>{},onMute=()=>{},onPause=()=>{},onResume=()=>{}}={}){
  if(current)return current;
  current=new Promise(resolve=>{
   const dialog=document.createElement('dialog'),abort=new AbortController(),audio=new Audio(),unlockAudio=new Audio();
   const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
   const returnFocus=document.activeElement;
   let phase='gate',silent=Boolean(muted),elapsed=0,startedAt=0,raf=0,closed=false,fadeTimer=0,finishTimer=0;
   audio.preload=unlockAudio.preload='none';audio.volume=unlockAudio.volume=Math.max(0,Math.min(1,volume));
   dialog.id='night-intro';dialog.className='night-intro';dialog.dataset.stage='gate';dialog.setAttribute('aria-labelledby','intro-heading');
   dialog.innerHTML=`<div class="intro-scene" aria-hidden="true"></div><div class="intro-traces" aria-hidden="true"><span class="trace-handprint"></span><span class="trace-scratches"></span></div><div class="intro-frame"><header class="intro-top"><img src="/art/yetang-logo.png?v=0.2" width="2172" height="724" alt="夜汤"></header><section class="intro-gate"><h1 id="intro-heading">留一点安静，<br>听一段故事。</h1><div class="intro-choices"><button class="primary" data-intro="sound">开启声音体验</button><button class="secondary" data-intro="silent">静音进入</button></div></section><section class="intro-story" hidden aria-label="开场故事" tabindex="-1"><div class="intro-quote" id="intro-quote"><p data-line="0" aria-hidden="true">世界上最后一个人</p><p data-line="1" aria-hidden="true">坐在房间中，</p><p data-line="2" aria-hidden="true">突然响起了<span>敲门声……</span></p></div><div class="intro-after"><p class="intro-status" role="status" aria-live="polite"></p><button class="primary" data-intro="resume" hidden>继续开场</button></div><div class="intro-key-slot" hidden><button type="button" class="intro-key-button" data-intro="unlock" aria-label="点击旧钥匙开门，进入夜汤"><span class="key-arrival"><span class="key-idle"><img class="key-base" src="/art/old-key.png" width="1536" height="1024" alt="" draggable="false"><img class="key-sheen" src="/art/old-key.png" width="1536" height="1024" alt="" draggable="false" aria-hidden="true"></span></span><span class="intro-key-hint" aria-hidden="true">轻触钥匙</span></button><p class="unlock-status sr-only" role="status" aria-live="polite"></p></div></section><footer class="intro-bottom"><button class="intro-audio" data-intro="mute" aria-pressed="${silent}">${silent?'声音：关闭':'声音：开启'}</button><label>音量 <input class="intro-volume" aria-label="开场音量" type="range" min="0" max="1" step=".02" value="${audio.volume}"></label><span>一段情景。一个未解的真相。</span></footer></div>`;
   audio.id='intro-audio';unlockAudio.id='intro-unlock-audio';
   for(const a of [audio,unlockAudio]){a.setAttribute('aria-hidden','true');dialog.appendChild(a);}
   const find=s=>dialog.querySelector(s),status=find('.intro-status');
   function stage(value){phase=value;dialog.dataset.stage=value;}
   function syncSound(){const b=find('[data-intro="mute"]');b.textContent=silent?'声音：关闭':'声音：开启';b.setAttribute('aria-pressed',String(silent));audio.muted=unlockAudio.muted=silent;onMute(silent);}
   function stop(a){a.pause();a.onloadedmetadata=null;a.removeAttribute('src');a.load();}
   function playAudio(){
    if(silent||phase!=='playing')return;
    if(!audio.getAttribute('src')){
     audio.onloadedmetadata=()=>{if(!closed&&phase==='playing')audio.currentTime=elapsed;};
     audio.src='/intro-knocks.wav?v=0.6.3';
    }else if(audio.readyState>0)audio.currentTime=elapsed;
    const attempt=audio.play();
    if(attempt)attempt.then(()=>{if(closed||phase!=='playing'||document.hidden)audio.pause();}).catch(()=>{
     if(closed||phase!=='playing')return;
     silent=true;syncSound();status.textContent='声音未能播放，已继续静音开场。';
    });
   }
   function finish(reason){
    if(closed)return;closed=true;cancelAnimationFrame(raf);clearTimeout(fadeTimer);clearTimeout(finishTimer);abort.abort();stop(audio);stop(unlockAudio);
    if(reason==='key')markSeen();
    dialog.close();dialog.remove();document.documentElement.classList.remove('intro-open');
    returnFocus?.isConnected&&returnFocus.focus({preventScroll:true});
    current=null;resolve({reason,muted:silent,volume:audio.volume});
   }
   function showKey(){
    if(closed||phase==='unlocking'||phase==='key')return;
    cancelAnimationFrame(raf);stop(audio);showLines(duration);stage('key');
    find('.intro-gate').hidden=true;find('.intro-story').hidden=false;find('.intro-key-slot').hidden=false;
    find('[data-intro="resume"]').hidden=true;status.textContent='';
    dialog.classList.add('intro-key-ready');dialog.setAttribute('aria-labelledby','intro-quote');
    find('.intro-story').focus({preventScroll:true});
   }
   function unlock(){
    if(closed||phase!=='key')return;
    stage('unlocking');const key=find('[data-intro="unlock"]');key.disabled=true;key.setAttribute('aria-busy','true');find('.unlock-status').textContent='正在开门';
    dialog.classList.add('intro-unlocking');find('[data-intro="mute"]').disabled=true;find('.intro-volume').disabled=true;
    if(!silent){
     onResume();unlockAudio.src='/unlock.wav?v=0.8.2';
     const attempt=unlockAudio.play();
     if(attempt)attempt.then(()=>{if(closed||document.hidden)unlockAudio.pause();}).catch(()=>{/* 音效不可用时，保持视觉开门可用。 */});
    }
    fadeTimer=setTimeout(()=>{if(!closed)dialog.classList.add('intro-leaving');},reduced?0:420);
    finishTimer=setTimeout(()=>finish('key'),reduced&&silent?180:1450);
   }
   function showLines(t){dialog.querySelectorAll('[data-line]').forEach((p,i)=>{
    const shown=reduced||t>=lineTimes[i];p.classList.toggle('is-shown',shown);p.setAttribute('aria-hidden',String(!shown));
   });}
   function frame(now){
    if(closed||phase!=='playing')return;
    elapsed=Math.min(duration,(now-startedAt)/1000);showLines(elapsed);
    if(elapsed>=duration){showKey();return;}
    raf=requestAnimationFrame(frame);
   }
   function begin(useSound){
    if(phase!=='gate')return;
    if(useSound&&audio.volume===0){audio.volume=unlockAudio.volume=.22;find('.intro-volume').value='.22';onVolume(.22);}
    silent=!useSound;syncSound();stage('playing');elapsed=0;startedAt=performance.now();
    dialog.classList.add('intro-started');find('.intro-gate').hidden=true;find('.intro-story').hidden=false;
    find('.intro-story').focus({preventScroll:true});playAudio();raf=requestAnimationFrame(frame);
   }
   dialog.addEventListener('cancel',e=>{e.preventDefault();if(phase!=='unlocking'){showKey();find('.intro-story').focus({preventScroll:true});}},{signal:abort.signal});
   dialog.addEventListener('click',e=>{
    const b=e.target.closest('[data-intro]');if(!b||b.disabled)return;
    const action=b.dataset.intro;
    if(action==='unlock')unlock();
    else if(action==='sound')begin(true);
    else if(action==='silent')begin(false);
    else if(action==='mute'&&phase!=='unlocking'){silent=!silent;if(!silent&&audio.volume===0){audio.volume=unlockAudio.volume=.22;find('.intro-volume').value='.22';onVolume(.22);}syncSound();if(!silent)playAudio();}
    else if(action==='resume'&&phase==='paused'){
     stage('playing');startedAt=performance.now()-elapsed*1000;b.hidden=true;status.textContent='';
     playAudio();onResume();raf=requestAnimationFrame(frame);find('.intro-story').focus({preventScroll:true});
    }
   },{signal:abort.signal});
   find('.intro-volume').addEventListener('input',e=>{if(phase==='unlocking')return;audio.volume=unlockAudio.volume=Number(e.target.value);onVolume(audio.volume);silent=audio.volume===0;syncSound();if(!silent)playAudio();},{signal:abort.signal});
   document.addEventListener('visibilitychange',()=>{
    if(!document.hidden)return;
    if(phase==='playing'){
     elapsed=Math.min(duration,(performance.now()-startedAt)/1000);stage('paused');cancelAnimationFrame(raf);audio.pause();onPause();
     status.textContent='开场已暂停';find('[data-intro="resume"]').hidden=false;
    }else if(phase==='key'||phase==='unlocking'){unlockAudio.pause();onPause();}
   },{signal:abort.signal});
   window.addEventListener('pagehide',()=>finish('leave'),{signal:abort.signal});
   document.body.appendChild(dialog);document.documentElement.classList.add('intro-open');dialog.showModal();
   find('[data-intro="sound"]').focus({preventScroll:true});
  });
  return current;
 }
 return {open,hasSeen};
})();
