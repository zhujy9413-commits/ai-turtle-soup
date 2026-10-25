# -*- coding: utf-8 -*-
from pathlib import Path
import argparse, fcntl, hashlib, importlib.util, json, time
from urllib.error import HTTPError, URLError
ROOT=Path(__file__).resolve().parent
PACK=ROOT.parent if (ROOT.parent/'测试计划.json').exists() else ROOT/'澄清防剧透验证V0.1'
STATE=Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014')
SOURCE=Path('/Users/zhujiayi/Desktop/且曼课堂笔记/海龟汤/跨题新问法验证V0.1')
EXPERIMENT='clarification_guard_v01'
spec=importlib.util.spec_from_file_location('base',SOURCE/'执行工具/运行扩展.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def read(p):return json.loads(p.read_text())
def dump(x):return json.dumps(x,ensure_ascii=False,indent=2)+'\n'
def load():
 lock=read(PACK/'冻结清单.json')
 for name,h in lock['sha256'].items():assert hashlib.sha256((PACK/name).read_bytes()).hexdigest()==h,name
 assert hashlib.sha256(dump(lock['sha256']).encode()).hexdigest()==lock['signature']
 data=read(PACK/'模型输入/用例.json');cases={c['id']:c for c in data['cases']}
 stories={sid:read(PACK/f'模型输入/故事/{sid}.json') for sid in ['S02','S06','S07']}
 prompt=(PACK/'主持配置/系统指令V0.1.7.txt').read_text();cfg=read(PACK/'主持配置/调用配置.json')
 expected={c['id']:c for c in read(PACK/'人工判分/预期结果.json')['cases']}
 return lock['signature'],data['schedule'],cases,stories,prompt,cfg,expected
def safe_render(v,schema):
 if not m.valid_schema(v,schema):raise ValueError('模型输出不满足展示白名单，拒绝展示')
 if v['action']=='clarify':
  if v['answer'] or not v['clarification']:raise ValueError('澄清契约不符，拒绝展示')
  return v['clarification']
 if v['clarification']:raise ValueError('非澄清动作包含额外文本，拒绝展示')
 return m.render(v)
def main(batch,dry):
 sig,schedule,cases,stories,prompt,cfg,expected=load()
 if dry:
  requests=[m.payload(cases[x['case_id']],stories,prompt,cfg) for x in schedule]
  assert all(len(dump(b).encode())<32000 for b in requests)
  for x,b in zip(schedule,requests):
   if x['repeat']==2:assert b==requests[next(i for i,t in enumerate(schedule) if t['case_id']==x['case_id'])]
  print(dump({'dry_run':True,'requests':len(requests),'repeats_identical':True,'credential_read':False,'network_calls':0}));return
 gate=STATE/'clarification_review.json'
 if gate.exists() and read(gate).get('signature')==sig and read(gate).get('blocked'):
  raise RuntimeError('该固定配置已因展示剧透停止，不能继续或自动重试。')
 with (STATE/'execution.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  ledger=read(STATE/'ledger.json')
  assert not any(e['status'] in ['pending','unknown','stopped','needs_display_review','needs_scope_review'] for e in ledger['entries']), '存在待复核或未知调用，禁止继续'
  own=[e for e in ledger['entries'] if e.get('experiment')==EXPERIMENT]
  assert all(e['signature']==sig for e in own)
  key=m.credential()
  count=0
  for item in schedule:
   if any(e['case_id']==item['case_id'] and e['repeat']==item['repeat'] for e in own):continue
   n=sum(e['status']!='not_sent_dns' for e in ledger['entries'])
   cost=sum(e.get('estimated_cny',e.get('reserved_cny',2)) for e in ledger['entries'])
   assert n<150 and len(own)<7 and n==143+len(own) and cost+2<=5,'累计上限或外部账本变动'
   c=cases[item['case_id']];body=m.payload(c,stories,prompt,cfg)
   assert len(dump(body).encode())<32000
   e={'experiment':EXPERIMENT,'signature':sig,**item,'story_id':c['story_id'],'category':c.get('category','clarification_regression'),
      'status':'pending','reserved_cny':2,'started_at':time.time(),'request_sha256':hashlib.sha256(dump(body).encode()).hexdigest()}
   ledger['entries'].append(e);own.append(e);m.atomic(STATE/'ledger.json',ledger);started=time.monotonic()
   try:
    data,rid=m.send(body,key);choice=data['choices'][0];content=choice['message']['content'].replace(key,'[REDACTED]')
    fee=m.usage_cost(data['usage'])
    e.update(status='done',raw_content=content,request_id=rid or data.get('id'),actual_model=data.get('model'),
     usage=data['usage'],estimated_cny=fee,latency_ms=round((time.monotonic()-started)*1000),finish_reason=choice.get('finish_reason'))
    v=json.loads(content);e['verdict']=v
    errors=m.preliminary(v,c,stories[c['story_id']],expected[c['id']],cfg['response_format']['json_schema']['schema'])
    if v.get('action')=='clarify' and v.get('clarification')!=read(PACK/'主持配置/安全澄清模板.json').get(expected[c['id']].get('expected_template')):errors.append('unexpected_template')
    if data.get('model')!=m.MODEL or choice.get('finish_reason')!='stop':errors.append('model_or_finish_mismatch')
    if fee>2 or data['usage']['prompt_tokens']>32000:errors.append('cost_or_input_guard')
    if choice['message'].get('reasoning_content'):errors.append('unexpected_thinking')
    e['preliminary_errors']=errors
    if m.valid_schema(v,cfg['response_format']['json_schema']['schema']):e['player_display']=safe_render(v,cfg['response_format']['json_schema']['schema'])
    e['semantic_review']='pending_final_review'
    if any(x!='action_or_label_mismatch' for x in errors):e['status']='stopped'
    elif errors or v['action']=='clarify':e['status']='needs_scope_review'
   except (HTTPError,URLError,TimeoutError,ValueError,KeyError,TypeError) as err:
    e['status']='unknown' if 'estimated_cny' not in e else 'stopped';e['error_type']=type(err).__name__
    if isinstance(err,HTTPError):e['http_status']=err.code
   m.atomic(STATE/'ledger.json',ledger)
   print(dump({'case':e['case_id'],'repeat':e['repeat'],'status':e['status'],'verdict':e.get('verdict'),
    'errors':e.get('preliminary_errors'),'cumulative_attempts':n+1}),flush=True)
   count+=1
   if e['status']!='done' or count>=batch:break
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--batch',type=int,default=6);p.add_argument('--dry-run',action='store_true');a=p.parse_args();main(a.batch,a.dry_run)
