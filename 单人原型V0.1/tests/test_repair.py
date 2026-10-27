# -*- coding: utf-8 -*-
"""Incident regression: natural help, first surface, single failure, recovery, public evidence."""
import copy,json,sqlite3,tempfile,time,unittest,uuid,sys
from pathlib import Path
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from server import GameService,GameError
from live_host import LiveHost,HostFailure,RoundBudget,BudgetedProvider,MODEL,public_history,resume_reviewed_revision
from host_feedback import local_intent,public_references,help_text
from test_live_host import StateHost,ScriptedProvider,verdict,authorization
from test_game import Clock

class IncidentTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.clock=Clock()
  self.provider=ScriptedProvider([]);self.host=StateHost(self.provider)
  self.game=GameService(self.base/'sessions.db',clock=self.clock,auto_jobs=False,host=self.host,story_ids=['S03'])
  self.s=self.game.create('owner',{'story_id':'S03','minutes':40,'attempts':40});self.id=self.s['id']
 def tearDown(self):self.game.db.close();self.temp.cleanup()
 def start(self):return self.game.ready(self.id,'owner')
 def submit(self,text,kind='question',rid=None):
  return self.game.submit(self.id,'owner',{'kind':kind,'text':text,'request_id':rid or uuid.uuid4().hex})
 def view(self):return self.game.snapshot(self.id,'owner')
 def finish(self):self.game.complete_job(self.id);return self.view()
 def test_opening_stays_untimed_across_long_wait_and_refresh(self):
  self.clock.advance(5000);self.assertEqual(self.view()['remaining_seconds'],2400);self.assertEqual(self.view()['status'],'opening')
  other=GameService(self.base/'sessions.db',clock=self.clock,auto_jobs=False,host=self.host)
  try:self.assertEqual(other.snapshot(self.id,'owner')['status'],'opening')
  finally:other.db.close()
 def test_natural_progress_requests_do_not_touch_provider_or_charge(self):
  self.start()
  for text in ['哪里尚未还原完整','哪里尚未完整','哪里不完整','我的还原还缺什么','还差什么','这段答案为什么没通过','怎么玩']:
   with self.subTest(text=text):
    self.assertIsNotNone(local_intent(text))
    view=self.submit(text);self.assertEqual(view['status'],'active');self.assertEqual(view['attempts_left'],40)
  self.assertEqual(self.provider.calls,[])
 def test_help_is_idempotent_and_available_while_paused(self):
  self.start();s=self.game._load(self.id);self.game._pause(s,'credential_required')
  rid=uuid.uuid4().hex;self.submit('哪里不完整',rid=rid);count=len(self.view()['messages'])
  self.submit('哪里不完整',rid=rid);self.assertEqual(len(self.view()['messages']),count)
  self.clock.advance(5000);s=self.view();self.assertEqual(s['status'],'paused');self.assertEqual(s['remaining_seconds'],2400)
  self.assertNotIn('truth',s);self.assertEqual(self.provider.calls,[])
 def test_text_hint_consumes_one_layer_and_never_a_fourth(self):
  self.start()
  for i in range(3):self.assertEqual(self.submit('给我一点提示')['hint_count'],i+1)
  view=self.submit('提示一下');self.assertEqual(view['hint_count'],3);self.assertEqual(view['attempts_left'],40)
  self.assertEqual(self.provider.calls,[])
 def test_invalid_scope_is_single_failure_and_next_question_works(self):
  self.start();self.provider.responses=[{'route':'judge','scope':'unresolved','clarification_kind':''}]
  self.submit('离线故障复现');s=self.finish()
  self.assertEqual(s['status'],'active');self.assertEqual(s['attempts_left'],40);self.assertFalse(self.provider.stopped)
  self.provider.responses=[{'route':'judge_actual'},verdict(facts=['F01'])]
  self.submit('女儿已经去世了吗？');s=self.finish();self.assertEqual(s['attempts_left'],39)
 def test_critical_failure_pauses_and_cannot_time_out_or_reveal(self):
  self.start();self.clock.advance(20)
  self.provider.responses=[{'route':'judge_actual'},HostFailure('provider_usage_unknown')]
  self.submit('离线网络错误');self.clock.advance(10);s=self.finish()
  self.assertEqual(s['status'],'paused');self.assertTrue(s['frozen']);self.assertEqual(s['remaining_seconds'],2380)
  self.clock.advance(5000);s=self.view();self.assertEqual(s['remaining_seconds'],2380);self.assertNotIn('truth',s)
  self.assertEqual(s['attempts_left'],40)
 def test_resume_does_not_replay_failed_request(self):
  self.start();self.game._pause(self.game._load(self.id),'maintenance');self.clock.advance(999)
  s=self.game.resume(self.id,'owner');self.assertEqual(s['status'],'active');self.assertEqual(s['remaining_seconds'],2400)
  self.assertEqual(self.provider.calls,[])
 def test_definite_rejection_carries_paused_view(self):
  self.start();self.provider.stop('unknown_usage')
  with self.assertRaises(GameError) as caught:self.submit('另一个问题')
  error=caught.exception;self.assertEqual(error.status,503);self.assertEqual(error.session['status'],'paused')
  self.assertEqual(self.view()['attempts_left'],40)
 def test_resolve_absent_request_cancels_late_arrival_without_a_call(self):
  self.start();rid=uuid.uuid4().hex
  resolved=self.game.resolve_request(self.id,'owner',rid)
  self.assertFalse(resolved['accepted']);self.assertTrue(resolved['cancelled'])
  with self.assertRaises(GameError):self.submit('迟到的请求',rid=rid)
  self.assertEqual(self.view()['attempts_left'],40);self.assertEqual(self.provider.calls,[])
 def test_resolve_accepted_request_does_not_resubmit(self):
  self.start();rid=uuid.uuid4().hex;self.submit('一个问题',rid=rid)
  state=self.game.resolve_request(self.id,'owner',rid);self.assertTrue(state['accepted']);self.assertFalse(state['cancelled'])
  self.assertEqual(len(self.view()['messages']),1);self.assertEqual(self.provider.calls,[])
 def test_incomplete_feedback_cites_only_public_confirmed_record(self):
  self.start();self.provider.responses=[{'route':'judge_actual'},verdict(facts=['F06'])]
  self.submit('我醒来女儿就消失');self.finish()
  answer=self.view()['messages'][-1];out=verdict('incomplete','',nodes=['N3']);out['review_refs']=[answer['id']]
  self.provider.responses=[out];self.submit('我说清了火灾。',kind='solution');s=self.finish()
  self.assertIn('我醒来女儿就消失',s['messages'][-1]['text']);self.assertIn('此前回答：是',s['messages'][-1]['text'])
  self.assertNotIn('N1',s['messages'][-1]['text']);self.assertNotIn('truth',s)
 def test_incomplete_and_conflict_have_distinct_feedback(self):
  self.start();texts=[]
  for action in ['incomplete','conflict']:
   self.provider.responses=[verdict(action,'')];self.submit('片段解释',kind='solution');texts.append(self.finish()['messages'][-1]['text'])
  self.assertNotEqual(*texts)
 def test_hidden_reference_is_rejected_without_stopping_round(self):
  self.start();out=verdict('incomplete','');out['review_refs']=['UNPUBLISHED_HINT']
  self.provider.responses=[out];self.submit('一段还原',kind='solution');s=self.finish()
  self.assertEqual(s['status'],'active');self.assertEqual(s['attempts_left'],40);self.assertNotIn('UNPUBLISHED_HINT',s['messages'][-1]['text'])
  self.assertFalse(self.provider.stopped)
 def test_solution_invalid_copy_does_not_request_yes_no_question(self):
  self.start();self.provider.responses=[verdict('invalid','')];self.submit('一段无效内容',kind='solution')
  self.assertNotIn('提交一个是非问题',self.finish()['messages'][-1]['text'])
 def test_correction_supersedes_old_answer_in_public_reference_catalog(self):
  messages=[{'id':'q1','role':'player','kind':'question','text':'我每晚都能梦到女儿'},
    {'id':'a1','role':'host','kind':'answer','text':'否','scope':'actual','question_id':'q1','corrected_by':'c1'},
    {'id':'c1','role':'host','kind':'correction','text':'更正：应为是','answer':'是','scope':'actual','question_id':'q1'}]
  history=public_history(messages);refs=public_references(history)
  self.assertNotIn('a1',refs);self.assertEqual(refs['c1']['answer'],'是');self.assertTrue(history[1]['superseded'])
 def test_frequency_matches_surface_and_old_question_fixture(self):
  story=self.game.stories['S03'];self.assertIn('每晚',story['surface']);self.assertIn('每晚',story['facts']['F02'])
  row=next(q for q in story['questions'] if q[0]=='我每晚都能梦到女儿');self.assertEqual(row[1],'是')
 def test_first_person_sleeper_waking_and_rescue_are_consistent(self):
  story=self.game.stories['S03'];truth=''.join(story['truth'])
  self.assertIn('当我熟睡',truth);self.assertIn('推我往门外走',truth);self.assertIn('梦中女儿救了我',truth)
  self.assertIn('避免叫醒我',story['nodes'][1][1])
  for phrase in ('当她熟睡','推她往门外','梦中女儿救了她','推动她逃生','叫醒我让她逃出去'):
   self.assertNotIn(phrase,truth+json.dumps(story['solutions'],ensure_ascii=False))
 def test_router_has_single_enum_state_without_cross_field_combinations(self):
  schema=self.host.scope_format['json_schema']['schema'];self.assertEqual(list(schema['properties']),['route'])
  self.assertIn('help_progress',schema['properties']['route']['enum'])

class BillingRecoveryTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def test_known_bad_output_is_settled_and_next_request_can_continue(self):
  budget=RoundBudget(authorization(),self.base/'billing.db')
  response={'model':MODEL,'usage':{'prompt_tokens':100,'completion_tokens':30},'choices':[{'finish_reason':'stop','message':{'content':'NOT JSON'}}]}
  sender=Mock(return_value=response);provider=BudgetedProvider(budget,lambda:'OFFLINE',sender)
  with self.assertRaises(HostFailure):provider.call({'max_completion_tokens':64},'scope','first')
  self.assertEqual(sender.call_count,1);self.assertFalse(budget.summary()['blocked']);self.assertEqual(budget.summary()['provider_calls'],1)
  response['choices'][0]['message']['content']='{"route":"judge_actual"}'
  self.assertEqual(provider.call({'max_completion_tokens':64},'scope','second')['route'],'judge_actual')
 def test_unknown_usage_preserves_reserve_and_blocks(self):
  budget=RoundBudget(authorization(),self.base/'billing.db');sender=Mock(side_effect=TimeoutError())
  with self.assertRaises(HostFailure):BudgetedProvider(budget,lambda:'OFFLINE',sender).call({},'scope','first')
  self.assertEqual(sender.call_count,1);self.assertTrue(budget.summary()['blocked'])
  with budget.connect() as db:self.assertEqual(db.execute('SELECT status,cost FROM calls').fetchone(),('unknown',None))
 def test_cleanup_cannot_overwrite_original_stop_reason(self):
  budget=RoundBudget(authorization(),self.base/'billing.db');budget.stop('provider_usage_unknown');budget.stop('round_stopped')
  self.assertEqual(budget.summary()['stop_reason'],'provider_usage_unknown')
 def test_explicit_credential_window_recovery_keeps_count_cost_and_cap(self):
  old=authorization(materials_sha256='old');budget=RoundBudget(old,self.base/'billing.db')
  budget.reserve('old-request','scope','test');budget.settle('old-request','scope',.001,{})
  budget.stop('round_stopped');expiry=time.time()+3500
  revised={**old,'materials_sha256':'new','review_revision':1,'expires_at':expiry}
  resume_reviewed_revision(old,revised,self.base/'billing.db','Known settled incident, explicit maintenance recovery',expiry)
  new=RoundBudget(revised,self.base/'billing.db');self.assertEqual(new.summary()['provider_calls'],1);self.assertEqual(new.summary()['remaining_authorized_provider_calls'],23)
  self.assertEqual(new.summary()['prior_estimated_cny'],budget.summary()['prior_estimated_cny'])
  with self.assertRaises(HostFailure):resume_reviewed_revision(revised,{**revised,'max_calls':100,'review_revision':2,'materials_sha256':'another'},self.base/'billing.db','Cannot expand approved count')

if __name__=='__main__':unittest.main()
