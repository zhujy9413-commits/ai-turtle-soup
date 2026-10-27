# -*- coding: utf-8 -*-
"""Player entry/budget/cleanup tests. No credentials or paid sender is used."""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

APP=Path(__file__).resolve().parents[1]
WORK=APP.parent
sys.path[:0]=[str(APP),str(WORK),str(APP/'tests')]
from server import GameService, GameError
from host_boundary import public_scope_input,private_judge_input
from live_host import RoundBudget,HostFailure,LiveHost,temporary_key,encoded
from test_live_host import StateHost,authorization,ScriptedProvider,verdict
from test_game import Clock
from player_round import RoundLifecycle,cleanup_credential

class PlayerRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.clock=Clock()
        self.provider=ScriptedProvider([])
        self.game=GameService(self.base/'sessions.db',clock=self.clock,auto_jobs=False,
            host=StateHost(self.provider),story_ids=['S03'],single_session=True)
        self.story=self.game.stories['S03']
    def tearDown(self):
        self.game.db.close()
        self.temp.cleanup()
    def start(self):
        self.session=self.game.create('player',{'story_id':'S03','minutes':40,'attempts':40})
        return self.game.ready(self.session['id'],'player')
    def submit(self,kind,text):
        self.game.submit(self.session['id'],'player',{'kind':kind,'text':text,'request_id':uuid.uuid4().hex})
        self.game.complete_job(self.session['id'])
        return self.game.snapshot(self.session['id'],'player')
    def fake_credential(self):
        folder=Path(tempfile.mkdtemp(prefix='ai-turtle-soup-eval-offline-',dir='/private/tmp'))
        os.chmod(folder,0o700)
        path=folder/'credentials.json'
        path.write_text('{}')
        path.chmod(0o600)
        pointer=self.base/'pointer.json'
        pointer.write_text(json.dumps({'path':str(path)}))
        self.addCleanup(lambda:folder.rmdir() if folder.exists() and not list(folder.iterdir()) else None)
        self.addCleanup(lambda:path.unlink(missing_ok=True))
        return path,pointer
    def test_one_visible_story_and_other_story_cannot_be_opened(self):
        self.assertEqual([x['id'] for x in self.game.list_stories('player')],['S03'])
        for sid in ('S02','S06','S07'):
            with self.assertRaises(GameError):self.game.create('player',{'story_id':sid,'minutes':40,'attempts':40})
    def test_one_session_even_after_refresh_or_new_cookie(self):
        self.start()
        restored=GameService(self.base/'sessions.db',auto_jobs=False,story_ids=['S03'],single_session=True)
        try:
            with self.assertRaises(GameError):restored.create('other',{'story_id':'S03','minutes':40,'attempts':40})
        finally:restored.db.close()
    def test_player_settings_match_one_round_and_cannot_expand_budget(self):
        self.assertEqual(self.game.runtime()['player_limits'],{'minutes':40,'attempts':40})
        for settings in ({'minutes':41,'attempts':40},{'minutes':40,'attempts':41}):
            with self.assertRaises(GameError):self.game.create('player',{'story_id':'S03',**settings})
    def test_before_reveal_never_publishes_truth_or_unasked_hints(self):
        session=self.start()
        for key in ('truth','facts','nodes','solutions','questions','host_audit','temporal_model'):
            self.assertNotIn(key,session)
        self.assertEqual(session['hints'],[])
        self.assertNotIn('梦',json.dumps(self.game.list_stories('player'),ensure_ascii=False))
        self.assertNotIn('梦',session['surface'])
    def test_scope_has_only_public_text_and_judge_has_no_future_hints_or_gold(self):
        current={'text':'她是为了救我吗？'}
        scope=public_scope_input(self.story,[],current)
        self.assertEqual(set(scope),{'public_surface','public_history','current_message'})
        private=private_judge_input(self.story,[],current,'actual')
        for key in ('hints','questions','solutions','starters'):
            self.assertNotIn(key,private['story'])
        self.assertLess(len(encoded(private)),16000)
    def test_four_core_nodes_and_fixed_three_hints_without_provider(self):
        self.start()
        self.assertEqual({x[0] for x in self.story['nodes']},{'N1','N2','N3','N4'})
        for count in range(1,4):
            session=self.submit('hint','')
            self.assertEqual(session['hint_count'],count)
            self.assertEqual(session['attempts_left'],40)
        with self.assertRaises(GameError):self.submit('hint','')
        self.assertEqual(self.provider.calls,[])
    def test_partial_solution_continues_and_complete_connects_all_nodes(self):
        self.start()
        self.provider.responses.extend([verdict('incomplete','',nodes=['N3']),
            verdict('complete','',facts=['F01','F03','F06','F10','F11','F12'],nodes=['N1','N2','N3','N4'])])
        partial=self.submit('solution','女儿救了我。')
        self.assertEqual(partial['status'],'active')
        self.assertNotIn('truth',partial)
        self.assertEqual(partial['attempts_left'],39)
        complete=self.submit('solution',self.story['solutions'][0]['text'])
        self.assertEqual(complete['end_reason'],'success')
        self.game.advance_end(self.session['id']);self.game.advance_end(self.session['id'])
        final=self.game.snapshot(self.session['id'],'player')
        self.assertEqual(final['status'],'completed')
        self.assertEqual(len(final['review']['causes']),4)
    def test_new_round_carries_prior_cost_and_hard_call_cap(self):
        b=RoundBudget(authorization(max_calls=100),self.base/'ledger.db')
        self.assertAlmostEqual(b.summary()['prior_estimated_cny'],0.1377942)
        for i in range(100):
            b.reserve(str(i),'judge','test');b.settle(str(i),'judge',.0001,{})
        self.assertEqual(b.summary()['provider_calls'],100)
        with self.assertRaises(HostFailure):b.ensure_capacity()
        for count in (0,101):
            with self.assertRaises(HostFailure):RoundBudget(authorization(max_calls=count),self.base/'invalid.db')
    def test_unsigned_plan_never_starts_ledger(self):
        pending=json.loads((APP/'private/host/authorization.example.json').read_text())
        with self.assertRaises(HostFailure):RoundBudget(pending,self.base/'unapproved.db')
        self.assertFalse((self.base/'unapproved.db').exists())
    def test_new_one_hour_temp_key_and_old_half_hour_limit(self):
        fake_stat=Mock(st_uid=os.getuid(),st_mode=0o700)
        def lstat(path):
            return Mock(st_uid=os.getuid(),st_mode=0o100600 if path.name=='credentials.json' else 0o40700)
        pointer={'path':'/private/tmp/ai-turtle-soup-eval-offline/credentials.json'}
        secret={'api_key':'OFFLINE_FAKE','region':'cn-beijing','created_at':1000,'ttl_seconds':3600}
        def read(path,*a,**kw):return json.dumps(secret if path.name=='credentials.json' else pointer)
        with patch.object(Path,'read_text',read),patch.object(Path,'lstat',lstat),patch('live_host.time.time',return_value=3000):
            self.assertEqual(temporary_key(),'OFFLINE_FAKE')
            del secret['ttl_seconds']
            with self.assertRaises(HostFailure):temporary_key()
            secret['ttl_seconds']=3601
            with self.assertRaises(HostFailure):temporary_key()
            secret['ttl_seconds']=3600;secret['created_at']=-1000
            with self.assertRaises(HostFailure):temporary_key()
    def test_completed_session_closes_budget_and_cleans_only_captured_key(self):
        self.start()
        self.game.interrupt(self.session['id'],'player')
        path,pointer=self.fake_credential()
        budget=RoundBudget(authorization(),self.base/'ledger.db')
        life=RoundLifecycle(self.game,budget,2000,str(path),self.base/'cleanup.json',pointer,self.clock)
        life.tick()
        self.assertTrue(life.finished)
        self.assertTrue(budget.summary()['blocked'])
        self.assertFalse(path.exists());self.assertFalse(pointer.exists())
        self.assertTrue(json.loads((self.base/'cleanup.json').read_text())['temporary_key_removed'])
    def test_wall_clock_expiry_cleans_without_player_request(self):
        path,pointer=self.fake_credential()
        budget=RoundBudget(authorization(),self.base/'ledger.db')
        life=RoundLifecycle(self.game,budget,1001,str(path),self.base/'cleanup.json',pointer,self.clock)
        life.tick();self.assertFalse(life.finished)
        self.clock.advance(2);life.tick()
        self.assertFalse(path.exists())
        self.assertEqual(json.loads((self.base/'cleanup.json').read_text())['reason'],'round_expired')
    def test_cleanup_preserves_a_later_round_pointer(self):
        path,pointer=self.fake_credential()
        pointer.write_text(json.dumps({'path':'/private/tmp/a-later-round/credentials.json'}))
        result=cleanup_credential(str(path),pointer)
        self.assertTrue(result['temporary_key_removed'])
        self.assertFalse(result['pointer_removed']);self.assertTrue(pointer.exists())

    def test_expiry_during_processing_freezes_session_and_drops_late_job(self):
        self.start()
        self.clock.advance(10)
        self.game.submit(self.session['id'],'player',{'kind':'question','text':'处理中到期','request_id':uuid.uuid4().hex})
        path,pointer=self.fake_credential()
        budget=RoundBudget(authorization(),self.base/'ledger.db')
        life=RoundLifecycle(self.game,budget,1011,str(path),self.base/'cleanup.json',pointer,self.clock)
        self.clock.advance(2);life.tick()
        paused=self.game.snapshot(self.session['id'],'player')
        self.assertEqual(paused['status'],'paused');self.assertEqual(paused['remaining_seconds'],2390)
        self.clock.advance(5000);self.game.complete_job(self.session['id'])
        final=self.game.snapshot(self.session['id'],'player')
        self.assertEqual(final['status'],'paused');self.assertEqual(final['remaining_seconds'],2390)
        self.assertEqual(final['attempts_left'],40);self.assertNotIn('truth',final)
        self.assertEqual(self.provider.calls,[]);self.assertFalse(path.exists())

if __name__=='__main__':unittest.main()
