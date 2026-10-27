# -*- coding: utf-8 -*-
"""Offline adapter/integration tests. All provider responses are explicit test doubles."""
import copy
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_host import (LiveHost, HostFailure, RoundBudget, BudgetedProvider, MODEL, BASE_URL,
                       PRIOR_ESTIMATED_CNY, public_history, load_live_host, resume_reviewed_revision,public_reading_guard)
from server import GameService, GameError
from test_game import Clock


def verdict(action='answer', answer='是', clarification='', facts=None, nodes=None, flags=None):
    return {'action': action, 'answer': answer, 'clarification': clarification,
            'fact_ids': facts or [], 'covered_nodes': nodes or [], 'review_refs':[], 'audit_flags': flags or []}


def authorization(**kw):
    return {'approved': True, 'approval_basis': 'OFFLINE TEST ONLY; no provider access',
            'round_id': 'offline-test-01', 'max_calls': 24, 'cap_cny': 5,
            'model': MODEL, 'base_url': BASE_URL, 'expires_at': time.time() + 3600, **kw}


class ScriptedProvider:
    """No urllib, API credentials, or real provider budget is used by this test double."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.stopped = []
        self.budget = self

    def ensure_capacity(self, slots=1):
        if self.stopped:
            raise HostFailure('round_stopped')

    def stop(self, reason):
        self.stopped.append(reason)

    def summary(self):
        return {'provider_calls': len(self.calls), 'remaining_authorized_provider_calls': 24-len(self.calls),
                'blocked': bool(self.stopped), 'test_double': True}

    def call(self, body, stage, operation):
        self.calls.append((copy.deepcopy(body), stage, operation))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class StateHost(LiveHost):
    """State-machine fixture only. This bypasses semantic assessment deliberately.

    Evidence protocol and actual LiveHost are tested separately in test_evidence_contract.
    These fixtures verify timers, reservation, idempotency and recovery, not model accuracy.
    """
    def judge(self,story,history,message,kind,operation):
        self.available(kind)
        if kind=='question':
            route=self.provider.call({},'scope',operation)
            if route not in ({'route':'judge_actual'},{'route':'judge_hypothetical'}):raise HostFailure('invalid_scope')
        result=self.provider.call({},'judge',operation)
        if any(x not in __import__('host_feedback').public_references(history) for x in result.get('review_refs',[])):
            raise HostFailure('unsupported_assessment')
        return {**result,'scope':'hypothetical' if kind=='question' and route['route']=='judge_hypothetical' else 'actual'}


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'ledger.sqlite3'
        self.auth = authorization()

    def tearDown(self):
        self.temp.cleanup()

    def test_no_approval_no_budget_file_and_no_credentials(self):
        with self.assertRaises(HostFailure):
            RoundBudget(authorization(approved=False), self.path)
        self.assertFalse(self.path.exists())
        with self.assertRaises(HostFailure):
            load_live_host(Path(self.temp.name)/'absent.json', self.temp.name)

    def test_exhaustion_checked_before_key_read_or_network(self):
        b = RoundBudget(self.auth, self.path)
        b.stop('test')
        key, sender = Mock(), Mock()
        p = BudgetedProvider(b, key, sender)
        with self.assertRaises(HostFailure):
            p.call({}, 'judge', 'op')
        key.assert_not_called();sender.assert_not_called()

    def test_request_count_durable_and_authorization_cannot_reset_it(self):
        b = RoundBudget(self.auth, self.path)
        b.reserve('op', 'scope', 'hash');b.settle('op', 'scope', .001, {})
        restored = RoundBudget(self.auth, self.path)
        self.assertEqual(restored.summary()['provider_calls'], 1)
        self.assertAlmostEqual(restored.summary()['estimated_or_reserved_cny'], PRIOR_ESTIMATED_CNY+.001)
        with self.assertRaises(HostFailure):
            RoundBudget({**self.auth, 'max_calls': 23}, self.path)

    def test_unknown_request_keeps_reservation_and_prevents_retry_after_restart(self):
        b = RoundBudget(self.auth, self.path)
        b.reserve('op', 'scope', 'hash')
        restored = RoundBudget(self.auth, self.path);restored.recover_uncertain()
        self.assertTrue(restored.summary()['blocked'])
        self.assertAlmostEqual(restored.summary()['estimated_or_reserved_cny'], PRIOR_ESTIMATED_CNY+2)
        with self.assertRaises(HostFailure):restored.ensure_capacity()

    def test_reviewed_revision_preserves_count_and_cannot_expand_approval(self):
        old={**self.auth,'materials_sha256':'old'}
        b=RoundBudget(old,self.path);b.reserve('op','scope','hash');b.settle('op','scope',.001,{})
        b.stop('integration_needs_review')
        revised={**old,'materials_sha256':'new','review_revision':1}
        with self.assertRaises(HostFailure):resume_reviewed_revision(old,{**revised,'max_calls':23},self.path,'review')
        resume_reviewed_revision(old,revised,self.path,'Verified semantic fix; preserve prior requests and cost')
        restored=RoundBudget(revised,self.path)
        with self.assertRaises(HostFailure):b.ensure_capacity()
        self.assertEqual(restored.summary()['provider_calls'],1)
        self.assertEqual(restored.summary()['remaining_authorized_provider_calls'],23)
        self.assertFalse(restored.summary()['blocked'])

    def test_unknown_usage_cannot_be_released_by_reviewed_revision(self):
        old={**self.auth,'materials_sha256':'old'}
        b=RoundBudget(old,self.path);b.reserve('op','scope','hash');b.stop('integration_needs_review','op','scope')
        with self.assertRaises(HostFailure):
            resume_reviewed_revision(old,{**old,'materials_sha256':'new','review_revision':1},self.path,'review')

    def test_two_phase_question_needs_two_remaining_provider_requests(self):
        auth=authorization(max_calls=1)
        b=RoundBudget(auth,self.path)
        with self.assertRaises(HostFailure):b.ensure_capacity(2)
        b.ensure_capacity(1)

    def test_conservative_cost_reservation_and_expiry(self):
        b=RoundBudget(authorization(cap_cny=2),self.path)
        with self.assertRaises(HostFailure):b.ensure_capacity()
        other=RoundBudget(authorization(round_id='expired-round',expires_at=time.time()-1),self.path)
        with self.assertRaises(HostFailure):other.ensure_capacity()

    def test_transport_error_is_one_call_no_retry_and_no_secret_logged(self):
        b=RoundBudget(self.auth,self.path)
        sender=Mock(side_effect=TimeoutError('PRIVATE_KEY'))
        p=BudgetedProvider(b,lambda:'PRIVATE_KEY',sender)
        with self.assertRaises(HostFailure) as caught:p.call({'max_completion_tokens':64},'scope','op')
        self.assertNotIn('PRIVATE_KEY',str(caught.exception))
        self.assertEqual(sender.call_count,1)
        with self.assertRaises(HostFailure):p.call({},'scope','op2')
        self.assertEqual(sender.call_count,1)
        with b.connect() as db:rows=db.execute('SELECT metadata FROM calls').fetchall()
        self.assertNotIn('PRIVATE_KEY',str(rows))

    def test_usage_model_and_finish_checked_before_output(self):
        for change in [{'model':'different'}, {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}, {'usage':{}}]:
            with self.subTest(change=change):
                path=Path(self.temp.name)/(uuid.uuid4().hex+'.db')
                b=RoundBudget(self.auth,path)
                data={'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
                      'usage':{'prompt_tokens':100,'completion_tokens':30},**change}
                sender=Mock(return_value=data)
                with self.assertRaises(HostFailure):BudgetedProvider(b,lambda:'test',sender).call({'max_completion_tokens':64},'scope','op')
                self.assertEqual(b.summary()['blocked'],change.get('choices',[{}])[0].get('finish_reason')!='length')

    def test_valid_response_settles_cost_and_tolerates_documented_ten_token_margin(self):
        b=RoundBudget(self.auth,self.path)
        data={'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':'{"route":"judge","scope":"actual"}'}}],
              'usage':{'prompt_tokens':100,'completion_tokens':74}}
        p=BudgetedProvider(b,lambda:'test',lambda body,key:data)
        self.assertEqual(p.call({'max_completion_tokens':64},'scope','op')['route'],'judge')
        self.assertFalse(b.summary()['blocked'])

    def test_oversized_context_rejected_without_budget_or_credential_use(self):
        b=RoundBudget(self.auth,self.path);key=Mock();sender=Mock()
        with self.assertRaises(HostFailure):BudgetedProvider(b,key,sender).call({'text':'a'*32001},'judge','op')
        key.assert_not_called();sender.assert_not_called()
        self.assertEqual(b.summary()['provider_calls'],0)


class LiveGameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.clock=Clock()
        self.provider=ScriptedProvider([]);self.host=StateHost(self.provider)
        self.game=GameService(Path(self.temp.name)/'game.db',clock=self.clock,auto_jobs=False,host=self.host)
        self.s=self.game.create('owner',{'story_id':'S02','minutes':1,'attempts':5})
        self.id=self.s['id'];self.game.ready(self.id,'owner')

    def tearDown(self):
        self.game.db.close();self.temp.cleanup()

    def submit(self,kind='question',text='这是自由问法吗',**kw):
        return self.game.submit(self.id,'owner',{'kind':kind,'text':text,'request_id':uuid.uuid4().hex,**kw})

    def view(self):return self.game.snapshot(self.id,'owner')

    def test_real_mode_has_no_client_override_and_hint_needs_no_provider_call(self):
        for args in [{'demo_outcome':'complete'},{'fault':'timeout'}]:
            with self.assertRaises(GameError):self.submit(kind='solution',**args)
        with self.assertRaises(GameError):self.game.reveal(self.id,'owner',True,True)
        self.submit(kind='hint',text='');self.game.complete_job(self.id)
        self.assertEqual(self.view()['hint_count'],1)
        self.assertEqual(len(self.provider.calls),0)

    def test_answer_charges_once_and_idempotent_resubmit_uses_no_extra_provider_call(self):
        self.provider.responses=[{'route':'judge_actual'},verdict(facts=['F01'])]
        rid=uuid.uuid4().hex;data={'kind':'question','text':'自由问题','request_id':rid}
        self.game.submit(self.id,'owner',data);self.game.complete_job(self.id)
        s=self.game.submit(self.id,'owner',data)
        self.assertEqual(s['attempts_left'],4);self.assertEqual(s['last_request_id'],rid)
        self.assertEqual(len(self.provider.calls),2)
        self.assertNotIn('host_audit',s)
        self.assertNotIn('fact_ids',s['messages'][-1])

    def test_clarification_invalid_decline_and_failure_do_not_charge(self):
        for output in [verdict('clarify','','你指的是哪方面的互换？'),verdict('invalid',''),verdict('decline',''),HostFailure('credential_required')]:
            self.provider.responses=[{'route':'judge_actual'},output]
            self.submit();self.clock.advance(20);self.game.complete_job(self.id)
            self.assertEqual(self.view()['attempts_left'],5)
            self.assertEqual(self.view()['remaining_seconds'],60)
            self.assertEqual(self.view()['status'],'paused' if isinstance(output,Exception) else 'active')

    def test_last_attempt_solution_success_before_exhaustion(self):
        s=self.game._load(self.id);s['attempts_left']=1;self.game._save(s)
        self.provider.responses=[verdict('complete','',nodes=['N1','N2','N3'])]
        self.submit(kind='solution',text='完整同义还原');self.game.complete_job(self.id)
        self.assertEqual(self.view()['end_reason'],'success')
        self.assertEqual(len(self.provider.calls),1)

    def test_incomplete_solution_charges_without_exposing_missing_nodes(self):
        self.provider.responses=[verdict('incomplete','',nodes=['N1'])]
        self.submit(kind='solution',text='一部分');self.game.complete_job(self.id)
        s=self.view();self.assertEqual(s['attempts_left'],4)
        self.assertNotIn('N2',s['messages'][-1]['text']);self.assertNotIn('truth',s)

    def test_slow_host_does_not_block_polls_or_duplicate_workers_and_freezes_timer(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def slow(*args):
            calls.append(1);entered.set();release.wait(5)
            return {**verdict(),'scope':'actual'}
        self.host.judge=slow
        self.submit();worker=threading.Thread(target=self.game.complete_job,args=(self.id,));worker.start()
        self.assertTrue(entered.wait(2))
        try:
            self.clock.advance(45)
            read=threading.Thread(target=lambda:calls.append(self.view()['remaining_seconds']))
            read.start();read.join(.5)
            self.assertFalse(read.is_alive(),'Polling blocked behind provider I/O')
            self.game.complete_job(self.id)
            self.assertEqual(calls,[1,60])
        finally:release.set();worker.join(2)
        self.assertEqual(self.view()['remaining_seconds'],60)
        self.assertEqual(self.view()['attempts_left'],4)

    def test_restart_does_not_retry_and_preserves_freeze_and_actual_session_mode(self):
        self.submit();self.clock.advance(40)
        other=GameService(Path(self.temp.name)/'game.db',clock=self.clock,auto_jobs=False,host=self.host)
        try:
            s=other.snapshot(self.id,'owner');self.assertEqual(s['mode'],'live')
            self.assertEqual(s['status'],'paused');self.assertTrue(s['frozen']);self.assertEqual(s['attempts_left'],5)
            self.assertEqual(s['remaining_seconds'],60);self.assertEqual(len(self.provider.calls),0)
        finally:other.db.close()


if __name__=='__main__':unittest.main(verbosity=2)
