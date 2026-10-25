# -*- coding: utf-8 -*-
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,time
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parent
PACK=ROOT.parent if (ROOT.parent/'测试计划.json').exists() else ROOT/'公开范围识别验证V0.1'
STATE=Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014')
BASE=Path('/Users/zhujiayi/Desktop/且曼课堂笔记/海龟汤/跨题新问法验证V0.1/执行工具/运行扩展.py')
EXPERIMENT='public_scope_gate_v01'
def dump(x):return json.dumps(x,ensure_ascii=False,indent=2)+'\n'
def read(p):return json.loads(p.read_text())
def sha(x):return hashlib.sha256(x).hexdigest()
spec=importlib.util.spec_from_file_location('base',BASE);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
def load():
 lock=read(PACK/'冻结清单.json')
 assert sha(dump(lock['sha256']).encode())==lock['signature']
 for n,h in lock['sha256'].items():assert sha((PACK/n).read_bytes())==h,n
 data=read(PACK/'模型输入/用例.json');cases={c['id']:c for c in data['cases']}
 stories={sid:read(PACK/f'模型输入/故事/{sid}.json') for sid in ['S02','S06','S07']}
 gold={g['id']:g for g in read(PACK/'人工判分/预期结果.json')['cases']}
 return lock['signature'],data['schedule'],cases,stories,gold
def scope_payload(case,story):
 # 明确构造只包含公开字段的输入，禁止传整份story快照。
 public={'public_surface':story['surface'],'public_history':case['history'],'current_message':case['current_message']}
 return {'model':b.MODEL,'messages':[{'role':'system','content':(PACK/'主持配置/公开范围识别指令.txt').read_text()},{'role':'user','content':dump(public)}],
  'response_format':read(PACK/'主持配置/范围输出结构.json'),'enable_thinking':False,'max_completion_tokens':64,'stream':False,'n':1}
def judge_payload(case,stories,scope):
 assert scope in ['actual','hypothetical']
 cfg=read(PACK/'主持配置/主持调用配置.json')
 body=b.payload(case,stories,(PACK/'主持配置/主持指令V0.1.8.txt').read_text(),cfg)
 user=json.loads(body['messages'][1]['content']);user['trusted_question_scope']=scope
 body['messages'][1]['content']=dump(user)
 return body
def render(v,schema):
 if not b.valid_schema(v,schema):raise ValueError('输出不符合严格结构与安全短句白名单')
 if v['action']=='clarify':
  if v['answer'] or not v['clarification']:raise ValueError('澄清契约错误')
  return v['clarification']
 if v['clarification']:raise ValueError('非澄清动作包含多余文本')
 return b.render(v)
def call(ledger,case,repeat,stage,body,signature,key,expected,story):
 own=[e for e in ledger['entries'] if e.get('experiment')==EXPERIMENT]
 count=sum(e['status']!='not_sent_dns' for e in ledger['entries'])
 cost=sum(e.get('estimated_cny',e.get('reserved_cny',2)) for e in ledger['entries'])
 assert ledger['max_calls']==162 and ledger['cap_cny']==5 and count==150+len(own)
 assert count<162 and len(own)<12 and cost+2<=5,'预算或累计次数保护'
 assert len(dump(body).encode())<=32000
 e={'experiment':EXPERIMENT,'signature':signature,'case_id':case['id'],'repeat':repeat,'stage':stage,'story_id':case['story_id'],
  'status':'pending','reserved_cny':2,'started_at':time.time(),'request_sha256':sha(dump(body).encode()),'semantic_review':'pending_review'}
 ledger['entries'].append(e);b.atomic(STATE/'ledger.json',ledger);started=time.monotonic()
 try:
  data,rid=b.send(body,key);choice=data['choices'][0];content=choice['message']['content'].replace(key,'[REDACTED]');fee=b.usage_cost(data['usage'])
  e.update(status='done',raw_content=content,request_id=rid or data.get('id'),actual_model=data.get('model'),usage=data['usage'],estimated_cny=fee,
   latency_ms=round((time.monotonic()-started)*1000),finish_reason=choice.get('finish_reason'))
  v=json.loads(content);e['verdict']=v;errors=[]
  schema=body['response_format']['json_schema']['schema']
  if not b.valid_schema(v,schema):errors.append('invalid_schema')
  elif stage=='scope':
   if (v['route'],v['scope']) not in [('judge','actual'),('judge','hypothetical'),('clarify','unresolved')]:errors.append('scope_contract')
   if v['route']!=expected['expected_route'] or v['scope']!=expected['expected_scope']:errors.append('scope_mismatch')
   if v['route']=='clarify' and v['scope']=='unresolved':
    e['player_display']=read(PACK/'主持配置/安全澄清模板.json')['scope']
    e['host_output']={'action':'clarify','answer':'','clarification':e['player_display'],'fact_ids':[],'covered_nodes':[],'audit_flags':[]}
  else:
   errors+=b.preliminary(v,case,story,expected,schema)
   e['player_display']=render(v,schema)
  if data.get('model')!=b.MODEL or choice.get('finish_reason')!='stop':errors.append('model_or_finish_mismatch')
  if data['usage']['prompt_tokens']>32000 or fee>2:errors.append('cost_or_input_guard')
  if choice['message'].get('reasoning_content'):errors.append('unexpected_thinking')
  e['preliminary_errors']=errors
  e['status']='needs_review' if not errors or set(errors)<={'scope_mismatch','action_or_label_mismatch'} else 'stopped'
 except (HTTPError,URLError,TimeoutError,ValueError,KeyError,TypeError) as err:
  e['status']='unknown' if 'estimated_cny' not in e else 'stopped';e['error_type']=type(err).__name__
  if isinstance(err,HTTPError):e['http_status']=err.code
 b.atomic(STATE/'ledger.json',ledger)
 print(dump({'case':case['id'],'repeat':repeat,'stage':stage,'status':e['status'],'verdict':e.get('verdict'),
  'display':e.get('player_display'),'errors':e.get('preliminary_errors'),'cumulative_attempts':count+1}),flush=True)
def main(dry=False):
 signature,schedule,cases,stories,gold=load()
 if dry:
  payloads=[]
  for item in schedule:
   c=cases[item['case_id']];payloads.append(scope_payload(c,stories[c['story_id']]))
   if gold[c['id']]['expected_route']=='judge':payloads.append(judge_payload(c,stories,gold[c['id']]['expected_scope']))
  assert len(payloads)==12 and all(len(dump(p).encode())<32000 for p in payloads)
  print(dump({'dry_run':True,'planned_requests':12,'network_calls':0,'credential_read':False}));return
 gate=STATE/'public_scope_review.json'
 if gate.exists() and read(gate).get('signature')==signature and read(gate).get('blocked'):raise RuntimeError('该配置已结束或停止，禁止继续与自动重试。')
 with (STATE/'execution.lock').open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);ledger=read(STATE/'ledger.json')
  assert not any(e['status'] in ['pending','unknown','stopped','needs_scope_review','needs_display_review','needs_review'] for e in ledger['entries']),'存在待复核或未知调用'
  own=[e for e in ledger['entries'] if e.get('experiment')==EXPERIMENT]
  assert all(e['signature']==signature for e in own)
  for item in schedule:
   c=cases[item['case_id']];prior=[e for e in own if e['case_id']==c['id'] and e['repeat']==item['repeat']]
   if not prior:
    body=scope_payload(c,stories[c['story_id']]);key=b.credential();call(ledger,c,item['repeat'],'scope',body,signature,key,gold[c['id']],stories[c['story_id']]);return
   s=next(e for e in prior if e['stage']=='scope')
   if s['status']=='reviewed_failure':continue
   assert s['status']=='done'
   if s['verdict']['route']=='clarify':continue
   if any(e['stage']=='judge' for e in prior):continue
   body=judge_payload(c,stories,s['verdict']['scope']);key=b.credential();call(ledger,c,item['repeat'],'judge',body,signature,key,gold[c['id']],stories[c['story_id']]);return
  print('本轮计划已执行完毕，没有新增调用。')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');a=p.parse_args();main(a.dry_run)
