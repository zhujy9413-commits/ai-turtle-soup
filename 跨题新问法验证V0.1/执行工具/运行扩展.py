"""Bounded internal evaluation helper. No game server or deployment code."""
import argparse, contextlib, fcntl, hashlib, json, os, stat, sys, time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

PACK = Path(__file__).resolve().parents[1]
STATE = Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014')
MODEL = 'qwen3.7-flash-2026-07-15'
URL = 'https://ws-6sartjfe1i7cpkgw.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
CAP = 5.0
LIMIT = 150
# Reserve above the documented full-context maximum input fee plus bounded output.
# This also covers a request with unknown usage. No cache/discount assumption.
RESERVE = 2.0

def dump(v): return json.dumps(v, ensure_ascii=False, indent=2)+'\n'
def read(p): return json.loads(p.read_text())
def digest(v): return hashlib.sha256(v).hexdigest()
def atomic(p, value):
    q=p.with_suffix(p.suffix+'.new')
    q.write_text(dump(value));q.replace(p)

def load_materials():
    manifest=read(PACK/'文件校验.json')
    for n,h in manifest['sha256'].items():
        if digest((PACK/n).read_bytes()) != h: raise RuntimeError('材料校验不通过：'+n)
    cases=read(PACK/'模型输入/用例.json')['cases']
    stories={sid:read(PACK/f'模型输入/故事/{sid}.json') for sid in ['S02','S06','S07']}
    prompt=(PACK/'主持配置/系统指令.txt').read_text()
    cfg=read(PACK/'主持配置/调用方案待确认.json')
    expected={c['id']:c for c in read(PACK/'人工判分/预期结果.json')['cases']}
    signature=digest(dump({'cases':cases,'stories':stories,'prompt':prompt,'format':cfg['response_format'],'model':MODEL,'endpoint':URL,'contract':'clarification-boundaries-v014','plan':{k:cfg[k] for k in ['smoke_cases','full_cases']}}).encode())
    return cases,stories,prompt,cfg,expected,signature

def payload(case, stories, prompt, cfg):
    # Grading labels never enter messages.
    user={'trusted_channel':case['channel'],'story':stories[case['story_id']],
          'public_history':case['history'],'current_message':case['current_message']}
    response_format=json.loads(json.dumps(cfg['response_format']))
    schema=response_format['json_schema']['schema']
    schema['properties']['fact_ids']['items']['enum']=list(stories[case['story_id']]['facts'])
    schema['properties']['covered_nodes']['items']['enum']=[n[0] for n in stories[case['story_id']]['nodes']]
    return {'model':MODEL,'messages':[{'role':'system','content':prompt},{'role':'user','content':dump(user)}],
            'response_format':response_format,'enable_thinking':False,
            'max_completion_tokens':512,'stream':False,'n':1}

def valid_schema(v, schema):
    if not isinstance(v,dict) or set(v)!=set(schema['required']): return False
    for k,p in schema['properties'].items():
        x=v[k]
        if p['type']=='string':
            if not isinstance(x,str) or ('enum' in p and x not in p['enum']):return False
        elif p['type']=='array':
            if not isinstance(x,list) or any(not isinstance(y,str) for y in x):return False
            if 'enum' in p['items'] and any(y not in p['items']['enum'] for y in x):return False
    return True

FIXED={'complete':'还原成立。','incomplete':'尚未还原完整，可继续结合已确认的线索。',
       'conflict':'这段还原与固定事实有冲突，可以继续核对。',
       'invalid':'请一次提交一个是非问题。','decline':'请提交与本局有关的是非问题。'}
def preliminary(v, case, story, expected, schema):
    if not valid_schema(v,schema):return ['invalid_schema']
    errors=[]; a=v['action']
    if a!=expected['expected_action'] or v['answer']!=expected['expected_label']:errors.append('action_or_label_mismatch')
    if a in ['answer','content_gap']:
        if not v['answer'] or v['clarification']:errors.append('answer_contract')
    elif v['answer']:errors.append('nonanswer_label')
    if a!='clarify' and v['clarification']:errors.append('unexpected_clarification')
    if a=='clarify' and not v['clarification'].strip():errors.append('empty_clarification')
    if not set(v['fact_ids'])<=set(story['facts']):errors.append('invalid_fact_id')
    if not set(v['covered_nodes'])<=set(n[0] for n in story['nodes']):errors.append('invalid_node_id')
    if not set(expected['required_audit_flags'])<=set(v['audit_flags']):errors.append('missing_audit_flag')
    return errors

def render(v):
    if v['action'] in ['answer','content_gap']:return v['answer']
    if v['action']=='clarify':return v['clarification']
    return FIXED[v['action']]

def usage_cost(usage):
    for k in ['prompt_tokens','completion_tokens']:
        if not isinstance(usage.get(k),int) or usage[k]<0:raise ValueError('missing_or_invalid_usage')
    n=usage['prompt_tokens'];o=usage['completion_tokens']
    if n>1000000 or o>522:raise ValueError('usage_outside_bound')
    i_rate,o_rate=(.2,.8) if n<=32000 else ((.6,2.4) if n<=256000 else (1.2,4.8))
    return (n*i_rate+o*o_rate)/1000000

def credential():
    if os.environ.get('DASHSCOPE_API_KEY'):return os.environ['DASHSCOPE_API_KEY']
    location=read(STATE/'credential_location.json')['path'];p=Path(location)
    ds=p.parent.lstat();fs=p.lstat()
    if not str(p).startswith('/private/tmp/ai-turtle-soup-eval-'):raise RuntimeError('非预期密钥位置')
    if stat.S_ISLNK(ds.st_mode) or stat.S_ISLNK(fs.st_mode):raise RuntimeError('拒绝密钥符号链接')
    if ds.st_uid!=os.getuid() or fs.st_uid!=os.getuid() or stat.S_IMODE(ds.st_mode)!=0o700 or stat.S_IMODE(fs.st_mode)!=0o600:raise RuntimeError('密钥文件权限不正确')
    d=read(p)
    if d['region']!='cn-beijing' or time.time()-d['created_at']>1800:raise RuntimeError('密钥配置已过期或地域不符')
    return d['api_key']

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def send(body,key):
    req=Request(URL,data=dump(body).encode(),method='POST',headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    with build_opener(NoRedirect()).open(req,timeout=45) as response:
        raw=response.read(1000000)
        return json.loads(raw), response.headers.get('x-request-id')

def execute(phase):
    cases,stories,prompt,cfg,expected,sig=load_materials()
    gate_path=STATE/'expanded_review.json'
    if gate_path.exists():
        gate=read(gate_path)
        if gate.get('signature')==sig and gate.get('blocked'):raise RuntimeError('该配置已停止，需新方案，不能继续或重试。')
    key=credential()
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'execution.lock').open('a') as lf:
        fcntl.flock(lf,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ledger_path=STATE/'ledger.json'
        if not ledger_path.exists():raise RuntimeError('累计账本缺失，禁止从零重置预算')
        ledger=read(ledger_path)
        if ledger['signature']!=sig:raise RuntimeError('材料变更，不能沿用本轮账本')
        if any(e['status'] in ['pending','unknown','stopped','needs_display_review'] for e in ledger['entries']):raise RuntimeError('上次调用未完成或需复核，禁止自动重试')
        if phase=='repeat':
            name='expanded_review.json'
            gate=read(STATE/name)
            if gate.get('signature')!=sig or gate.get('approved') is not True:raise RuntimeError('对应阶段尚未人工复核')
            prior=[(c,1) for c in cases if c['id'] in (cfg['smoke_cases'] if phase=='full' else cfg['full_cases'])]
            if not all(any(e.get('signature')==sig and e['case_id']==c['id'] and e['repeat']==r and e['status']=='done' and not e.get('preliminary_errors') for e in ledger['entries']) for c,r in prior):raise RuntimeError('上一阶段存在未完成或失败案例')
        selected=[(c,1) for c in cases if c['id'] in cfg['smoke_cases']] if phase=='smoke' else [(c,2 if phase=='repeat' else 1) for c in cases if c['id'] in cfg['full_cases']]
        if phase=='probe':selected=[(c,1) for c in cases if c['id']==cfg['smoke_cases'][0]]
        schema=cfg['response_format']['json_schema']['schema']
        for c,repeat in selected:
            if any(e.get('signature')==sig and e['case_id']==c['id'] and e['repeat']==repeat and e['status'] in ['done','reviewed_failure'] for e in ledger['entries']):continue
            spent=sum(e.get('estimated_cny',e.get('reserved_cny',RESERVE)) for e in ledger['entries'])
            request_count=sum(e['status']!='not_sent_dns' for e in ledger['entries'])
            if request_count>=LIMIT or spent+RESERVE>CAP:raise RuntimeError('调用次数或累计费用预留已达上限')
            body=payload(c,stories,prompt,cfg)
            # Local byte guard limits accidental prompt growth. It is not a tokenizer.
            if len(dump(body).encode())>32000:raise RuntimeError('请求文本超过本轮字节保护线')
            entry={'signature':sig,'experiment':'expanded_regression_v01','phase':phase,'case_id':c['id'],'repeat':repeat,'status':'pending','reserved_cny':RESERVE,'started_at':time.time(),'request_sha256':digest(dump(body).encode())}
            ledger['entries'].append(entry);atomic(ledger_path,ledger)
            started=time.monotonic()
            try:
                data,request_id=send(body,key)
                choice=data['choices'][0];content=choice['message']['content']
                # Never persist headers, the API key, or reasoning content.
                content=content.replace(key,'[REDACTED]') if isinstance(content,str) else content
                cost=usage_cost(data.get('usage',{}))
                entry.update(request_id=request_id or data.get('id'),actual_model=data.get('model'),usage=data['usage'],estimated_cny=cost,latency_ms=round((time.monotonic()-started)*1000),raw_content=content,finish_reason=choice.get('finish_reason'),status='done')
                if cost>RESERVE:raise ValueError('reservation_exceeded')
                verdict=json.loads(content);entry['verdict']=verdict
                checks=preliminary(verdict,c,stories[c['story_id']],expected[c['id']],schema)
                if valid_schema(verdict,schema):entry['player_display']=render(verdict)
                if choice.get('finish_reason')!='stop':checks.append('not_finished')
                if data.get('model')!=MODEL:checks.append('unexpected_model')
                if data['usage']['prompt_tokens']>32000:checks.append('input_outside_planned_tier')
                if choice['message'].get('reasoning_content'):checks.append('unexpected_thinking')
                entry['preliminary_errors']=checks;entry['semantic_review']='pending_human_review'
                if checks:entry['status']='stopped'
                elif verdict['action']=='clarify':entry['status']='needs_display_review'
            except (HTTPError,URLError,TimeoutError,ValueError,KeyError,TypeError) as error:
                entry['status']='unknown' if 'estimated_cny' not in entry else 'stopped'
                entry['error_type']=type(error).__name__
                if isinstance(error,HTTPError):
                    entry['http_status']=error.code
                    import re
                    try:
                        problem=json.loads(error.read(8192))
                        error_obj=problem.get('error',problem)
                        code=error_obj.get('code',error_obj.get('type')) if isinstance(error_obj,dict) else None
                        if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',code) and code not in key:
                            entry['provider_error_code']=code
                        request_id=error.headers.get('x-request-id') or problem.get('request_id')
                        if isinstance(request_id,str) and re.fullmatch(r'[a-fA-F0-9-]{36}',request_id):
                            entry['request_id']=request_id
                    except (ValueError,TypeError,AttributeError):pass
                elif isinstance(error,URLError):
                    entry['network_reason_type']=type(error.reason).__name__
                    entry['network_errno']=getattr(error.reason,'errno',None)
                # Do not print exception text or response body; they may contain secrets.
            atomic(ledger_path,ledger)
            print(dump({'case':c['id'],'repeat':repeat,'status':entry['status'],'calls':sum(e['status']!='not_sent_dns' for e in ledger['entries']),'cost_estimate_cny':round(sum(e.get('estimated_cny',e.get('reserved_cny',RESERVE)) for e in ledger['entries']),6)}),flush=True)
            if entry['status']!='done':return 2
        return 0

def dry_run():
    cases,stories,prompt,cfg,expected,sig=load_materials();sizes=[]
    for c in cases:
        body=payload(c,stories,prompt,cfg);raw=dump(body)
        assert all(k not in body['messages'][1]['content'] for k in ['expected_action','expected_label','semantic_review'])
        assert body['model']==MODEL and body['max_completion_tokens']==512 and body['enable_thinking'] is False
        assert len(raw.encode())<=32000
        sizes.append(len(raw.encode()))
    assert abs(usage_cost({'prompt_tokens':32000,'completion_tokens':512})*150-1.02144)<1e-9
    assert usage_cost({'prompt_tokens':991808,'completion_tokens':522})<RESERVE
    assert not valid_schema({},cfg['response_format']['json_schema']['schema'])
    print(dump({'status':'offline_preflight_passed','cases':len(cases),'max_request_bytes':max(sizes),'signature':sig,'network_requests':0,'credential_read':False,'targeted_cases':len(cfg['smoke_cases']),'full_cases':len(cfg['full_cases'])}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['dry-run','smoke','repeat']);args=parser.parse_args()
    try:
        if args.mode=='dry-run':dry_run()
        else:sys.exit(execute(args.mode))
    except (RuntimeError,FileNotFoundError,BlockingIOError):
        print('运行已暂停：请核对本机配置、材料校验或本轮账本；没有自动重试。',file=sys.stderr);sys.exit(3)
