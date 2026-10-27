# -*- coding: utf-8 -*-
"""Offline request failures. No real key, network, original ledger or player record."""
import io,json,ssl,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError

APP=Path(__file__).resolve().parents[1]/'blind_content_s08/app'
sys.path.insert(0,str(APP))
from live_host import (RoundBudget,BudgetedProvider,HostFailure,MODEL,BASE_URL,
    PRIOR_ESTIMATED_CNY,failure_diagnostic)

SECRET='FAKE_TEST_SECRET_MUST_NOT_APPEAR'
BODY={'max_completion_tokens':64}

class DiagnosticTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.serial=0
    def tearDown(self):self.temp.cleanup()
    def budget(self):
        self.serial+=1
        auth={'approved':True,'approval_basis':'OFFLINE TEST; no API access','round_id':'offline-diagnostic',
            'max_calls':24,'cap_cny':5,'model':MODEL,'base_url':BASE_URL,'expires_at':time.time()+300}
        return RoundBudget(auth,Path(self.temp.name)/(str(self.serial)+'.sqlite3'))
    def row(self,budget):
        with budget.connect() as db:
            return db.execute('SELECT status,reserved,cost,metadata FROM calls').fetchall()
    def fail(self,error):
        budget=self.budget();sender=Mock(side_effect=error);key=Mock(return_value=SECRET)
        provider=BudgetedProvider(budget,key,sender)
        with self.assertRaises(HostFailure) as caught:provider.call(BODY,'scope','offline-request')
        self.assertEqual(caught.exception.code,'provider_usage_unknown');self.assertNotIn(SECRET,str(caught.exception))
        row=self.row(budget)[0];self.assertEqual(row[:3],('unknown',2.0,None))
        self.assertNotIn(SECRET,row[3]);self.assertEqual(sender.call_count,1);self.assertEqual(key.call_count,1)
        self.assertEqual(budget.summary()['provider_calls'],1)
        self.assertAlmostEqual(budget.summary()['estimated_or_reserved_cny'],PRIOR_ESTIMATED_CNY+2)
        with self.assertRaises(HostFailure):provider.call(BODY,'scope','another-operation')
        self.assertEqual(sender.call_count,1);self.assertEqual(key.call_count,1)
        return json.loads(row[3])
    def test_timeout_is_preserved_without_exception_text_or_retry(self):
        metadata=self.fail(TimeoutError(SECRET))
        self.assertEqual(metadata['failure'],{'phase':'transport','category':'timeout'})
        self.assertEqual(len(metadata['request_sha256']),64)
    def test_http_status_is_preserved_without_headers_body_url_or_billing_assumption(self):
        for status in [401,429,503]:
            with self.subTest(status=status):
                body=io.BytesIO(SECRET.encode())
                error=HTTPError('https://'+SECRET,status,SECRET,{'Authorization':SECRET},body)
                metadata=self.fail(error)
                self.assertEqual(metadata['failure'],{'phase':'transport','category':'http_error','http_status':status})
                self.assertEqual(body.tell(),0)
    def test_nested_url_failures_are_classified_without_reading_reason_text(self):
        for reason,category in [(TimeoutError(SECRET),'timeout'),(ssl.SSLError(SECRET),'tls_error'),(OSError(SECRET),'connection_error')]:
            with self.subTest(category=category):
                self.assertEqual(self.fail(URLError(reason))['failure']['category'],category)
    def test_tls_and_connection_errors_get_distinct_labels(self):
        for error,category in [(ssl.SSLError(SECRET),'tls_error'),(ConnectionError(SECRET),'connection_error')]:
            with self.subTest(category=category):self.assertEqual(self.fail(error)['failure']['category'],category)
    def test_non_json_response_is_distinct_from_invalid_usage(self):
        self.assertEqual(self.fail(json.JSONDecodeError(SECRET,SECRET,0))['failure']['category'],'response_not_json')
    def test_expected_host_failure_codes_only_are_persisted(self):
        for code in ['response_too_large',SECRET]:
            with self.subTest(code=code):
                expected=code if code=='response_too_large' else 'unclassified_error'
                self.assertEqual(self.fail(HostFailure(code))['failure']['category'],expected)
    def test_envelope_model_and_usage_failures_have_different_phases(self):
        for data,phase,category in [
            (SECRET,'response_envelope','invalid_response_envelope'),
            ({'model':SECRET},'response_envelope','unexpected_response_model'),
            ({'model':MODEL,'usage':{}},'usage_validation','invalid_usage')]:
            with self.subTest(category=category):
                budget=self.budget();sender=Mock(return_value=data);provider=BudgetedProvider(budget,lambda:SECRET,sender)
                with self.assertRaises(HostFailure):provider.call(BODY,'scope','request')
                row=self.row(budget)[0];metadata=json.loads(row[3])
                self.assertEqual(metadata['failure'],{'phase':phase,'category':category})
                self.assertNotIn(SECRET,row[3]);self.assertEqual(row[:3],('unknown',2.,None))
                self.assertEqual(sender.call_count,1)
    def test_reservation_excess_is_not_recorded_as_zero_or_released(self):
        budget=self.budget();provider=BudgetedProvider(budget,lambda:SECRET,Mock(return_value={'model':MODEL,'usage':{}}))
        with patch('live_host.usage_cost',return_value=2.01):
            with self.assertRaises(HostFailure):provider.call(BODY,'scope','request')
        row=self.row(budget)[0]
        self.assertEqual(json.loads(row[3])['failure']['category'],'usage_exceeds_reservation')
        self.assertEqual(row[:3],('unknown',2.,None))
    def test_audit_storage_failure_still_blocks_usage_without_retry(self):
        budget=self.budget();sender=Mock(side_effect=TimeoutError(SECRET));provider=BudgetedProvider(budget,lambda:SECRET,sender)
        with patch.object(provider,'record_failure',side_effect=OSError(SECRET)):
            with self.assertRaises(HostFailure) as caught:provider.call(BODY,'scope','request')
        self.assertEqual(caught.exception.code,'provider_usage_unknown')
        self.assertEqual(self.row(budget)[0][:3],('unknown',2.,None));self.assertTrue(budget.summary()['blocked'])
        self.assertEqual(sender.call_count,1)
    def test_missing_credential_does_not_reserve_a_paid_request(self):
        budget=self.budget();sender=Mock();provider=BudgetedProvider(budget,Mock(side_effect=HostFailure('credential_required')),sender)
        with self.assertRaises(HostFailure):provider.call(BODY,'scope','request')
        self.assertEqual(self.row(budget),[]);sender.assert_not_called()
    def test_known_usage_but_malformed_content_is_settled_and_does_not_freeze_round(self):
        budget=self.budget();data={'model':MODEL,'usage':{'prompt_tokens':12,'completion_tokens':4},
            'choices':[{'finish_reason':'stop','message':{'content':'{malformed'}}]}
        sender=Mock(return_value=data);provider=BudgetedProvider(budget,lambda:SECRET,sender)
        with self.assertRaises(HostFailure) as caught:provider.call(BODY,'scope','request')
        self.assertEqual(caught.exception.code,'invalid_provider_output');row=self.row(budget)[0]
        self.assertEqual(row[0],'done');self.assertIsNotNone(row[2]);self.assertFalse(budget.summary()['blocked'])
        self.assertEqual(sender.call_count,1)
    def test_valid_output_still_returns_normally_with_recorded_usage(self):
        budget=self.budget();data={'model':MODEL,'usage':{'prompt_tokens':12,'completion_tokens':4},
            'choices':[{'finish_reason':'stop','message':{'content':'{"route":"judge_actual"}'}}]}
        sender=Mock(return_value=data);provider=BudgetedProvider(budget,lambda:SECRET,sender)
        self.assertEqual(provider.call(BODY,'scope','request'),{'route':'judge_actual'})
        row=self.row(budget)[0];self.assertEqual(row[0],'done');self.assertNotIn('failure',json.loads(row[3]))
        self.assertFalse(budget.summary()['blocked']);self.assertEqual(sender.call_count,1)
    def test_unclassified_exception_name_and_message_are_never_serialized(self):
        self.assertEqual(failure_diagnostic(ValueError(SECRET),'transport'),
            {'phase':'transport','category':'unclassified_error'})

if __name__=='__main__':unittest.main()
