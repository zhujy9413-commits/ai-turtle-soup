"""Room membership and shared play, composed around the existing GameService.

All room changes use the game's lock and database. Discussion never enters the
host history. The room transport does not accept fixture controls from players.
"""
import copy
import hashlib
import json
import re
import secrets
import uuid

from server import GameError
from host_feedback import local_intent

AVATARS = ('witness', 'keeper', 'visitor', 'reader', 'watcher', 'wanderer')
TERMINAL = ('completed', 'interrupted')


class RoomService:
    def __init__(self, game, capacity=6):
        self.game, self.db, self.lock = game, game.db, game.lock
        self.clock, self.capacity = game.clock, capacity
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS room_profiles(owner TEXT PRIMARY KEY,data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS rooms(id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS room_actions(room_id TEXT,owner TEXT,request_id TEXT,payload TEXT,
            PRIMARY KEY(room_id,owner,request_id));
          CREATE TABLE IF NOT EXISTS room_chat(id INTEGER PRIMARY KEY AUTOINCREMENT,room_id TEXT,
            round_id TEXT,author TEXT,text TEXT,at REAL);
        ''')
        # A restart cancels queued work; accepted work is recovered by GameService.
        with self.lock:
            for (raw,) in self.db.execute('SELECT data FROM rooms').fetchall():
                room = json.loads(raw)
                if room['queue']:
                    room['queue'] = []
                    room['notice'] = '连接曾中断，排队的消息尚未发送，请核对记录后重新提交。'
                    self._save(room)

    def _save(self, room):
        self.db.execute('INSERT OR REPLACE INTO rooms VALUES (?,?,?)',
                        (room['id'], room['code'], json.dumps(room, ensure_ascii=False)))
        self.db.commit()

    def _load(self, room_id, owner=None):
        row = self.db.execute('SELECT data FROM rooms WHERE id=?', (room_id,)).fetchone()
        if not row:
            raise GameError('没有找到这个房间。', 404)
        room = json.loads(row[0])
        if owner is not None and owner not in room['members']:
            raise GameError('请先加入这个房间。', 403)
        return room

    def profile(self, owner, data=None):
        with self.lock:
            row = self.db.execute('SELECT data FROM room_profiles WHERE owner=?', (owner,)).fetchone()
            profile = json.loads(row[0]) if row else {
                'id': uuid.uuid4().hex, 'name': '夜行客' + secrets.token_hex(2), 'avatar': 'witness'}
            if data is not None:
                name = str(data.get('name', '')).strip()
                avatar = data.get('avatar')
                if not 1 <= len(name) <= 12 or any(ord(c) < 32 for c in name):
                    raise GameError('名字请填写1—12个字。')
                if avatar not in AVATARS:
                    raise GameError('请选择已有头像。')
                profile.update(name=name, avatar=avatar)
            self.db.execute('INSERT OR REPLACE INTO room_profiles VALUES (?,?)',
                            (owner, json.dumps(profile, ensure_ascii=False)))
            self.db.commit()
            return profile

    def current(self, owner):
        with self.lock:
            for (raw,) in self.db.execute('SELECT data FROM rooms ORDER BY rowid DESC').fetchall():
                room = json.loads(raw)
                if not room['closed'] and owner in room['members']:
                    return self.snapshot(room['id'], owner)
            return None

    def catalog(self, owner):
        # Explicit allowlist: internal genre/rule metadata never enters the lobby.
        return [{key: card[key] for key in ('id', 'title', 'intro', 'fear', 'difficulty') if key in card}
                for card in self.game.list_stories(owner)]

    def listing(self):
        with self.lock:
            result = []
            for (raw,) in self.db.execute('SELECT data FROM rooms ORDER BY rowid DESC').fetchall():
                r = json.loads(raw)
                if r['visibility'] == 'public' and not r['closed'] and not r['session_id']:
                    result.append({'id': r['id'], 'code': r['code'], 'host': r['members'][r['host']]['profile'],
                                   'count': len(r['members']), 'capacity': self.capacity})
            return result[:50]

    def create(self, owner, data):
        visibility = data.get('visibility', 'private')
        if visibility not in ('public', 'private'):
            raise GameError('房间类型无效。')
        with self.lock:
            if self.current(owner):
                raise GameError('你已经在一个房间里，请先离开。', 409)
            if self.db.execute('SELECT COUNT(*) FROM rooms WHERE json_extract(data,\'$.closed\')=0').fetchone()[0] >= 50:
                raise GameError('等待中的房间较多，请稍后再创建。', 409)
            while True:
                code = ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(6))
                if not self.db.execute('SELECT 1 FROM rooms WHERE code=?', (code,)).fetchone():
                    break
            room = {'id': uuid.uuid4().hex, 'code': code, 'visibility': visibility, 'host': owner,
                    'members': {owner: self._member(owner)}, 'closed': False,
                    'story_id': sorted(self.game.allowed_stories)[0], 'minutes': 25, 'attempts': 40,
                    'session_id': None, 'round_ids': [], 'readers': [], 'queue': [], 'vote': None,
                    'notice': '', 'created_at': self.clock()}
            self._save(room)
            return self._view(room, owner)

    def _member(self, owner):
        return {'profile': self.profile(owner), 'ready': False, 'joined_at': self.clock(), 'seen_at': self.clock()}

    def join(self, owner, data):
        code = str(data.get('code', '')).strip().upper()
        with self.lock:
            row = self.db.execute('SELECT id FROM rooms WHERE code=?', (code,)).fetchone()
            if not row:
                raise GameError('房间码不正确，或房间已经关闭。', 404)
            r = self._load(row[0])
            if r['closed']:
                raise GameError('这个房间已经关闭。', 409)
            existing = self.current(owner)
            if existing and existing['id'] != r['id']:
                raise GameError('请先离开当前房间。', 409)
            if owner not in r['members']:
                if r['session_id']:
                    raise GameError('这一局已经开始，请等下一局再入座。', 409)
                if len(r['members']) >= self.capacity:
                    raise GameError('房间已经坐满了。', 409)
                r['members'][owner] = self._member(owner)
                self._save(r)
            return self.snapshot(r['id'], owner)

    def _host(self, room, owner):
        if room['host'] != owner:
            raise GameError('这项操作由房主进行。', 403)

    def configure(self, room_id, owner, data):
        with self.lock:
            r = self._load(room_id, owner); self._host(r, owner)
            if r['session_id']:
                raise GameError('这一局的规则已经锁定。', 409)
            sid, minutes, attempts = data.get('story_id'), data.get('minutes'), data.get('attempts')
            if sid not in self.game.allowed_stories:
                raise GameError('请选择已有档案。')
            if type(minutes) is not int or not 1 <= minutes <= 60 or type(attempts) is not int or not 1 <= attempts <= 100:
                raise GameError('时长为1—60分钟，次数为1—100次，请填写整数。')
            r.update(story_id=sid, minutes=minutes, attempts=attempts)
            for member in r['members'].values(): member['ready'] = False
            self._save(r)
            return self._view(r, owner)

    def prepare(self, room_id, owner, data):
        with self.lock:
            r = self._load(room_id, owner)
            if r['session_id'] or type(data.get('ready')) is not bool:
                raise GameError('当前不能修改准备状态。', 409)
            r['members'][owner]['ready'] = data['ready']; self._save(r)
            return self._view(r, owner)

    def start(self, room_id, owner):
        with self.lock:
            r = self._load(room_id, owner); self._host(r, owner)
            if r['session_id']: return self._view(r, owner)
            if len(r['members']) < 2 or not all(m['ready'] for m in r['members'].values()):
                raise GameError('至少两位玩家入座，并且全员准备后才能开始。', 409)
            active = 0
            for (raw,) in self.db.execute('SELECT data FROM rooms').fetchall():
                other = json.loads(raw)
                if other['session_id'] and self.game._load(other['session_id'])['status'] not in TERMINAL:
                    active += 1
            if active >= 8: raise GameError('正在进行的对局已满，请稍后开局。', 409)
            s = self.game.create('room:' + r['id'], {key: r[key] for key in ('story_id', 'minutes', 'attempts')})
            r.update(session_id=s['id'], readers=[], queue=[], vote=None, notice='')
            r['round_ids'].append(s['id']); self._save(r)
            return self._view(r, owner)

    def read(self, room_id, owner):
        with self.lock:
            r = self._load(room_id, owner)
            if not r['session_id']: raise GameError('请先打开档案。', 409)
            if owner not in r['readers']: r['readers'].append(owner)
            self._save(r); self._settle(r)
            return self._view(r, owner)

    def _request(self, r, owner, rid, payload):
        if not isinstance(rid, str) or not re.fullmatch(r'[a-zA-Z0-9-]{10,64}', rid):
            raise GameError('提交标识无效，请刷新后再试。')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = r['id'] + '/' + str(r['session_id'])
        row = self.db.execute('SELECT payload FROM room_actions WHERE room_id=? AND owner=? AND request_id=?',
                              (key, owner, rid)).fetchone()
        if row and row[0] != digest: raise GameError('同一提交标识不能用于不同消息。', 409)
        return key, digest, bool(row)

    def _receipt(self, key, owner, rid, payload):
        self.db.execute('INSERT OR IGNORE INTO room_actions VALUES (?,?,?,?)', (key, owner, rid, payload))
        self.db.commit()

    def _dispatch(self, r, item):
        sid = r['session_id']; before = {m['id'] for m in self.game._load(sid)['messages']}
        self.game.submit(sid, 'room:' + r['id'], {'kind': item['kind'], 'text': item['text'], 'request_id': item['request_id']})
        s = self.game._load(sid)
        for m in s['messages']:
            if m['id'] not in before and m['role'] == 'player': m['author'] = item['author']
        self.game._save(s)

    def submit(self, room_id, owner, data):
        kind, text = data.get('kind'), str(data.get('text', '')).strip()
        if kind not in ('question', 'solution') or not text or len(text) > 3000:
            raise GameError('请填写问题或还原，最多3000字。')
        if set(data) - {'kind', 'text', 'request_id'}:
            raise GameError('提交包含不支持的字段。')
        with self.lock:
            r = self._load(room_id, owner); self._settle(r)
            key, payload, duplicate = self._request(r, owner, data.get('request_id'), {'kind': kind, 'text': text})
            if duplicate: return self._view(r, owner)
            if not r['session_id']: raise GameError('请先开始这一局。', 409)
            s = self.game._load(r['session_id'])
            if s['status'] not in ('active', 'processing'):
                raise GameError('请等全员读完汤面；已结束的对局无法继续提交。', 409)
            if kind == 'question' and local_intent(text) == 'hint':
                result = self.vote(room_id, owner, {'kind': 'hint'})
                self._receipt(key, owner, data['request_id'], payload)
                return result
            if s['attempts_left'] - s['reserved'] - len(r['queue']) <= 0:
                raise GameError('剩余机会已经由在途提交占用，请等结果返回。', 409)
            item = {'kind': kind, 'text': text, 'author': r['members'][owner]['profile'],
                    'request_id': r['members'][owner]['profile']['id'][:12] + '-' + data['request_id']}
            if s['status'] == 'processing': r['queue'].append(item)
            else: self._dispatch(r, item)
            self._save(r); self._receipt(key, owner, data['request_id'], payload)
            return self._view(r, owner)

    def chat(self, room_id, owner, data):
        text = str(data.get('text', '')).strip()
        if not text or len(text) > 500: raise GameError('讨论内容请填写1—500字。')
        with self.lock:
            r = self._load(room_id, owner)
            key, payload, duplicate = self._request(r, owner, data.get('request_id'), {'chat': text})
            if not duplicate:
                self.db.execute('INSERT INTO room_chat(room_id,round_id,author,text,at) VALUES (?,?,?,?,?)',
                                (r['id'], r['session_id'], json.dumps(r['members'][owner]['profile'], ensure_ascii=False), text, self.clock()))
                self._receipt(key, owner, data['request_id'], payload)
                if r['session_id']:
                    s = self.game._load(r['session_id']); s['last_player_at'] = self.clock(); s['cold_notified'] = False
                    self.game._save(s)
            return self._view(r, owner)

    def vote(self, room_id, owner, data):
        kind = data.get('kind')
        if kind not in ('hint', 'reveal'): raise GameError('投票类型无效。')
        with self.lock:
            r = self._load(room_id, owner); self._settle(r)
            if not r['session_id']: raise GameError('请先开始这一局。', 409)
            s = self.game._load(r['session_id'])
            if s['status'] not in ('active', 'processing'): raise GameError('当前无法发起投票。', 409)
            vote = r['vote']
            if data.get('vote_id'):
                if not vote or vote['id'] != data['vote_id'] or vote['state'] != 'open':
                    raise GameError('这次投票已经结束。', 409)
                member_id = r['members'][owner]['profile']['id']
                if member_id not in vote['eligible'] or type(data.get('agree')) is not bool:
                    raise GameError('你不在本次投票名单中。', 403)
                vote['ballots'].setdefault(member_id, data['agree'])
            else:
                if vote and vote['state'] in ('open', 'approved'):
                    raise GameError('请先完成当前投票。', 409)
                if kind == 'hint' and s['hint_count'] >= 3: raise GameError('三层提示已经全部公开。')
                eligible = [m['profile']['id'] for m in r['members'].values() if self.clock() - m['seen_at'] <= 30]
                me = r['members'][owner]['profile']['id']
                if me not in eligible: eligible.append(me)
                vote = {'id': uuid.uuid4().hex, 'kind': kind, 'eligible': eligible, 'ballots': {me: True},
                        'required': len(eligible)//2 + 1 if kind == 'hint' else len(eligible),
                        'expires_at': self.clock() + 30, 'state': 'open'}
                r['vote'] = vote
            self._save(r); self._settle(r)
            return self._view(r, owner)

    def leave(self, room_id, owner):
        with self.lock:
            r = self._load(room_id, owner); del r['members'][owner]
            if not r['members']: r['closed'] = True; r['queue'] = []
            elif r['host'] == owner: r['host'] = next(iter(r['members']))
            self._save(r); self._settle(r)
            return {'left': True}

    def return_to_room(self, room_id, owner):
        with self.lock:
            r = self._load(room_id, owner); self._host(r, owner); self._settle(r)
            if not r['session_id'] or self.game._load(r['session_id'])['status'] not in TERMINAL:
                raise GameError('请等揭晓和复盘完成。', 409)
            r.update(session_id=None, readers=[], queue=[], vote=None, notice='')
            for member in r['members'].values(): member['ready'] = False
            self._save(r)
            return self._view(r, owner)

    def feedback(self, room_id, owner, data):
        with self.lock:
            r = self._load(room_id, owner)
            if not r['session_id']: raise GameError('没有可反馈的对局。', 409)
            result = self.game.feedback(r['session_id'], 'room:' + r['id'], data)
            session = self.game._load(r['session_id'])
            session['feedback'][-1]['author'] = r['members'][owner]['profile']
            self.game._save(session)
            return result

    def _settle(self, r):
        if not r['session_id']: return
        sid, game_owner = r['session_id'], 'room:' + r['id']
        s = self.game._load(sid)
        if r['closed']:
            if s['status'] not in TERMINAL + ('processing', 'revealing', 'reviewing'):
                self.game.interrupt(sid, game_owner)
            return
        if s['status'] == 'opening' and all(owner in r['readers'] for owner in r['members']):
            self.game.ready(sid, game_owner)
        s = self.game.snapshot(sid, game_owner)
        vote = r['vote']
        if s['status'] in TERMINAL + ('revealing', 'reviewing'):
            r['queue'] = []
            if vote and vote['state'] in ('open', 'approved'): vote['state'] = 'cancelled'
        elif vote and vote['state'] == 'open':
            yes = sum(v is True for v in vote['ballots'].values())
            if yes >= vote['required']: vote['state'] = 'approved'
            elif self.clock() >= vote['expires_at']: vote['state'] = 'expired'
            elif yes + len(vote['eligible']) - len(vote['ballots']) < vote['required']: vote['state'] = 'rejected'
        if vote and vote['state'] == 'approved' and s['status'] == 'active':
            if vote['kind'] == 'hint':
                self.game.submit(sid, game_owner, {'kind': 'hint', 'request_id': 'room-hint-' + vote['id']})
            else: self.game.reveal(sid, game_owner, confirmed=True)
            vote['state'] = 'applied'
        s = self.game._load(sid)
        if s['status'] == 'active' and r['queue']:
            item = r['queue'].pop(0)
            self._dispatch(r, item)
        self._save(r)

    def tick(self):
        with self.lock:
            for (raw,) in self.db.execute('SELECT data FROM rooms').fetchall():
                self._settle(json.loads(raw))

    def snapshot(self, room_id, owner):
        with self.lock:
            r = self._load(room_id, owner)
            r['members'][owner]['seen_at'] = self.clock()
            self._settle(r); self._save(r)
            return self._view(r, owner)

    def _view(self, r, owner):
        session = self.game._view(self.game._load(r['session_id'])) if r['session_id'] else None
        members = [{**m['profile'], 'host': key == r['host'], 'ready': m['ready'],
                    'online': self.clock() - m['seen_at'] <= 30, 'read': key in r['readers']}
                   for key, m in r['members'].items()]
        chat = self.db.execute('SELECT id,author,text,at FROM room_chat WHERE room_id=? ORDER BY id DESC LIMIT 100', (r['id'],)).fetchall()
        return {'id': r['id'], 'code': r['code'], 'visibility': r['visibility'], 'capacity': self.capacity,
                'me': r['members'][owner]['profile']['id'], 'members': members, 'story_id': r['story_id'],
                'minutes': r['minutes'], 'attempts': r['attempts'], 'session': session,
                'queue': [{'kind': q['kind'], 'text': q['text'], 'author': q['author']} for q in r['queue']],
                'reserved': (session['reserved'] if session else 0) + len(r['queue']),
                'vote': copy.deepcopy(r['vote']), 'notice': r['notice'], 'server_time': self.clock(),
                'chat': [{'id': row[0], 'author': json.loads(row[1]), 'text': row[2], 'at': row[3]} for row in reversed(chat)]}
