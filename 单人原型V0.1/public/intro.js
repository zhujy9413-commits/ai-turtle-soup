"use strict";
// 独立开场只管理展示与音效，不创建、开始或暂停任何对局。
window.NightIntro=(()=>{
 const seenKey='night_intro_seen_v1',duration=8.2,lineTimes=[.25,1.8,3.8];
 let current=null;
 const hasSeen=()=>{try{return sessionStorage.getItem(seenKey)==='1';}catch{return false;}};
 const markSeen=()=>{try{sessionStorage.setItem(seenKey,'1');}catch{}};
 function open({volume=.22,muted=true,onVolume=()=>{},onMute=()=>{}}={}){
  if(current)return current;
  current=new Promise(resolve=>{
   const dialog=document.createElement('dialog'),abort=new AbortController(),audio=new Audio();
   const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
   const returnFocus=document.activeElement;
   let phase='gate',silent=Boolean(muted),elapsed=0,startedAt=0,raf=0,closed=false;
   audio.preload='none';audio.volume=Math.max(0,Math.min(1,volume));
   dialog.id='night-intro';dialog.className='night-intro';dialog.setAttribute('aria-labelledby','intro-heading');
   dialog.innerHTML=`<div class="intro-scene" aria-hidden="true"></div><div class="intro-frame"><header class="intro-top"><img src="/art/yetang-logo.png?v=0.2" width="2172" height="724" alt="夜汤"><button class="intro-skip" data-intro="skip">跳过开场 →</button></header><section class="intro-gate"><div class="intro-kicker">夜汤 · 开场</div><h1 id="intro-heading">留一点安静，<br>听一段故事。</h1><p>约 8 秒，包含轻微环境声与敲门声。<br>可以静音体验，也可以随时跳过。</p><div class="intro-choices"><button class="primary" data-intro="sound">开启声音体验</button><button class="secondary" data-intro="silent">静音进入</button></div></section><section class="intro-story" hidden aria-label="开场故事"><div class="intro-quote"><p data-line="0" aria-hidden="true">世界上最后一个人</p><p data-line="1" aria-hidden="true">坐在房间中，</p><p data-line="2" aria-hidden="true">突然响起了<span>敲门声……</span></p></div><div class="intro-after"><p class="intro-status" role="status" aria-live="polite"></p><button class="primary" data-intro="resume" hidden>继续开场</button><button class="primary" data-intro="enter" hidden>进入夜汤 →</button></div></section><footer class="intro-bottom"><button class="intro-audio" data-intro="mute" aria-pressed="${silent}">${silent?'开场声音：关闭':'开场声音：开启'}</button><label>音量 <input class="intro-volume" aria-label="开场音量" type="range" min="0" max="1" step=".02" value="${audio.volume}"></label><span>一段情景。一个未解的真相。</span></footer></div>`;
   audio.id='intro-audio';audio.setAttribute('aria-hidden','true');dialog.appendChild(audio);
   const find=s=>dialog.querySelector(s),status=find('.intro-status');
   function syncSound(){const b=find('[data-intro="mute"]');b.textContent=silent?'开场声音：关闭':'开场声音：开启';b.setAttribute('aria-pressed',String(silent));audio.muted=silent;onMute(silent);}
   function stopAudio(){audio.pause();audio.removeAttribute('src');audio.load();}
   function playAudio(){
    if(silent||phase!=='playing')return;
    if(!audio.getAttribute('src')){
     audio.onloadedmetadata=()=>{if(!closed&&phase==='playing')audio.currentTime=elapsed;};
     audio.src='/intro-knocks.wav?v=0.6.1';
    }else if(audio.readyState>0)audio.currentTime=elapsed;
    // 用户明确点击开启/继续/取消静音后才请求播放；后台和结束后不重启。
    const attempt=audio.play();
    if(attempt)attempt.then(()=>{if(closed||phase!=='playing'||document.hidden)audio.pause();}).catch(()=>{
     if(closed||phase!=='playing')return;
     silent=true;syncSound();status.textContent='声音未能播放，已继续静音开场。';
    });
   }
   function finish(reason){
    if(closed)return;closed=true;cancelAnimationFrame(raf);abort.abort();stopAudio();markSeen();
    dialog.close();dialog.remove();document.documentElement.classList.remove('intro-open');
    returnFocus?.isConnected&&returnFocus.focus({preventScroll:true});
    current=null;resolve({reason,muted:silent,volume:audio.volume});
   }
   function showLines(t){dialog.querySelectorAll('[data-line]').forEach((p,i)=>{
    const shown=reduced||t>=lineTimes[i];p.classList.toggle('is-shown',shown);p.setAttribute('aria-hidden',String(!shown));
   });}
   function frame(now){
    if(closed||phase!=='playing')return;
    elapsed=Math.min(duration,(now-startedAt)/1000);showLines(elapsed);
    if(elapsed>=duration){phase='complete';stopAudio();find('[data-intro="enter"]').hidden=false;return;}
    raf=requestAnimationFrame(frame);
   }
   function begin(useSound){
    silent=!useSound;syncSound();phase='playing';elapsed=0;startedAt=performance.now();
    dialog.classList.add('intro-started');find('.intro-gate').hidden=true;find('.intro-story').hidden=false;
    find('[data-intro="skip"]').focus({preventScroll:true});playAudio();raf=requestAnimationFrame(frame);
   }
   dialog.addEventListener('cancel',e=>{e.preventDefault();finish('skip');},{signal:abort.signal});
   dialog.addEventListener('click',e=>{
    const b=e.target.closest('[data-intro]');if(!b)return;
    const action=b.dataset.intro;
    if(action==='skip'||action==='enter')finish(action);
    else if(action==='sound')begin(true);
    else if(action==='silent')begin(false);
    else if(action==='mute'){silent=!silent;syncSound();if(!silent)playAudio();}
    else if(action==='resume'){
     phase='playing';startedAt=performance.now()-elapsed*1000;b.hidden=true;status.textContent='';
     playAudio();raf=requestAnimationFrame(frame);find('[data-intro="skip"]').focus({preventScroll:true});
    }
   },{signal:abort.signal});
   find('.intro-volume').addEventListener('input',e=>{audio.volume=Number(e.target.value);onVolume(audio.volume);},{signal:abort.signal});
   document.addEventListener('visibilitychange',()=>{
    if(!document.hidden||phase!=='playing')return;
    elapsed=Math.min(duration,(performance.now()-startedAt)/1000);phase='paused';cancelAnimationFrame(raf);audio.pause();
    status.textContent='开场已暂停';find('[data-intro="resume"]').hidden=false;
   },{signal:abort.signal});
   window.addEventListener('pagehide',()=>finish('leave'),{signal:abort.signal});
   document.body.appendChild(dialog);document.documentElement.classList.add('intro-open');dialog.showModal();
   find('[data-intro="sound"]').focus({preventScroll:true});
  });
  return current;
 }
 return {open,hasSeen};
})();
