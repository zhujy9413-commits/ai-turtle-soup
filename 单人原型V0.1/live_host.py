# -*- coding: utf-8 -*-
"""Bounded server-side host. Importing this module never reads credentials or calls APIs."""
import copy
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler

from host_boundary import public_scope_input, private_judge_input
from host_feedback import public_references, help_text
from host_assessment import (AssessmentError, criterion_catalog, evidence_sources, question_verdict, solution_verdict, is_negative)

ROOT = Path(__file__).resolve().parent
MODEL = 'qwen3.7-flash-2026-07-15'
BASE_URL = 'https://ws-6sartjfe1i7cpkgw.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
RESERVE_CNY = 2.0
PRIOR_ESTIMATED_CNY = 0.1377942
CONFIG = ROOT / 'private/host'
FOUR = {'是', '否', '无关', '不清楚'}


class HostFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def materials_signature():
    manifest = json.loads((CONFIG/'source_manifest.json').read_text())
    hashes = {}
    for filename, entry in manifest['files'].items():
        value = hashlib.sha256((CONFIG/filename).read_bytes()).hexdigest()
        if value != entry['sha256']:
            raise HostFailure('materials_changed')
        hashes[filename] = value
    hashes['stories.json'] = hashlib.sha256((ROOT/'private/stories.json').read_bytes()).hexdigest()
    for name in ['live_host.py','host_boundary.py','host_feedback.py','player_round.py','server.py','host_assessment.py']:
        hashes[name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return digest({'version': 'live-adapter-v0.9.3', 'hashes': hashes})


def public_history(messages, current_id=None):
    history=[]
    for message in messages:
        if message['id']==current_id:continue
        if message['role']=='player' and message['kind'] in ('question','solution'):
            history.append({'id':message['id'],'role':'player','channel':'formal_'+message['kind'],'text':message['text']})
        elif message['role']=='host' and message['kind'] in ('answer','clarify','hint','solution_result','correction','help'):
            item={'id':message['id'],'role':'host','kind':message['kind'],'text':message['text']}
            for key in ('question_id','answer'):
                if key in message:item[key]=message[key]
            if message.get('scope') in ('actual','hypothetical'):item['scope']=message['scope']
            if message.get('corrected_by'):item['superseded']=True
            history.append(item)
    return history


def public_narration_reference(story, message):
    """A narration correction must be anchored to an unquoted phrase in the public surface."""
    surface = story['surface']
    quoted_surface = re.findall(r'[“「"]([^”」"\n]+)[”」"]', surface)
    phrases = re.findall(r'[“「"‘]([^”」"’\n]+)[”」"’]', message.get('text', ''))
    return any(len(p) >= 3 and p in surface and all(p not in q for q in quoted_surface) for p in phrases)


def public_reading_guard(story, message):
    """Narrow deterministic guard for quoted public narration treated as an existing voice.

    This does not guess hidden answers or classify arbitrary prose. Explicit existence or
    utterance questions still go to the normal judge, as do quotes of actual public dialogue.
    """
    if not public_narration_reference(story, message):
        return False
    text = message['text']
    if re.search(r'说出口|说过|说了|讲过|说出过|声音.{0,5}(?:存在|出现过|发出过)', text):
        return False
    return bool(re.search(r'声音|声线|声源|说话者|谁说|谁在说|谁发出|由谁|触发', text))


def public_text_reference(message):
    return bool(re.search(r'(?:这|那|前|后|第).{0,5}(?:句|段|话|行)|[“「\"]',message.get('text','')))


def requests_hidden_answer(message):
    """Explicit requests to expose the answer are handled by application permissions, not a model."""
    text=message.get('text','')
    return bool(re.search(r'(?:请|给我|帮我|直接|忽略|无视|管理员)',text)
                and re.search(r'(?:公布|公开|展示|给出|提供|告诉|揭晓|输出).{0,8}(?:汤底|完整故事|完整答案|隐藏答案|隐藏事实)',text))


class RoundBudget:
    """Persistent per-round count and conservative cost reservations. No reset on restart."""
    def __init__(self, authorization, ledger_path):
        self.authorization = copy.deepcopy(authorization)
        a = self.authorization
        if (a.get('approved') is not True or not isinstance(a.get('approval_basis'), str)
                or not a['approval_basis'].strip()):
            raise HostFailure('approval_required')
        if (not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', a.get('round_id', ''))
                or type(a.get('max_calls')) is not int or not 1 <= a['max_calls'] <= 100
                or type(a.get('cap_cny')) not in (int, float) or not 0 < a['cap_cny'] <= 5
                or a.get('model') != MODEL or a.get('base_url') != BASE_URL
                or type(a.get('expires_at')) not in (int, float)
                or not math.isfinite(a['expires_at'])):
            raise HostFailure('invalid_authorization')
        self.prior_estimated_cny = a.get('prior_estimated_cny', PRIOR_ESTIMATED_CNY)
        if (type(self.prior_estimated_cny) not in (int,float) or not math.isfinite(self.prior_estimated_cny)
                or not 0 <= self.prior_estimated_cny <= a['cap_cny']):
            raise HostFailure('invalid_authorization')
        self.round_id = a['round_id']
        ceiling=a.get('phase_call_ceiling',a['max_calls'])
        if type(ceiling) is not int or not 1<=ceiling<=a['max_calls']:raise HostFailure('invalid_authorization')
        if 'phase_call_ceiling' in a:
            approval=a.get('maintenance_approval',{})
            if (approval.get('approved') is not True or not isinstance(approval.get('basis'),str)
                    or not approval['basis'].strip() or type(approval.get('max_new_calls')) is not int
                    or not 1<=approval['max_new_calls']<=a['max_calls']):raise HostFailure('approval_required')
        self.call_ceiling=ceiling
        self.path = Path(ledger_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS rounds (id TEXT PRIMARY KEY, signature TEXT NOT NULL, blocked TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS calls
                (round_id TEXT, operation TEXT, stage TEXT, status TEXT, reserved REAL,
                 cost REAL, metadata TEXT, PRIMARY KEY(round_id,operation,stage))''')
            row = db.execute('SELECT signature FROM rounds WHERE id=?', (self.round_id,)).fetchone()
            signature = digest(a)
            if row and row[0] != signature:
                raise HostFailure('authorization_changed')
            db.execute('INSERT OR IGNORE INTO rounds VALUES (?,?,NULL)', (self.round_id, signature))

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _check(self, db, slots=1):
        if time.time() >= self.authorization['expires_at']:
            raise HostFailure('round_expired')
        record = db.execute('SELECT signature,blocked FROM rounds WHERE id=?', (self.round_id,)).fetchone()
        if record[0] != digest(self.authorization):
            raise HostFailure('authorization_changed')
        if record[1]:
            raise HostFailure('round_stopped')
        rows = db.execute('SELECT status,COALESCE(cost,reserved) FROM calls WHERE round_id=?', (self.round_id,)).fetchall()
        if any(row[0] == 'pending' for row in rows):
            raise HostFailure('provider_busy')
        if len(rows) + slots > self.call_ceiling:
            raise HostFailure('call_limit')
        if self.prior_estimated_cny + sum(row[1] for row in rows) + RESERVE_CNY > self.authorization['cap_cny']:
            raise HostFailure('cost_limit')

    def ensure_capacity(self, slots=1):
        with self.connect() as db:
            self._check(db, slots)

    def reserve(self, operation, stage, request_hash):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check(db)
            try:
                db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',
                           (self.round_id, operation, stage, 'pending', RESERVE_CNY, None,
                            json.dumps({'request_sha256': request_hash})))
            except sqlite3.IntegrityError:
                raise HostFailure('duplicate_provider_request')

    def settle(self, operation, stage, cost, metadata):
        with self.connect() as db:
            db.execute('UPDATE calls SET status=?,cost=?,metadata=? WHERE round_id=? AND operation=? AND stage=?',
                       ('done', cost, json.dumps(metadata), self.round_id, operation, stage))

    def stop(self, reason, operation=None, stage=None):
        with self.connect() as db:
            db.execute('UPDATE rounds SET blocked=COALESCE(blocked,?) WHERE id=?', (reason, self.round_id))
            if operation is not None:
                db.execute("UPDATE calls SET status='unknown' WHERE round_id=? AND operation=? AND stage=? AND status='pending'",
                           (self.round_id, operation, stage))

    def recover_uncertain(self):
        """Call once on server boot, never retry a request that might have reached provider."""
        with self.connect() as db:
            if db.execute("SELECT 1 FROM calls WHERE round_id=? AND status='pending'", (self.round_id,)).fetchone():
                db.execute("UPDATE calls SET status='unknown' WHERE round_id=? AND status='pending'", (self.round_id,))
                db.execute("UPDATE rounds SET blocked='restart_uncertain' WHERE id=?", (self.round_id,))

    def summary(self):
        with self.connect() as db:
            rows = db.execute('SELECT status,COALESCE(cost,reserved) FROM calls WHERE round_id=?', (self.round_id,)).fetchall()
            blocked = db.execute('SELECT blocked FROM rounds WHERE id=?', (self.round_id,)).fetchone()[0]
        return {'provider_calls': len(rows),
                'remaining_authorized_provider_calls': max(0, self.call_ceiling - len(rows)),
                'remaining_in_original_round':max(0,self.authorization['max_calls']-len(rows)),
                'estimated_or_reserved_cny': round(self.prior_estimated_cny + sum(x[1] for x in rows), 7),
                'prior_estimated_cny': self.prior_estimated_cny,
                'cap_cny': self.authorization['cap_cny'],
                'blocked': bool(blocked) or time.time() >= self.authorization['expires_at'],
                'stop_reason': blocked or ('round_expired' if time.time() >= self.authorization['expires_at'] else None)}


def temporary_key():
    """Use the existing temporary setup flow, without reading its key until an approved call."""
    location = Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014/credential_location.json')
    try:
        path = Path(json.loads(location.read_text())['path'])
        if not str(path).startswith('/private/tmp/ai-turtle-soup-eval-'):
            raise ValueError()
        for entry, mode in ((path.parent, 0o700), (path, 0o600)):
            data = entry.lstat()
            if (stat.S_ISLNK(data.st_mode) or data.st_uid != os.getuid()
                    or stat.S_IMODE(data.st_mode) != mode):
                raise ValueError()
        secret = json.loads(path.read_text())
        age = time.time() - secret['created_at']
        lifetime = secret.get('ttl_seconds', 1800)
        if (type(lifetime) is not int or not 1 <= lifetime <= 3600
                or secret['region'] != 'cn-beijing' or not 0 <= age <= lifetime):
            raise ValueError()
        key = secret['api_key']
        if not isinstance(key, str) or not key or '\n' in key or '\r' in key:
            raise ValueError()
        return key
    except (OSError, ValueError, KeyError, TypeError):
        raise HostFailure('credential_required') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_bailian(body, key):
    request = Request(BASE_URL + '/chat/completions', data=encoded(body), method='POST',
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise HostFailure('response_too_large')
        return json.loads(raw)


def usage_cost(usage):
    if not isinstance(usage, dict):
        raise HostFailure('invalid_usage')
    n, o = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(n) is not int or type(o) is not int or not 0 <= n <= 1000000 or not 0 <= o <= 1546:
        raise HostFailure('invalid_usage')
    rate_in, rate_out = (.2, .8) if n <= 32000 else ((.6, 2.4) if n <= 256000 else (1.2, 4.8))
    return (n * rate_in + o * rate_out) / 1000000


class BudgetedProvider:
    def __init__(self, budget, credential=temporary_key, sender=send_bailian):
        self.budget, self.credential, self.sender = budget, credential, sender

    def record_assessment(self,operation,stage,value,validation):
        # Only the structured annotation is retained in the private ledger: no key,
        # headers, transport envelope or model reasoning is accepted here.
        with self.budget.connect() as db:
            row=db.execute('SELECT metadata FROM calls WHERE round_id=? AND operation=? AND stage=?',
                (self.budget.round_id,operation,stage)).fetchone()
            if row is None:raise HostFailure('assessment_audit_failed')
            metadata=json.loads(row[0]);metadata.update(assessment=value,assessment_validation=validation)
            db.execute('UPDATE calls SET metadata=? WHERE round_id=? AND operation=? AND stage=?',
                (json.dumps(metadata,ensure_ascii=False),self.budget.round_id,operation,stage))

    def call(self, body, stage, operation):
        if len(encoded(body)) > 32000:
            raise HostFailure('context_limit')
        self.budget.ensure_capacity()
        expected=self.budget.authorization.get('materials_sha256')
        if expected and expected!=materials_signature():raise HostFailure('materials_changed')
        key = self.credential()  # Approval and capacity are checked before touching credentials.
        self.budget.reserve(operation, stage, digest(body))
        try:
            data=self.sender(body,key)
            if not isinstance(data,dict) or data.get('model')!=MODEL:
                raise ValueError()
            cost=usage_cost(data.get('usage'))
            if cost>RESERVE_CNY:raise ValueError()
        except Exception:
            self.budget.stop('provider_usage_unknown',operation,stage)
            raise HostFailure('provider_usage_unknown') from None
        self.budget.settle(operation,stage,cost,{'usage':data['usage'],'request_sha256':digest(body)})
        try:
            choice=data['choices'][0];message=choice['message']
            if (len(data['choices'])!=1 or choice.get('finish_reason')!='stop'
                    or message.get('reasoning_content') or message.get('tool_calls')
                    or not isinstance(message.get('content'),str)
                    or data['usage']['prompt_tokens']>32000
                    or data['usage']['completion_tokens']>body['max_completion_tokens']+10):
                raise ValueError()
            return json.loads(message['content'])
        except Exception:
            # Known billed usage: isolate this response, do not close the whole round.
            raise HostFailure('invalid_provider_output') from None


def resume_reviewed_revision(previous, revised, ledger_path, review_note, credential_expires_at=None):
    mutable={'materials_sha256','review_revision'}
    if credential_expires_at is not None:
        mutable.add('expires_at')
        if (type(credential_expires_at) not in (int,float)
                or not time.time()<credential_expires_at<=time.time()+3600
                or revised.get('expires_at')!=credential_expires_at):
            raise HostFailure('invalid_execution_window')
    if ({k:v for k,v in previous.items() if k not in mutable} !=
            {k:v for k,v in revised.items() if k not in mutable}
            or revised.get('review_revision')!=previous.get('review_revision',0)+1
            or (revised.get('materials_sha256')==previous.get('materials_sha256') and credential_expires_at is None)
            or not isinstance(review_note,str) or not review_note.strip()):
        raise HostFailure('revision_changes_authorization')
    budget=RoundBudget(previous,ledger_path)
    with budget.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        reason=db.execute('SELECT blocked FROM rounds WHERE id=?',(budget.round_id,)).fetchone()[0]
        if reason not in ('integration_needs_review','invalid_scope','invalid_judge_output','unsupported_public_correction','round_stopped','round_expired','server_stopped'):
            raise HostFailure('revision_not_reviewable')
        if db.execute("SELECT 1 FROM calls WHERE round_id=? AND (status!='done' OR cost IS NULL)",(budget.round_id,)).fetchone():
            raise HostFailure('uncertain_usage_blocks_revision')
        db.execute('CREATE TABLE IF NOT EXISTS revisions (round_id TEXT, old_signature TEXT, new_signature TEXT, note TEXT, at REAL)')
        db.execute('INSERT INTO revisions VALUES (?,?,?,?,?)',(budget.round_id,digest(previous),digest(revised),review_note,time.time()))
        db.execute('UPDATE rounds SET signature=?,blocked=NULL WHERE id=?',(digest(revised),budget.round_id))


def resume_completed_maintenance(previous,revised,ledger_path,credential_expires_at):
    """A completed player round requires fresh explicit maintenance approval, with a smaller phase cap."""
    allowed={'materials_sha256','review_revision','expires_at','phase_call_ceiling','maintenance_approval'}
    if {k:v for k,v in previous.items() if k not in allowed}!={k:v for k,v in revised.items() if k not in allowed}:
        raise HostFailure('revision_changes_authorization')
    approval=revised.get('maintenance_approval',{})
    if (approval.get('approved') is not True or not isinstance(approval.get('basis'),str) or not approval['basis'].strip()
            or type(approval.get('max_new_calls')) is not int or not 1<=approval['max_new_calls']<=previous['max_calls']
            or revised.get('review_revision')!=previous.get('review_revision',0)+1
            or revised.get('expires_at')!=credential_expires_at
            or not time.time()<credential_expires_at<=time.time()+3600):raise HostFailure('approval_required')
    budget=RoundBudget(previous,ledger_path)
    with budget.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        blocked=db.execute('SELECT blocked FROM rounds WHERE id=?',(budget.round_id,)).fetchone()[0]
        if blocked!='round_completed':raise HostFailure('maintenance_requires_completed_round')
        rows=db.execute('SELECT status,cost FROM calls WHERE round_id=?',(budget.round_id,)).fetchall()
        if any(status!='done' or cost is None for status,cost in rows):raise HostFailure('uncertain_usage_blocks_revision')
        if revised.get('phase_call_ceiling')!=min(previous['max_calls'],len(rows)+approval['max_new_calls']):
            raise HostFailure('revision_changes_authorization')
        db.execute('CREATE TABLE IF NOT EXISTS revisions (round_id TEXT, old_signature TEXT, new_signature TEXT, note TEXT, at REAL)')
        db.execute('INSERT INTO revisions VALUES (?,?,?,?,?)',(budget.round_id,digest(previous),digest(revised),
            'Explicit maintenance approval: '+approval['basis'],time.time()))
        db.execute('UPDATE rounds SET signature=?,blocked=NULL WHERE id=?',(digest(revised),budget.round_id))


class LiveHost:
    mode = 'live'

    def __init__(self, provider):
        materials_signature()
        self.provider = provider
        self.templates = json.loads((CONFIG/'safe_clarifications.json').read_text())
        self.scope_format = json.loads((CONFIG/'scope_schema.json').read_text())
        self.judge_format = json.loads((CONFIG/'judge_format.json').read_text())
        self.solution_format = json.loads((CONFIG/'solution_format.json').read_text())
        self.scope_prompt = (CONFIG/'scope_prompt.txt').read_text()
        self.judge_prompt = (CONFIG/'evidence_prompt.txt').read_text()

    def available(self, kind):
        if kind != 'hint':
            self.provider.budget.ensure_capacity(2)

    def connection_ready(self):
        self.provider.budget.ensure_capacity()
        if isinstance(self.provider,BudgetedProvider):self.provider.credential()

    def payload(self, value, prompt, response_format, limit):
        return {'model': MODEL, 'messages': [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': encoded(value).decode()}],
                'response_format': response_format, 'enable_thinking': False,
                'max_completion_tokens': limit, 'stream': False, 'n': 1}

    def _empty(self,action,scope='actual',**extra):
        return {'action':action,'answer':'','clarification':'','scope':scope,
                'fact_ids':[],'covered_nodes':[],'review_refs':[],'audit_flags':[],**extra}

    def _format(self,story,history,message,kind):
        fmt=copy.deepcopy(self.judge_format if kind=='question' else self.solution_format)
        def visit(node):
            if not isinstance(node,dict):return
            for key,value in node.get('properties',{}).items():
                if key=='fact_id':value['enum']=list(story['facts'])
                if key=='criterion_id':value['enum']=[x['id'] for x in criterion_catalog(story)]
                if key=='source_id':value['enum']=list(evidence_sources(history,message))
                if key=='review_refs':value['items']['enum']=list(public_references(history)) or ['__none__']
                visit(value)
            visit(node.get('items'))
        visit(fmt['json_schema']['schema'])
        return fmt

    def _assess(self,story,history,message,kind,scope,operation,review=False):
        channel='formal_question' if kind=='question' else 'formal_solution'
        value_input=private_judge_input(story,history,message,scope,channel)
        value_input['criterion_catalog']=criterion_catalog(story) if kind=='solution' else []
        value_input['eligible_evidence_sources']=evidence_sources(history,message) if kind=='solution' else {}
        value_input['public_reference_catalog']=list(public_references(history).values()) if kind=='solution' else []
        prompt=self.judge_prompt
        if review:
            purpose={'completion':'通关','progress':'缺失项','negative':'否定'}[review]
            prompt=('这是发布前的独立'+purpose+'核对。请重新从固定事实独立标注当前发言，既核对是否缺少必要条件，也检查是否把遗漏、自然省略或旧主持错误当成事实相反。'
                    '你的输出仍是证据标注，不是复述前一次结论。不得为了维持旧答而否定正确推理。\n'+prompt)
        body=self.payload(value_input,prompt,self._format(story,history,message,kind),1536)
        stage='verify' if review else 'judge'
        value=self.provider.call(body,stage,operation)
        try:
            verdict=(question_verdict(value,story,message,self.templates) if kind=='question'
                     else solution_verdict(value,story,history,message))
            if verdict['action']=='clarify':
                if verdict['clarification'] not in self.templates.values():raise AssessmentError()
                if verdict['clarification']==self.templates['public_text'] and not public_text_reference(message):raise AssessmentError()
            elif verdict['clarification']:raise AssessmentError()
        except (AssessmentError,KeyError,TypeError,ValueError,AttributeError) as error:
            if hasattr(self.provider,'record_assessment'):
                self.provider.record_assessment(operation,stage,value,{'status':'rejected',
                    'reason':getattr(error,'code','validator_'+type(error).__name__),
                    'path':getattr(error,'path','')})
            raise HostFailure('unsupported_assessment') from None
        if hasattr(self.provider,'record_assessment'):
            self.provider.record_assessment(operation,stage,value,{'status':'accepted'})
        return {**verdict,'scope':scope}

    def judge(self,story,history,message,kind,operation):
        self.available(kind)
        scope='actual'
        if requests_hidden_answer(message):return self._empty('decline')
        if kind=='question':
            if public_reading_guard(story,message):return self._empty('clarify',clarification=self.templates['narration'])
            body=self.payload(public_scope_input(story,history,message),self.scope_prompt,self.scope_format,64)
            value=self.provider.call(body,'scope',operation)
            if type(value) is not dict or set(value)!={'route'} or not isinstance(value['route'],str):raise HostFailure('invalid_scope')
            route=value['route']
            if route=='help_progress' and not re.search(r'哪|差|缺|漏|通过|完整|还原|卡住|讲清|讲明|不懂|不知道怎么|进度|提示',message['text']):
                route='judge_actual'
            if route=='help_rules' and not re.search(r'玩法|规则|怎么玩|扣次|计时|时间限制|提交还原|怎么提问|如何提问|游戏|次数',message['text']):
                route='judge_actual'
            if route in ('help_progress','help_rules','invalid'):
                return self._empty('help',intent=route,text=help_text(history,'rules' if route=='help_rules' else 'progress') if route!='invalid' else '可以把猜测改成一个是非问题；想核对还原时，也可以问我“哪里不完整”。')
            if route=='request_hint':return self._empty('request_hint',intent=route)
            routes={'judge_actual':('actual',None),'judge_hypothetical':('hypothetical',None),
                    'clarify_scope':('unresolved','scope'),'clarify_narration':('actual','narration'),
                    'clarify_public_text':('actual','public_text')}
            if route not in routes:raise HostFailure('invalid_scope')
            scope,template=routes[route]
            # A new factual guess is not a request to locate a sentence in the public surface.
            if template=='public_text' and not public_text_reference(message):
                scope='hypothetical' if re.search('假设|假如|如果|倘若',message['text']) else 'actual'
                template=None
            if template:
                if template=='narration' and not public_narration_reference(story,message):raise HostFailure('unsupported_public_correction')
                return self._empty('clarify',scope,clarification=self.templates[template])
        result=self._assess(story,history,message,kind,scope,operation)
        if is_negative(result) or result['action'] in ('complete','incomplete'):
            # One deliberate independent check of valid verdicts, including missing
            # relations. Malformed/transport errors still never trigger a retry.
            reason='completion' if result['action']=='complete' else 'progress' if result['action']=='incomplete' else 'negative'
            verified=self._assess(story,history,message,kind,scope,operation,review=reason)
            verified['verification']={'performed':True,'reason':reason,'initial_action':result['action'],
                                      'initial_answer':result['answer'],'initial_evidence':result['negative_claims']}
            result=verified
        return result

    def runtime(self):
        return {'mode':self.mode,'live_enabled':True,**self.provider.budget.summary()}


def load_live_host(authorization_path, data_dir):
    try:
        authorization = json.loads(Path(authorization_path).read_text())
    except (OSError, ValueError, TypeError):
        raise HostFailure('approval_required') from None
    if authorization.get('materials_sha256') != materials_signature():
        raise HostFailure('materials_changed')
    budget = RoundBudget(authorization, Path(data_dir)/'provider_ledger.sqlite3')
    budget.recover_uncertain()
    budget.ensure_capacity()
    return LiveHost(BudgetedProvider(budget))
