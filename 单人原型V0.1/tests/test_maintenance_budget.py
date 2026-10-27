import copy,tempfile,time,unittest
from pathlib import Path
from live_host import RoundBudget,HostFailure,BudgetedProvider,resume_completed_maintenance
from test_live_host import authorization
from unittest.mock import Mock

class MaintenanceBudgetTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'ledger.db'
  self.old=authorization(max_calls=100,materials_sha256='old')
  self.budget=RoundBudget(self.old,self.path)
  for i in range(4):self.budget.reserve(str(i),'judge','hash');self.budget.settle(str(i),'judge',.001,{})
  self.budget.stop('round_completed')
  self.expiry=time.time()+3500
  self.new={**self.old,'review_revision':1,'materials_sha256':'new','expires_at':self.expiry,'phase_call_ceiling':6,
   'maintenance_approval':{'approved':True,'basis':'OFFLINE explicit maintenance approval only','max_new_calls':2}}
 def tearDown(self):self.temp.cleanup()
 def test_fresh_explicit_approval_preserves_counts_costs_and_adds_a_smaller_phase_ceiling(self):
  cost=self.budget.summary()['estimated_or_reserved_cny']
  resume_completed_maintenance(self.old,self.new,self.path,self.expiry)
  b=RoundBudget(self.new,self.path);s=b.summary()
  self.assertEqual(s['provider_calls'],4);self.assertEqual(s['remaining_authorized_provider_calls'],2)
  self.assertEqual(s['remaining_in_original_round'],96);self.assertEqual(s['estimated_or_reserved_cny'],cost)
  for i in range(2):b.reserve('maintenance'+str(i),'judge','hash');b.settle('maintenance'+str(i),'judge',.001,{})
  with self.assertRaises(HostFailure):b.ensure_capacity()
 def test_closed_round_is_not_reopened_by_generic_resume_or_missing_approval(self):
  bad=copy.deepcopy(self.new);bad['maintenance_approval']['approved']=False
  with self.assertRaises(HostFailure):resume_completed_maintenance(self.old,bad,self.path,self.expiry)
  self.assertTrue(self.budget.summary()['blocked']);self.assertEqual(self.budget.summary()['provider_calls'],4)
 def test_approval_cannot_expand_original_call_count_or_cost_cap(self):
  for changed in ({'max_calls':101},{'cap_cny':6},{'phase_call_ceiling':100}):
   with self.assertRaises(HostFailure):resume_completed_maintenance(self.old,{**self.new,**changed},self.path,self.expiry)
 def test_unknown_usage_blocks_even_with_new_explicit_approval(self):
  with self.budget.connect() as db:db.execute("UPDATE calls SET status='unknown',cost=NULL WHERE operation='0'")
  with self.assertRaises(HostFailure):resume_completed_maintenance(self.old,self.new,self.path,self.expiry)
 def test_zero_remaining_phase_calls_reject_before_credentials_or_network(self):
  resume_completed_maintenance(self.old,self.new,self.path,self.expiry);b=RoundBudget(self.new,self.path)
  for i in range(2):b.reserve('new'+str(i),'judge','hash');b.settle('new'+str(i),'judge',.001,{})
  key,sender=Mock(),Mock();provider=BudgetedProvider(b,key,sender)
  with self.assertRaises(HostFailure):provider.call({'max_completion_tokens':64},'scope','extra')
  key.assert_not_called();sender.assert_not_called()
