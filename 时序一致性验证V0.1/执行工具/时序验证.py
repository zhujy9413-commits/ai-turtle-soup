"""Bounded diagnostic experiment; no game server, credentials or grading labels in requests."""
import argparse,fcntl,hashlib,importlib.util,json,os,re,sys,time
from pathlib import Path
from urllib.error import HTTPError,URLError

PACK=Path(__file__).resolve().parents[1]
STATE=Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014')
EXPERIMENT='temporal_consistency_v01'
spec=importlib.util.spec_from_file_location('base_eval',PACK/'执行工具/基础调用工具.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.STATE=STATE
def dump(v):return json.dumps(v,ensure_ascii=False,indent=2)+'\n'
def read(p):return json.loads(p.read_text())
def digest(v):return hashlib.sha256(v).hexdigest()
def atomic(p,v):
 q=p.with_suffix(p.suffix+'.new');q.write_text(dump(v));q.replace(p)
def materials():
 for name,h in read(PACK/'文件校验.json')['sha256'].items():
  if digest((PACK/name).read_bytes())!=h:raise RuntimeError('材料变更，停止。')
 cfg=read(PACK/'主持配置/实验配置.json')
 assert cfg['model']==base.MODEL and cfg['enable_thinking'] is False
 assert cfg['api_host']+'/chat/completions'==base.URL
 prompt=(PACK/'主持配置/固定系统指令.txt').read_text()
 story=read(PACK/'模型输入/S06原始故事.json')
 timeline=read(PACK/'模型输入/事件与触发条件.json')
 cases=read(PACK/'模型输入/冻结用例.json')['cases']
 expected={e['id']:e for e in read(PACK/'人工判分/预期结果.json')['cases']}
 assert len(cases)==18 and set(expected)=={c['id'] for c in cases}
 return cfg,prompt,story,timeline,cases,expected
def variant_story(story,timeline,variant):
 result=json.loads(dump(story))
 if variant=='B':result['temporal_model']=timeline
 return result
def signature(cfg,prompt,story,timeline,cases,variant):
 return digest(dump({'experiment':EXPERIMENT,'variant':variant,'model':base.MODEL,
  'url':base.URL,'prompt':prompt,'story':variant_story(story,timeline,variant),
  'cases':cases,'response_format':cfg['response_format'],'repeats':cfg['repeats']}).encode())
def body_for(case,cfg,prompt,story,timeline,variant):
 result=base.payload(case,{'S06':variant_story(story,timeline,variant)},prompt,cfg)
 assert len(dump(result).encode())<=32000
 user=result['messages'][1]['content']
 assert all(x not in user for x in ['expected_action','expected_label','evidence_reference','label_review'])
 return result
def execute(variant):
 cfg,prompt,story,timeline,cases,expected=materials()
 sig=signature(cfg,prompt,story,timeline,cases,variant)
 key=base.credential()
 with (STATE/'execution.lock').open('a') as lf:
  fcntl.flock(lf,fcntl.LOCK_EX|fcntl.LOCK_NB)
  lp=STATE/'ledger.json'
  if not lp.exists():raise RuntimeError('累计账本缺失，不能从零重置。')
  ledger=read(lp)
  if any(e['status'] in ['pending','unknown','stopped'] for e in ledger['entries']):raise RuntimeError('尚有未复核失败或未知结果。')
  rp=STATE/'temporal_variant_review.json';review=read(rp)
  if review[variant].get('outcome')=='failed':raise RuntimeError('该配置已失败，不重试。')
  if variant=='B' and not review['A'].get('reviewed'):raise RuntimeError('先复核A，再运行B。')
  for repeat in [1,2]:
   for case in cases:
    if any(e.get('signature')==sig and e.get('case_id')==case['id'] and e.get('repeat')==repeat and e['status']=='done' for e in ledger['entries']):continue
    counts=sum(e['status']!='not_sent_dns' for e in ledger['entries'])
    spent=sum(e.get('estimated_cny',e.get('reserved_cny',base.RESERVE)) for e in ledger['entries'])
    if counts>=150 or spent+base.RESERVE>5:raise RuntimeError('累计预算或次数保护已触发。')
    body=body_for(case,cfg,prompt,story,timeline,variant)
    entry={'experiment':EXPERIMENT,'variant':variant,'signature':sig,'case_id':case['id'],
     'repeat':repeat,'status':'pending','reserved_cny':base.RESERVE,'started_at':time.time(),
     'request_sha256':digest(dump(body).encode()),'prompt_sha256':digest(prompt.encode())}
    ledger['entries'].append(entry);atomic(lp,ledger);start=time.monotonic()
    try:
     data,rid=base.send(body,key);choice=data['choices'][0];content=choice['message']['content']
     content=content.replace(key,'[REDACTED]') if isinstance(content,str) else content
     cost=base.usage_cost(data.get('usage',{}))
     entry.update(status='done',raw_content=content,request_id=rid or data.get('id'),
      actual_model=data.get('model'),usage=data['usage'],estimated_cny=cost,
      latency_ms=round((time.monotonic()-start)*1000),finish_reason=choice.get('finish_reason'))
     v=json.loads(content);entry['verdict']=v
     problems=base.preliminary(v,case,story,expected[case['id']],cfg['response_format']['json_schema']['schema'])
     if choice.get('finish_reason')!='stop':problems.append('not_finished')
     if data.get('model')!=base.MODEL:problems.append('unexpected_model')
     if cost>base.RESERVE:problems.append('reservation_exceeded')
     if data['usage']['prompt_tokens']>32000:problems.append('input_outside_planned_tier')
     if choice['message'].get('reasoning_content'):problems.append('unexpected_thinking')
     entry['preliminary_errors']=problems
     if base.valid_schema(v,cfg['response_format']['json_schema']['schema']):entry['player_display']=base.render(v)
     entry['semantic_review']='pending_agent_review'
     if problems:entry['status']='stopped'
    except (HTTPError,URLError,TimeoutError,ValueError,KeyError,TypeError) as err:
     entry['status']='unknown' if 'estimated_cny' not in entry else 'stopped'
     entry['error_type']=type(err).__name__
     if isinstance(err,HTTPError):
      entry['http_status']=err.code
      try:
       problem=json.loads(err.read(8192));obj=problem.get('error',problem)
       code=obj.get('code',obj.get('type')) if isinstance(obj,dict) else None
       if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',code) and code not in key:entry['provider_error_code']=code
      except (ValueError,TypeError,AttributeError):pass
    atomic(lp,ledger)
    print(dump({'variant':variant,'case':case['id'],'repeat':repeat,'status':entry['status'],
     'cumulative_attempts':sum(e['status']!='not_sent_dns' for e in ledger['entries']),
     'estimated_and_reserved_cny':round(sum(e.get('estimated_cny',e.get('reserved_cny',2)) for e in ledger['entries']),6)}),flush=True)
    if entry['status']!='done':return 2
  return 0
def dry_run():
 cfg,prompt,story,timeline,cases,expected=materials();sizes=[]
 for variant in ['A','B']:
  for c in cases:sizes.append(len(dump(body_for(c,cfg,prompt,story,timeline,variant)).encode()))
 assert cfg['max_new_requests']==len(cases)*2*2==72
 assert cfg['previous_request_attempts']+72<=150
 print(dump({'status':'offline_preflight_passed','case_count':len(cases),
  'variants':2,'repeats':2,'max_new_calls':72,'max_payload_bytes':max(sizes),
  'real_model_requests':0,'credential_read':False}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['dry-run','A','B']);a=ap.parse_args()
 try:
  if a.mode=='dry-run':dry_run()
  else:sys.exit(execute(a.mode))
 except (RuntimeError,FileNotFoundError,BlockingIOError):
  print('运行暂停：请核对材料、阶段复核、本机配置和累计账本；无自动重试。',file=sys.stderr);sys.exit(3)
