# -*- coding: utf-8 -*-
import json,sys,tempfile,unittest,uuid,threading,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import GameService,GameError,Handler,ThreadingHTTPServer
from host_boundary import public_scope_input,private_judge_input,dispatch_provider,LiveHostUnavailable

class Clock:
 def __init__(self):self.now=1000.
 def __call__(self):return self.now
 def advance(self,n):self.now+=n
class GameTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.clock=Clock();self.game=GameService(Path(self.temp.name)/'test.db',self.clock,False);self.owner='test-owner'
 def tearDown(self):self.game.db.close();self.temp.cleanup()
 def create(self,story='S07',attempts=5,minutes=1):
  s=self.game.create(self.owner,{'story_id':story,'minutes':minutes,'attempts':attempts});self.id=s['id'];return s
 def start(self,**kw):self.create(**kw);return self.game.ready(self.id,self.owner)
 def submit(self,kind='question',text='妹妹真的出差了吗？',**kw):return self.game.submit(self.id,self.owner,{'kind':kind,'text':text,'request_id':uuid.uuid4().hex,**kw})
 def view(self):return self.game.snapshot(self.id,self.owner)
 def finish(self):self.game.advance_end(self.id);self.game.advance_end(self.id);return self.view()
 def test_opening_clock_and_background_elapsed(self):
  self.create();self.clock.advance(30);self.assertEqual(self.view()['remaining_seconds'],60)
  self.game.ready(self.id,self.owner);self.clock.advance(20);self.assertEqual(self.view()['remaining_seconds'],40)
  self.clock.advance(50);self.assertEqual(self.view()['status'],'revealing')
 def test_processing_freezes_entire_wait_and_charges_once(self):
  self.start();self.clock.advance(7);self.submit();self.clock.advance(80)
  s=self.view();self.assertEqual(s['remaining_seconds'],53);self.assertEqual(s['reserved'],1)
  self.game.complete_job(self.id);s=self.view();self.assertEqual(s['attempts_left'],4);self.assertEqual(s['remaining_seconds'],53)
  self.clock.advance(9);self.assertEqual(self.view()['remaining_seconds'],44)
 def test_idempotent_request_does_not_duplicate_hint(self):
  self.start();data={'kind':'hint','text':'','request_id':uuid.uuid4().hex}
  self.game.submit(self.id,self.owner,data);self.game.submit(self.id,self.owner,data);self.game.complete_job(self.id)
  self.game.submit(self.id,self.owner,data);s=self.view();self.assertEqual(s['hint_count'],1);self.assertEqual(len(s['hints']),1);self.assertEqual(s['attempts_left'],5)
 def test_only_three_hints_and_no_private_hints_before_publication(self):
  self.start();self.assertEqual(self.view()['hints'],[])
  for _ in range(3):self.submit(kind='hint',text='');self.game.complete_job(self.id)
  with self.assertRaises(GameError):self.submit(kind='hint',text='')
  self.assertEqual(self.view()['hint_count'],3)
 def test_unknown_and_failure_release_last_attempt(self):
  self.start(attempts=1);self.submit(text='这里是随便一个新问题');self.game.complete_job(self.id)
  self.assertEqual(self.view()['attempts_left'],1);self.assertEqual(self.view()['status'],'active')
  self.submit(fault='timeout');self.clock.advance(15);self.game.complete_job(self.id)
  self.assertEqual(self.view()['attempts_left'],1);self.assertFalse(self.view()['frozen']);self.assertEqual(self.view()['remaining_seconds'],60)
 def test_clarification_does_not_charge_or_announce_hidden_answer(self):
  self.start(story='S06');self.submit(text='我们互换了，是吗？');self.game.complete_job(self.id)
  s=self.view();self.assertEqual(s['attempts_left'],5);self.assertEqual(s['messages'][-1]['text'],'你指的是哪方面的互换？');self.assertNotIn('truth',s)
 def test_correct_solution_on_last_attempt_wins_before_exhaustion(self):
  self.start(attempts=1);self.submit(kind='solution',text='（模拟测试）完整还原',demo_outcome='complete');self.game.complete_job(self.id)
  self.assertEqual(self.view()['end_reason'],'success');s=self.finish();self.assertEqual(s['status'],'completed');self.assertIn('truth',s)
 def test_incomplete_solution_consumes_last_attempt(self):
  self.start(attempts=1);self.submit(kind='solution',text='（模拟测试）缺少因果',demo_outcome='incomplete');self.game.complete_job(self.id)
  self.assertEqual(self.view()['attempts_left'],0);self.assertEqual(self.view()['end_reason'],'attempts')
 def test_manual_reveal_confirmation_and_review_completion(self):
  self.start()
  with self.assertRaises(GameError):self.game.reveal(self.id,self.owner)
  self.assertNotIn('truth',self.view());s=self.game.reveal(self.id,self.owner,True,True);self.assertEqual(s['status'],'revealing')
  s=self.finish();self.assertEqual(s['status'],'completed');self.assertEqual(s['review']['kind'],'fixed_fallback');self.assertEqual(s['end_reason'],'manual')
 def test_refresh_preserves_drafts_history_and_timer(self):
  self.start();self.game.save_draft(self.id,self.owner,{'solution_draft':'一段未提交的草稿'})
  self.clock.advance(8);self.submit();self.game.complete_job(self.id);self.clock.advance(9)
  other=GameService(Path(self.temp.name)/'test.db',self.clock,False);s=other.snapshot(self.id,self.owner)
  self.assertEqual(s['solution_draft'],'一段未提交的草稿');self.assertEqual(s['attempts_left'],4);self.assertEqual(s['remaining_seconds'],43);other.db.close()
 def test_restart_recovers_pending_without_retry_or_charge(self):
  self.start();self.submit();self.clock.advance(25)
  other=GameService(Path(self.temp.name)/'test.db',self.clock,False);s=other.snapshot(self.id,self.owner)
  self.assertEqual(s['status'],'active');self.assertEqual(s['attempts_left'],5);self.assertEqual(s['remaining_seconds'],60);other.db.close()
 def test_no_hidden_fields_before_reveal_and_owner_isolation(self):
  self.start();s=self.view()
  for field in ['truth','facts','nodes','temporal_model','questions','solutions','owner','pending']:self.assertNotIn(field,s)
  for card in self.game.list_stories(self.owner):
   for field in ['setting','truth','facts','hints','surface']:self.assertNotIn(field,card)
  with self.assertRaises(GameError):self.game.snapshot(self.id,'another-owner')
 def test_invalid_settings_and_busy_exclusion(self):
  with self.assertRaises(GameError):self.create(minutes=0)
  self.start();self.submit()
  with self.assertRaises(GameError):self.submit(kind='hint',text='')
 def test_cold_reminder_once_and_no_auto_hint(self):
  self.start(minutes=10);self.clock.advance(121);s=self.view();self.assertEqual(s['messages'][-1]['kind'],'cold');n=len(s['messages'])
  self.clock.advance(100);self.assertEqual(len(self.view()['messages']),n);self.assertEqual(s['hint_count'],0)
 def test_review_highlights_are_actual_messages_not_invented(self):
  self.start();self.submit();self.game.complete_job(self.id);self.game.reveal(self.id,self.owner,True);s=self.finish()
  self.assertEqual(s['review']['highlights'][0]['question'],'妹妹真的出差了吗？');self.assertEqual(s['review']['highlights'][0]['answer'],'否')
 def test_hypothetical_fixture_is_separate_from_actual_answer(self):
  self.start(story='S06');self.submit(text='假设我没有回应，病痛会在天亮撤回吗？');self.game.complete_job(self.id)
  s=self.view();self.assertEqual(s['messages'][-1]['scope'],'hypothetical');self.assertEqual(s['messages'][-1]['text'],'是')
  self.submit(text='故事结尾我已经死了吗？');self.game.complete_job(self.id)
  s=self.view();self.assertEqual(s['messages'][-1]['scope'],'actual');self.assertEqual(s['messages'][-1]['text'],'否')
 def test_all_three_stories_publish_hints_and_review_without_unintroduced_name(self):
  for sid in ['S02','S06','S07']:
   with self.subTest(story=sid):
    self.start(story=sid)
    for _ in range(3):self.submit(kind='hint',text='');self.game.complete_job(self.id)
    self.assertEqual(len(self.view()['hints']),3)
    self.game.reveal(self.id,self.owner,True);s=self.finish()
    published=[s['surface'],*s['hints'],*s['truth'],*s['review']['causes']]
    self.assertNotIn('阿宁',''.join(published))
 def test_legacy_host_hint_corrected_without_changing_player_or_saved_history(self):
  self.start(story='S02');s=self.game._load(self.id)
  s['hints']=['那一分钟里，阿宁还在屋外。'];s['hint_count']=1
  self.game._msg(s,'host','hint',s['hints'][0]);self.game._msg(s,'player','question','阿宁是我吗？');self.game._save(s)
  view=self.view();self.assertEqual(view['hints'],['那一分钟里，你还在屋外。'])
  self.assertEqual(view['messages'][0]['text'],'那一分钟里，你还在屋外。')
  self.assertEqual(view['messages'][1]['text'],'阿宁是我吗？')
  self.assertIn('阿宁',self.game._load(self.id)['messages'][0]['text'])
  self.assertNotIn('truth',view)

class HTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();Handler.service=GameService(Path(cls.temp.name)/'http.db',auto_jobs=False)
  cls.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.url=f'http://127.0.0.1:{cls.http.server_port}'
  cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();Handler.service.db.close();cls.temp.cleanup()
 def client(self):return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def test_public_routes_and_private_files_denied(self):
  client=self.client()
  for path in ['/','/app.js','/styles.css','/ambient.wav']:
   with client.open(self.url+path) as r:self.assertEqual(r.status,200)
  for path in ['/private/stories.json','/runtime/sessions.sqlite3','/server.py','/../private/stories.json',
               '/private/host/judge_prompt.txt','/private/host/authorization.example.json','/live_host.py','/tools/配置本轮百炼密钥.py']:
   with self.assertRaises(urllib.error.HTTPError) as e:client.open(self.url+quote(path))
   self.assertEqual(e.exception.code,404)
 def test_session_cookie_and_live_mode_blocked(self):
  c=self.client()
  with c.open(self.url+'/api/stories') as r:self.assertEqual(len(json.load(r)['stories']),4)
  req=urllib.request.Request(self.url+'/api/sessions',data=json.dumps({'story_id':'S02','minutes':1,'attempts':2}).encode(),headers={'Content-Type':'application/json'})
  with c.open(req) as r:s=json.load(r)['session']
  with self.assertRaises(urllib.error.HTTPError) as e:self.client().open(self.url+'/api/sessions/'+s['id'])
  self.assertEqual(e.exception.code,404)
  req=urllib.request.Request(self.url+'/api/live/question',data=b'{}',headers={'Content-Type':'application/json'})
  with self.assertRaises(urllib.error.HTTPError) as e:c.open(req)
  self.assertEqual(e.exception.code,409)

class BoundaryTests(unittest.TestCase):
 def test_public_scope_shape_excludes_hidden_data_and_solution_is_actual(self):
  story={'id':'test','surface':'公开汤面','truth':['私有哨兵'],'facts':{'F1':'私有哨兵'},'questions':['私有夹具']}
  body=public_scope_input(story,[],{'text':'问题'})
  self.assertNotIn('私有哨兵',json.dumps(body,ensure_ascii=False));self.assertEqual(set(body),{'public_surface','public_history','current_message'})
  judged=private_judge_input(story,[],{'text':'还原'},'hypothetical','formal_solution')
  self.assertEqual(judged['trusted_question_scope'],'actual');self.assertNotIn('questions',judged['story'])
 def test_live_dispatch_never_reads_key_or_sends_request(self):
  with self.assertRaises(LiveHostUnavailable):dispatch_provider({'text':'问题'})
if __name__=='__main__':unittest.main(verbosity=2)
