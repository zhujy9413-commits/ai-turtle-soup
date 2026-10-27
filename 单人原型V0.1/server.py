# -*- coding: utf-8 -*-
"""Local single-player prototype. Fixture by default; approved live rounds are explicit."""
import argparse,copy,json,mimetypes,re,secrets,sqlite3,threading,time,uuid
from http import cookies
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from live_host import HostFailure,public_history,requests_hidden_answer
from host_feedback import local_intent,help_text,public_references,solution_feedback,effective_answers

ROOT=Path(__file__).resolve().parent
SAFE_SCOPE='你问的是故事实际发生的情况，还是假设条件下的情况？'
SAFE_NARRATION='这句在汤面中是旁白。你想确认哪一段实际发出的声音？'
class GameError(Exception):
 def __init__(self,text,status=400,code='not_accepted',session=None):super().__init__(text);self.status=status;self.code=code;self.session=session
def normalized(s):return re.sub(r'[\s，。！？、,.!?：“”"\s；;：:]','',s).lower()

class GameService:
 def __init__(self,db=None,clock=None,auto_jobs=True,delay=.85,host=None,story_ids=None,single_session=False):
  self.clock=clock or time.time;self.auto_jobs=auto_jobs;self.delay=delay;self.lock=threading.RLock()
  self.host=host;self.inflight=set();self.single_session=single_session
  self.stories=json.loads((ROOT/'private/stories.json').read_text())
  self.allowed_stories=set(story_ids) if story_ids is not None else set(self.stories)
  if not self.allowed_stories or not self.allowed_stories<=set(self.stories):raise ValueError('Unknown story selection')
  self.db=sqlite3.connect(str(db or ROOT/'runtime/sessions.sqlite3'),check_same_thread=False)
  self.db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL)');self.db.commit()
  self.fixtures={}
  for sid,story in self.stories.items():
   self.fixtures[sid]={normalized(q[0]):{'action':'answer','answer':q[1],'scope':'actual'} for q in story['questions'] if q[1] in ['是','否','无关','不清楚']}
  self.fixtures['S02'][normalized('回我“我已经习惯了”的，是我自己的声音吗？')]={'action':'clarify','text':SAFE_NARRATION}
  self.fixtures['S06'][normalized('我们互换了，是吗？')]={'action':'clarify','text':'你指的是哪方面的互换？'}
  self.fixtures['S06'][normalized('假设我没有回应，病痛会在天亮撤回吗？')]={'action':'answer','answer':'是','scope':'hypothetical'}
  self.fixtures['S06'][normalized('我若不回应这次呼名，病痛会在天亮撤回吗？')]={'action':'answer','answer':'是','scope':'hypothetical'}
  for sid in self.stories:
   self.fixtures[sid][normalized('我没说清问实际还是假设，你能先确认范围吗？')]={'action':'clarify','text':SAFE_SCOPE}
  # Recovered processing jobs return their reservation; no ghost retry after restart.
  for (raw,) in self.db.execute('SELECT data FROM sessions').fetchall():
   s=json.loads(raw)
   if s['status']=='processing':
    self._release(s);s['pending']=None
    if s.get('host_mode')=='live':self._pause(s,'restart_interrupted')
    else:s['status']='active'
    self._msg(s,'system','error','上次处理中断，现场已保留，本次未扣次数。');self._save(s)
   elif s['status'] in ['revealing','reviewing']:
    self._build_review(s,True);s['status']='completed';self._save(s)
 def _save(self,s):
  self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?)',(s['id'],s['owner'],json.dumps(s,ensure_ascii=False)));self.db.commit()
 def _load(self,sid,owner=None):
  row=self.db.execute('SELECT data FROM sessions WHERE id=?',(sid,)).fetchone()
  if not row:raise GameError('没有找到这局记录。',404)
  s=json.loads(row[0])
  if owner is not None and s['owner']!=owner:raise GameError('没有找到这局记录。',404)
  return s
 def _msg(self,s,role,kind,text,**extra):
  m={'id':uuid.uuid4().hex,'role':role,'kind':kind,'text':text,'at':self.clock(),**extra};s['messages'].append(m);return m
 def remaining(self,s):
  if s['started_at'] is None:return s['duration']
  end=s.get('ended_at') or self.clock()
  frozen=s['frozen_seconds']
  if s['status']=='processing':frozen+=max(0,end-s['processing_started'])
  if s['status']=='paused':frozen+=max(0,end-s.get('paused_at',end))
  return max(0,s['duration']-(end-s['started_at']-frozen))
 def _release(self,s):
  if s.get('processing_started') is not None:s['frozen_seconds']+=max(0,self.clock()-s['processing_started'])
  s['processing_started']=None;s['reserved']=0
 def _pause(self,s,reason):
  if s['status']=='paused':return
  if s['status']=='processing':self._release(s)
  s['status']='paused';s['paused_at']=self.clock();s['pause_reason']=reason;s['pending']=None
  self._save(s)
 def _release_pause(self,s):
  if s.get('paused_at') is not None:s['frozen_seconds']+=max(0,self.clock()-s['paused_at'])
  s['paused_at']=None;s['pause_reason']=None
 def pause_all(self,reason):
  with self.lock:
   for (raw,) in self.db.execute('SELECT data FROM sessions').fetchall():
    s=json.loads(raw)
    if s['status'] in ('active','processing'):self._pause(s,reason)
 def resume(self,sid,owner):
  with self.lock:
   s=self._load(sid,owner)
   if s['status']!='paused':return self._view(s)
   try:
    if s.get('host_mode')=='live':
     if not self.host:raise HostFailure('host_unavailable')
     self.host.connection_ready()
   except HostFailure as error:
    raise GameError('主持连接尚未恢复，计时继续暂停，记录与次数已保留。',503,error.code,self._view(s)) from None
   self._release_pause(s);s['status']='active' if s['started_at'] is not None else 'opening'
   self._save(s);return self._view(s)
 def _later(self,fn,*args):
  if self.auto_jobs:
   def task():
    time.sleep(self.delay)
    try:fn(*args)
    except Exception:
     # A failed fixture worker never leaves a permanent processing freeze.
     with self.lock:
      s=self._load(args[0])
      if s['status']=='processing':
       self._release(s);s['pending']=None;s['status']='active'
       if s.get('host_mode')=='live':self._pause(s,'worker_interrupted')
       self._msg(s,'system','error','这次处理未完成，本次未扣次数，现场已保留。');self._save(s)
   threading.Thread(target=task,daemon=True).start()
 def _expire(self,s):
  if s['status']=='active' and self.remaining(s)<=0:self._begin_end(s,'timeout');return
  if s['status']=='active' and s['attempts_left']<=0:self._begin_end(s,'attempts')
 def _begin_end(self,s,reason):
  s['status']='revealing';s['ended_at']=self.clock();s['end_reason']=reason;s['pending']=None
  self._save(s);self._later(self.advance_end,s['id'])
 def _build_review(self,s,fallback=False):
  story=self.stories[s['story_id']]
  answered=effective_answers(s['messages'])
  asked=[m for m in s['messages'] if m['kind']=='question']
  summary=f"这一局留下了 {len(asked)} 次正式提问、{len(answered)} 次四词回答，使用了 {s['hint_count']} 层提示。"
  highlights=[]
  for m in answered[:3]:
   q=next((q for q in asked if q['id']==m.get('question_id')),None)
   if q:highlights.append({'question':q['text'],'answer':m['text'],'scope':m.get('scope','actual')})
  s['review']={'summary':summary,'highlights':highlights,'causes':[n[1] for n in story['nodes']],
   'kind':'fixed_fallback' if fallback else 'fixed_record_review','record_version':'0.9.3',
   'note':'自动复盘暂不可用，已提供固定因果讲解。' if fallback else '固定因果讲解与本局记录；复盘不另外调用模型。'}
 def advance_end(self,sid):
  with self.lock:
   s=self._load(sid)
   if s['status']=='revealing':s['status']='reviewing';self._save(s);self._later(self.advance_end,sid)
   elif s['status']=='reviewing':
    self._build_review(s,s.get('review_fault',False));s['status']='completed';self._save(s)
 def list_stories(self,owner):
  with self.lock:
   played={json.loads(r[0])['story_id'] for r in self.db.execute('SELECT data FROM sessions WHERE owner=?',(owner,)) if json.loads(r[0])['status']=='completed'}
   return [{'id':sid,'title':s['title'],**s['card'],'played':sid in played} for sid,s in self.stories.items() if sid in self.allowed_stories]
 def create(self,owner,data):
  sid=data.get('story_id');minutes=data.get('minutes');attempts=data.get('attempts')
  if sid not in self.allowed_stories:raise GameError('请先选择一道汤。')
  if type(minutes) is not int or not 1<=minutes<=60:raise GameError('时长请填写 1—60 的整数分钟。')
  if type(attempts) is not int or not 1<=attempts<=100:raise GameError('次数请填写 1—100 的整数。')
  if self.single_session and (minutes>40 or attempts>40):raise GameError('本轮最多40分钟、40次提问与还原机会。')
  with self.lock:
   if self.single_session and self.db.execute('SELECT 1 FROM sessions LIMIT 1').fetchone():raise GameError('本轮档案已打开，请继续现有对局。',409)
   for (raw,) in self.db.execute('SELECT data FROM sessions WHERE owner=?',(owner,)):
    old=json.loads(raw)
    self._expire(old)
    if old['status'] in ['opening','active','processing','paused','revealing','reviewing']:raise GameError('请先继续或结束当前对局。',409)
   s={'id':uuid.uuid4().hex,'owner':owner,'story_id':sid,'host_mode':'live' if self.host else 'fixture','status':'opening','duration':minutes*60,'attempts_total':attempts,'attempts_left':attempts,
    'created_at':self.clock(),'started_at':None,'ended_at':None,'processing_started':None,'frozen_seconds':0,'reserved':0,'hint_count':0,
    'messages':[],'hints':[],'pending':None,'request_ids':[],'question_draft':'','solution_draft':'','end_reason':None,'review':None,'feedback':[],
    'last_player_at':self.clock(),'cold_notified':False,'review_fault':False}
   self._save(s);return self._view(s)
 def current(self,owner):
  with self.lock:
   rows=self.db.execute('SELECT data FROM sessions WHERE owner=? ORDER BY rowid DESC',(owner,)).fetchall()
   for (raw,) in rows:
    s=json.loads(raw)
    if s['status']!='interrupted':return self.snapshot(s['id'],owner)
   return None
 def _view(self,s):
  story=self.stories[s['story_id']]
  if s['status']=='completed' and (s.get('review') or {}).get('record_version')!='0.9.3':
   self._build_review(s,(s.get('review') or {}).get('kind') in ('fixed_fallback','fixed_record_fallback'))
  result={k:copy.deepcopy(s[k]) for k in ['id','story_id','status','duration','attempts_total','attempts_left','reserved','hint_count','messages','hints','question_draft','solution_draft','end_reason','review']}
  result.update(title=story['title'],surface=story['surface'],remaining_seconds=self.remaining(s),server_time=self.clock(),mode=s.get('host_mode','fixture'),
   starters=story['starters'],last_request_id=s['request_ids'][-1] if s['request_ids'] else None,frozen=s['status'] in ['processing','paused'],pause_reason=s.get('pause_reason'),pending_kind=s['pending']['kind'] if s['pending'] else None)
  if s['status'] in ['revealing','reviewing','completed']:result['truth']=story['truth']
  if s['story_id']=='S02':
   result['hints']=[x.replace('阿宁','你') for x in result['hints']]
   for m in result['messages']:
    if m['role']=='host':m['text']=m['text'].replace('阿宁','你')
   if result['review']:result['review']['causes']=[x.replace('阿宁','我').replace('她','我') for x in result['review']['causes']]
  return result
 def snapshot(self,sid,owner):
  with self.lock:
   s=self._load(sid,owner);self._expire(s)
   if s['status']=='active' and self.clock()-s['last_player_at']>=120 and not s['cold_notified'] and s['hint_count']<3:
    self._msg(s,'system','cold','卡住了吗？需要时可以获取下一层提示。');s['cold_notified']=True
   self._save(s);return self._view(s)
 def ready(self,sid,owner):
  with self.lock:
   s=self._load(sid,owner)
   if s['status']=='opening':
    if s.get('host_mode')=='live':
     try:self.host.connection_ready()
     except (HostFailure,AttributeError):
      self._pause(s,'credential_required');return self._view(s)
    s['started_at']=self.clock();s['last_player_at']=self.clock();s['status']='active';self._save(s)
   return self._view(s)
 def request_state(self,sid,owner,rid):
  with self.lock:
   s=self._load(sid,owner)
   return {'accepted':rid in s['request_ids'],'session':self._view(s)}
 def resolve_request(self,sid,owner,rid):
  if not isinstance(rid,str) or not re.fullmatch('[a-zA-Z0-9-]{10,80}',rid):raise GameError('请求标识无效。')
  with self.lock:
   s=self._load(sid,owner);accepted=rid in s['request_ids']
   if not accepted:
    cancelled=s.setdefault('cancelled_request_ids',[])
    if rid not in cancelled:cancelled.append(rid)
    self._save(s)
   return {'accepted':accepted,'cancelled':not accepted,'session':self._view(s)}
 def _hint(self,s):
  text=self.stories[s['story_id']]['hints'][s['hint_count']]
  s['hint_count']+=1;s['hints'].append(text);self._msg(s,'host','hint',text,level=s['hint_count'])
 def save_draft(self,sid,owner,data):
  with self.lock:
   s=self._load(sid,owner)
   for name in ['question_draft','solution_draft']:
    if name in data:s[name]=str(data[name])[:3000]
   self._save(s);return {'saved':True}
 def submit(self,sid,owner,data):
  kind=data.get('kind');rid=data.get('request_id');text=str(data.get('text','')).strip()
  if kind not in ['question','solution','hint']:raise GameError('不支持的操作。')
  if not isinstance(rid,str) or not re.fullmatch('[a-zA-Z0-9-]{10,80}',rid):raise GameError('请求标识无效。')
  with self.lock:
   s=self._load(sid,owner)
   if rid in s['request_ids']:return self._view(s)
   if rid in s.get('cancelled_request_ids',[]):raise GameError('这次提交已确认未受理，可以重新提问。',409,'request_cancelled')
   self._expire(s)
   if kind!='hint' and (not text or len(text)>3000):raise GameError('请填写问题或还原，最多3000字。')
   intent=local_intent(text) if kind=='question' else None
   if s['status'] in ['active','paused'] and (intent in ('progress','rules') or (kind=='question' and requests_hidden_answer({'text':text}))):
    self._msg(s,'player','help',text)
    reply=help_text(public_history(s['messages']),intent or 'progress') if intent else '想查看完整故事，可以主动选择“查看汤底”并确认揭晓。'
    self._msg(s,'host','help',reply);s['request_ids'].append(rid);s['last_player_at']=self.clock();self._save(s);return self._view(s)
   if intent=='hint':
    if s['hint_count']>=3:
     self._msg(s,'player','help',text);self._msg(s,'host','help','本局三层提示已经全部公开，可以回看汤面中的提示记录，或问我“哪里不完整”。')
     s['request_ids'].append(rid);self._save(s);return self._view(s)
    kind='hint'
   if s['status']!='active':raise GameError('本局当前已暂停或结束，记录已保留。',409,'session_not_active',self._view(s))
   if kind=='hint' and s['hint_count']>=3:raise GameError('三层提示已经全部公开。')
   if kind!='hint' and (not text or len(text)>3000):raise GameError('请填写问题或还原，最多3000字。')
   if kind!='hint' and s['attempts_left']<=0:raise GameError('剩余次数已经用尽。',409)
   demo=data.get('demo_outcome') if kind=='solution' else None
   if demo not in [None,'complete','incomplete','conflict']:raise GameError('模拟结果无效。')
   fault=data.get('fault')
   if fault not in [None,'timeout','bad_output']:raise GameError('模拟异常无效。')
   if s.get('host_mode')=='live':
    if demo is not None or fault is not None:raise GameError('当前对局不接受模拟结果。')
    try:
     if not self.host:raise HostFailure('host_unavailable')
     self.host.available(kind)
    except HostFailure as error:
     self._pause(s,error.code)
     raise GameError('主持暂时中断，计时已暂停，记录与次数已保留。',503,error.code,self._view(s)) from None
   if kind=='hint':
    if intent:self._msg(s,'player','help',text)
    self._hint(s);s['request_ids'].append(rid);s['last_player_at']=self.clock();s['cold_notified']=False;self._save(s);return self._view(s)
   message=None
   if kind!='hint':message=self._msg(s,'player',kind,text,simulated_outcome=demo)
   s['pending']={'kind':kind,'text':text,'demo_outcome':demo,'fault':fault,'question_id':message['id'] if message else None,'request_id':rid}
   s['request_ids'].append(rid);s['reserved']=int(kind!='hint');s['processing_started']=self.clock();s['status']='processing'
   s['last_player_at']=self.clock();s['cold_notified']=False
   if kind=='question':s['question_draft']=''
   elif kind=='solution':s['solution_draft']=''
   self._save(s);self._later(self.complete_job,sid);return self._view(s)
 def complete_job(self,sid):
  with self.lock:
   current=self._load(sid)
   if current['status']!='processing':return
   live=current.get('host_mode')=='live' and current['status']=='processing' and current['pending']['kind']!='hint'
  if live:return self._complete_live_job(sid)
  with self.lock:
   s=self._load(sid)
   if s['status']!='processing':return
   p=s['pending'];story=self.stories[s['story_id']];ending=None
   if p['fault']:
    label='模拟超时' if p['fault']=='timeout' else '模拟非法输出'
    self._msg(s,'system','error',label+'：本次未扣次数，计时恢复。')
   elif p['kind']=='hint':
    text=story['hints'][s['hint_count']];s['hint_count']+=1;s['hints'].append(text)
    self._msg(s,'host','hint',text,level=s['hint_count'])
   elif p['kind']=='question':
    verdict=self.fixtures[s['story_id']].get(normalized(p['text']))
    if verdict is None:
     self._msg(s,'system','demo_notice','模拟主持仅支持示例问题和预设问法，本次未判定、未扣次数。真实自由提问将在模型联调后启用。')
    elif verdict['action']=='clarify':self._msg(s,'host','clarify',verdict['text'],question_id=p['question_id'])
    else:
     self._msg(s,'host','answer',verdict['answer'],question_id=p['question_id'],scope=verdict.get('scope','actual'))
     s['attempts_left']-=1
   else:
    outcome=p['demo_outcome']
    if outcome is None:
     matching=[x for x in story['solutions'] if normalized(x['text'])==normalized(p['text'])]
     if matching:
      label=matching[0]['result'];outcome='complete' if label=='通过' else ('conflict' if '冲突' in label else 'incomplete')
    if outcome=='complete':
     self._msg(s,'host','solution_result','还原成立。（模拟判定）');ending='success'
    elif outcome in ['incomplete','conflict']:
     self._msg(s,'host','solution_result','尚未还原完整，可继续结合已确认的线索。（模拟判定）');s['attempts_left']-=1
    else:self._msg(s,'system','demo_notice','模拟版暂不判断自由还原。可在还原窗口使用“模拟测试工具”检查完整、未完整和冲突分支；本次未扣次数。')
   self._release(s);s['status']='active';s['pending']=None
   if ending:self._begin_end(s,ending)
   else:self._expire(s)
   self._save(s)
 def _complete_live_job(self,sid):
  # Snapshot under lock, release during network I/O so polls and drafts remain responsive.
  with self.lock:
   s=self._load(sid)
   if s['status']!='processing' or sid in self.inflight:return
   self.inflight.add(sid);p=copy.deepcopy(s['pending']);story=copy.deepcopy(self.stories[s['story_id']])
   history=public_history(s['messages'],p['question_id'])
  verdict=None;failure=None
  try:
   if not self.host:raise HostFailure('host_unavailable')
   verdict=self.host.judge(story,history,{'id':p['question_id'],'text':p['text']},p['kind'],sid+'/'+p['request_id'])
  except Exception as e:
   failure=e.code if isinstance(e,HostFailure) else 'host_failed'
  finally:
   with self.lock:
    self.inflight.discard(sid)
    s=self._load(sid)
    if s['status']!='processing' or s['pending'].get('request_id')!=p['request_id']:return
    ending=None
    pause_reason=None
    if failure:
     recoverable={'invalid_scope','unsupported_public_correction','invalid_output','wrong_channel','unsafe_clarification','unexpected_display_text','incomplete_success','invalid_provider_output','unsupported_assessment'}
     if failure in recoverable:
      self._msg(s,'system','error','这条消息暂时未能可靠判断，本次未扣次数。可以换个问法继续，原有记录已保留。')
     else:
      pause_reason=failure
      self._msg(s,'system','error','主持暂时中断，计时已暂停，本次未扣次数。恢复后可继续原来的推理。')
     s.setdefault('host_audit',[]).append({'request_id':p['request_id'],'error_code':failure})
    else:
     action=verdict['action'];scope=verdict['scope']
     s.setdefault('host_audit',[]).append({'request_id':p['request_id'],'verdict':verdict})
     if action=='answer':
      self._msg(s,'host','answer',verdict['answer'],question_id=p['question_id'],scope=scope);s['attempts_left']-=1
     elif action=='content_gap':
      pause_reason='content_gap';self._msg(s,'system','error','这处设定需要核对，计时已暂停，本次未扣次数。')
     elif action=='help':self._msg(s,'host','help',verdict['text'])
     elif action=='request_hint':
      if s['hint_count']<3:self._hint(s)
      else:self._msg(s,'host','help','本局三层提示已经公开，可以回看提示记录或核对上一段还原。')
     elif action=='clarify':self._msg(s,'host','clarify',verdict['clarification'],question_id=p['question_id'])
     elif action in ['invalid','decline']:
      text=('请用自己的话讲清已经推理出的经过和原因，不需要写成是非问题。' if p['kind']=='solution' else
            ('请一次确认一个清楚的命题；对局求助也可以直接提出。' if action=='invalid' else '想查看完整故事，可以主动选择“查看汤底”并确认揭晓。'))
      self._msg(s,'host',action,text,question_id=p['question_id'])
     elif action=='complete':self._msg(s,'host','solution_result','还原成立。');ending='success'
     else:
      feedback=solution_feedback(action,public_references(history),verdict.get('review_refs',[]),verdict,story)
      self._msg(s,'host','solution_result',feedback);s['attempts_left']-=1
    self._release(s);s['status']='active';s['pending']=None
    if pause_reason:self._pause(s,pause_reason)
    elif ending:self._begin_end(s,ending)
    else:self._expire(s)
    self._save(s)
 def runtime(self):
  result=self.host.runtime() if self.host else {'mode':'fixture','live_enabled':False,'provider_calls':0,'remaining_authorized_provider_calls':0}
  if self.single_session:result['player_limits']={'minutes':40,'attempts':40}
  return result
 def reveal(self,sid,owner,confirmed=False,review_fault=False):
  if confirmed is not True:raise GameError('请先确认提前揭晓。')
  with self.lock:
   s=self._load(sid,owner);self._expire(s)
   if s.get('host_mode')=='live' and review_fault:raise GameError('当前对局不接受模拟结果。')
   if s['status'] in ['active','paused']:
    if s['status']=='paused':self._release_pause(s)
    s['review_fault']=bool(review_fault);self._begin_end(s,'manual')
   elif s['status'] not in ['revealing','reviewing','completed']:raise GameError('请等当前处理结束再揭晓。',409)
   return self._view(s)
 def interrupt(self,sid,owner):
  with self.lock:
   s=self._load(sid,owner)
   if s['status']=='processing':raise GameError('请先等待当前处理结束。',409)
   if s['status'] in ['opening','active','paused']:
    if s['status']=='paused':self._release_pause(s)
    s['status']='interrupted';s['ended_at']=self.clock();s['end_reason']='interrupted';self._save(s)
   return self._view(s)
 def feedback(self,sid,owner,data):
  with self.lock:
   s=self._load(sid,owner);text=str(data.get('text','')).strip()[:1000]
   if not text:raise GameError('请填写想反馈的问题。')
   message_id=data.get('message_id')
   if message_id and not any(m['id']==message_id for m in s['messages']):raise GameError('消息不存在。')
   s['feedback'].append({'text':text,'message_id':message_id,'at':self.clock()});self._save(s);return {'saved':True}

class Handler(BaseHTTPRequestHandler):
 service=None
 def log_message(self,format,*args):pass
 def handle(self):
  try:super().handle()
  except (BrokenPipeError,ConnectionResetError):pass
 def respond(self,obj,status=200):
  raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self._headers(len(raw));self.end_headers();self.wfile.write(raw)
 def _headers(self,length):
  self.send_header('Content-Length',str(length));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
  if getattr(self,'new_owner',False):self.send_header('Set-Cookie',f'turtle_local={self.owner}; HttpOnly; SameSite=Strict; Path=/')
 def identify(self):
  c=cookies.SimpleCookie()
  try:c.load(self.headers.get('Cookie',''))
  except cookies.CookieError:pass
  val=c.get('turtle_local');owner=val.value if val else ''
  self.new_owner=not bool(re.fullmatch('[a-f0-9]{48}',owner));self.owner=secrets.token_hex(24) if self.new_owner else owner
 def do_GET(self):
  self.identify();path=urlsplit(self.path).path
  try:
   if path=='/api/stories':return self.respond({'stories':self.service.list_stories(self.owner)})
   if path=='/api/current':return self.respond({'session':self.service.current(self.owner)})
   if path=='/api/runtime':return self.respond(self.service.runtime())
   match=re.fullmatch(r'/api/sessions/([a-f0-9]{32})',path)
   if match:return self.respond({'session':self.service.snapshot(match[1],self.owner)})
   request_match=re.fullmatch(r'/api/sessions/([a-f0-9]{32})/requests/([a-zA-Z0-9-]{10,80})',path)
   if request_match:return self.respond(self.service.request_state(request_match[1],self.owner,request_match[2]))
   allowed={'/night.css':'night.css','/fonts/night-song.woff2':'fonts/night-song.woff2','/fonts/NotoSerifSC-OFL.txt':'fonts/NotoSerifSC-OFL.txt','/quota.js':'quota.js','/unlock.wav':'unlock.wav','/art/old-key.png':'art/old-key.png','/art/selection-handprint.png':'art/selection-handprint.png','/archive.css':'archive.css','/art/archive-paper.png':'art/archive-paper.png','/':'index.html','/app.js':'app.js','/intro.js':'intro.js','/intro.css':'intro.css','/intro-knocks.wav':'intro-knocks.wav','/styles.css':'styles.css','/ambient.wav':'ambient.wav','/favicon.svg':'favicon.svg','/atmosphere.css':'atmosphere.css','/art/night-door.png':'art/night-door.png','/art/night-door-red.png':'art/night-door-red.png','/art/story-scenes.png':'art/story-scenes.png','/art/yetang-logo.png':'art/yetang-logo.png','/fonts/long-cang-title.ttf':'fonts/long-cang-title.ttf','/fonts/LongCang-OFL.txt':'fonts/LongCang-OFL.txt','/fonts/night-display.woff2':'fonts/night-display.woff2','/fonts/night-reading.woff2':'fonts/night-reading.woff2','/fonts/WenKai-OFL.txt':'fonts/WenKai-OFL.txt'}
   if path not in allowed:return self.respond({'error':'没有这个页面。'},404)
   p=ROOT/'public'/allowed[path];data=p.read_bytes();self.send_response(200);self.send_header('Content-Type',(mimetypes.guess_type(str(p))[0] or 'application/octet-stream')+('' if p.suffix not in ['.html','.css','.js'] else '; charset=utf-8'));self._headers(len(data));self.end_headers();self.wfile.write(data)
  except GameError as e:self.respond({'error':str(e),'accepted':False,'code':e.code,**({'session':e.session} if e.session else {})},e.status)
 def do_POST(self):
  self.identify()
  try:
   origin=self.headers.get('Origin')
   if origin and origin!=f'http://{self.headers.get("Host")}':raise GameError('请求来源不匹配。',403)
   if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise GameError('需要JSON请求。',415)
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=20000:raise GameError('请求内容过长。',413)
   data=json.loads(self.rfile.read(length))
   if not isinstance(data,dict):raise GameError('请求格式无效。')
   path=urlsplit(self.path).path
   if path=='/api/sessions':return self.respond({'session':self.service.create(self.owner,data)},201)
   if path.startswith('/api/live'):raise GameError('真实模型尚未启用；当前调用额度已用满，不会发送请求。',409)
   match=re.fullmatch(r'/api/sessions/([a-f0-9]{32})/(ready|resume|resolve|draft|submit|reveal|interrupt|feedback)',path)
   if not match:raise GameError('没有这个操作。',404)
   sid,action=match.groups()
   if action=='ready':
    if data.get('surface_presented') is not True:raise GameError('请先阅读完整汤面，再开始推理。')
    result={'session':self.service.ready(sid,self.owner)}
   elif action=='resume':result={'session':self.service.resume(sid,self.owner)}
   elif action=='resolve':result=self.service.resolve_request(sid,self.owner,data.get('request_id'))
   elif action=='draft':result=self.service.save_draft(sid,self.owner,data)
   elif action=='submit':result={'session':self.service.submit(sid,self.owner,data)}
   elif action=='reveal':result={'session':self.service.reveal(sid,self.owner,data.get('confirmed'),data.get('review_fault'))}
   elif action=='interrupt':result={'session':self.service.interrupt(sid,self.owner)}
   else:result=self.service.feedback(sid,self.owner,data)
   self.respond(result)
  except GameError as e:self.respond({'error':str(e),'accepted':False,'code':e.code,**({'session':e.session} if e.session else {})},e.status)
  except (ValueError,TypeError):self.respond({'error':'请求格式无效。'},400)

def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8876);p.add_argument('--data-dir');p.add_argument('--open',action='store_true',dest='open_browser')
 p.add_argument('--host',choices=['fixture','live'],default='fixture');p.add_argument('--authorization');args=p.parse_args()
 runtime=Path(args.data_dir) if args.data_dir else ROOT/'runtime'/('live' if args.host=='live' else '')
 host=None
 if args.host=='live':
  if not args.authorization:p.error('真实主持需要本轮已确认的调用额度文件，当前没有启用。')
  from live_host import load_live_host
  try:host=load_live_host(args.authorization,runtime)
  except HostFailure as e:p.error('真实主持未启用：'+e.code)
 runtime.mkdir(parents=True,exist_ok=True)
 Handler.service=GameService(runtime/'sessions.sqlite3',host=host)
 try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 except OSError:
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  print('默认端口被占用，已自动选择空闲端口。',flush=True)
 print(f'夜汤单人原型（{"真实AI主持联调" if host else "模拟主持"}）已启动：http://127.0.0.1:{server.server_port}',flush=True)
 print('本机服务。'+('调用受本轮次数和费用上限约束。' if host else '不调用模型。')+'保持此窗口开启；按 Ctrl+C 关闭。',flush=True)
 if args.open_browser:
  import webbrowser
  threading.Timer(.5,lambda:webbrowser.open(f'http://127.0.0.1:{server.server_port}')).start()
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.server_close()
if __name__=='__main__':main()
