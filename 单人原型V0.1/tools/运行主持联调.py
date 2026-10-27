# -*- coding: utf-8 -*-
"""One explicit case per invocation; no automatic retries and no provider credentials."""
import argparse
import http.cookiejar
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from live_host import RoundBudget,HostFailure

def dump(value):return json.dumps(value,ensure_ascii=False,indent=2)+'\n'
def save(path,value):
    temporary=path.with_suffix('.new');temporary.write_text(dump(value));temporary.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--authorization',required=True)
    parser.add_argument('--state',required=True)
    args=parser.parse_args()
    parts=urlsplit(args.url)
    if parts.scheme!='http' or parts.hostname!='127.0.0.1' or parts.path not in ('','/'):
        raise RuntimeError('仅连接本机测试服务')
    folder=Path(args.state);folder.mkdir(parents=True,exist_ok=True)
    state_path=folder/'results.json';cookie_path=folder/'cookies.txt'
    plan=json.loads((ROOT/'验收/真实主持联调方案V0.9.0.json').read_text())
    state=json.loads(state_path.read_text()) if state_path.exists() else {'version':'V0.9.0','entries':[],'stopped':False}
    if state.get('stopped') or any(e['status'] in ('pending','review_required') for e in state['entries']):
        raise RuntimeError('先复核上次结果；不重复发送未知请求')
    cases=plan['scenarios'];index=len(state['entries'])
    if index>=len(cases):
        print('主测试已完成；定向复核单独审阅，不自动扩展。');return
    cookie=http.cookiejar.MozillaCookieJar(str(cookie_path))
    if cookie_path.exists():cookie.load(ignore_discard=True,ignore_expires=True)
    client=build_opener(HTTPCookieProcessor(cookie))
    def api(path,body=None):
        request=Request(args.url.rstrip('/')+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
                        headers={'Content-Type':'application/json'} if body is not None else {})
        with client.open(request,timeout=12) as response:result=json.load(response)
        cookie.save(ignore_discard=True,ignore_expires=True);os.chmod(cookie_path,0o600)
        return result
    case=cases[index]
    current=api('/api/current')['session']
    if current and current['story_id']!=case['story_id'] and current['status']!='completed':
        raise RuntimeError('先完成上一题收尾，不擅自丢弃对局')
    if not current or current['story_id']!=case['story_id']:
        current=api('/api/sessions',{'story_id':case['story_id'],'minutes':60,'attempts':100})['session']
        current=api('/api/sessions/'+current['id']+'/ready',{})['session']
    if current['mode']!='live':raise RuntimeError('当前服务不是已授权的真实主持联调')
    entry={'case_id':case['id'],'story_id':case['story_id'],'session_id':current['id'],
           'request_id':uuid.uuid4().hex,'text':case['text'],'status':'pending','started_at':time.time()}
    state['entries'].append(entry);save(state_path,state)
    started=time.monotonic()
    try:
        current=api('/api/sessions/'+current['id']+'/submit',{'kind':case['kind'],'text':case['text'],'request_id':entry['request_id']})['session']
        deadline=time.monotonic()+75
        while current['status']=='processing' and time.monotonic()<deadline:
            time.sleep(.25)
            current=api('/api/sessions/'+current['id'])['session']
        if current['status']=='processing':raise RuntimeError('请求仍在处理中，不重试')
        db=sqlite3.connect(str(Path(args.data_dir)/'sessions.sqlite3'))
        try:internal=json.loads(db.execute('SELECT data FROM sessions WHERE id=?',(current['id'],)).fetchone()[0])
        finally:db.close()
        audit=next(x for x in internal.get('host_audit',[]) if x['request_id']==entry['request_id'])
        output=audit.get('verdict');checks=[]
        if not output:checks.append(audit.get('error_code','missing_verdict'))
        else:
            if output['action']!=case['action'] or output['answer']!=case['answer']:checks.append('semantic_mismatch')
            if case.get('scope') and output['scope']!=case['scope']:checks.append('scope_mismatch')
            if case['id']=='L02-2' and output['clarification']!='这句在汤面中是旁白。你想确认哪一段实际发出的声音？':checks.append('narration_clarification_mismatch')
        entry.update(status='failed' if checks else 'review_required',output=output,checks=checks,
                     attempts_left=current['attempts_left'],session_status=current['status'],
                     player_display=current['messages'][-1]['text'],elapsed_ms=round((time.monotonic()-started)*1000))
        if checks:state['stopped']=True
    except Exception as error:
        entry.update(status='unknown',error_type=type(error).__name__)
        state['stopped']=True
    if state['stopped']:
        a=json.loads(Path(args.authorization).read_text())
        RoundBudget(a,Path(args.data_dir)/'provider_ledger.sqlite3').stop('integration_needs_review')
    save(state_path,state)
    print(dump({**{k:entry.get(k) for k in ['case_id','status','output','checks','player_display','elapsed_ms','session_status']},'runtime':api('/api/runtime')}))

if __name__=='__main__':main()
