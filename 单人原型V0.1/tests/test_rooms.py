"""Offline room contract checks; no Key or model provider is loaded."""
import concurrent.futures
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from server import GameError, GameService, ThreadingHTTPServer
from rooms import RoomService
from room_server import RoomHandler


class RoomTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.
        self.game = GameService(':memory:', clock=lambda: self.now, auto_jobs=False, story_ids=['S02'])
        self.rooms = RoomService(self.game)
        self.a, self.b, self.c = 'a'*48, 'b'*48, 'c'*48
        self.rooms.profile(self.a, {'name': '甲', 'avatar': 'witness'})
        self.rooms.profile(self.b, {'name': '乙', 'avatar': 'reader'})
        self.room = self.rooms.create(self.a, {'visibility': 'private'})
        self.rid = self.room['id']

    def tearDown(self): self.game.db.close()

    def join(self): self.rooms.join(self.b, {'code': self.room['code']})

    def start(self, attempts=5):
        self.join()
        self.rooms.configure(self.rid, self.a, {'story_id': 'S02', 'minutes': 25, 'attempts': attempts})
        for owner in (self.a, self.b): self.rooms.prepare(self.rid, owner, {'ready': True})
        self.rooms.start(self.rid, self.a)
        self.rooms.read(self.rid, self.a); result = self.rooms.read(self.rid, self.b)
        self.sid = result['session']['id']
        return result

    def question(self): return self.game.stories['S02']['questions'][0][0]

    def send(self, owner, number, text=None, kind='question'):
        return self.rooms.submit(self.rid, owner, {'kind': kind, 'text': text or self.question(), 'request_id': 'request-%03d' % number})

    def test_private_room_hidden_and_nonmember_cannot_read(self):
        self.assertEqual(self.rooms.listing(), [])
        with self.assertRaises(GameError): self.rooms.snapshot(self.rid, self.c)
        view = json.dumps(self.rooms.snapshot(self.rid, self.a), ensure_ascii=False)
        self.assertNotIn(self.a, view)
        self.assertNotIn('truth', view)

    def test_only_host_configures_or_starts(self):
        self.join()
        with self.assertRaises(GameError): self.rooms.configure(self.rid, self.b, {'story_id': 'S02', 'minutes': 25, 'attempts': 20})
        with self.assertRaises(GameError): self.rooms.start(self.rid, self.b)
        with self.assertRaises(GameError): self.rooms.start(self.rid, self.a)

    def test_profile_whitelist_and_name_validation(self):
        with self.assertRaises(GameError): self.rooms.profile(self.a, {'name': '甲', 'avatar': 'https://external/avatar.png'})
        with self.assertRaises(GameError): self.rooms.profile(self.a, {'name': '', 'avatar': 'reader'})

    def test_join_is_idempotent_and_capacity_is_enforced(self):
        self.rooms.capacity = 2; self.join(); self.join()
        with self.assertRaises(GameError): self.rooms.join(self.c, {'code': self.room['code']})
        self.assertEqual(len(self.rooms.snapshot(self.rid, self.a)['members']), 2)

    def test_configuration_resets_ready(self):
        self.join(); self.rooms.prepare(self.rid, self.b, {'ready': True})
        result = self.rooms.configure(self.rid, self.a, {'story_id': 'S02', 'minutes': 20, 'attempts': 10})
        self.assertFalse(any(m['ready'] for m in result['members']))

    def test_both_read_before_clock_starts_and_truth_not_exposed(self):
        self.join()
        for owner in (self.a, self.b): self.rooms.prepare(self.rid, owner, {'ready': True})
        opened = self.rooms.start(self.rid, self.a)
        self.now += 100
        one = self.rooms.read(self.rid, self.a)
        self.assertEqual(one['session']['remaining_seconds'], 1500)
        self.assertEqual(one['session']['status'], 'opening')
        both = self.rooms.read(self.rid, self.b)
        self.now += 10
        self.assertEqual(self.rooms.snapshot(self.rid, self.a)['session']['remaining_seconds'], 1490)
        self.assertNotIn('truth', opened['session']); self.assertNotIn('truth', both['session'])

    def test_same_record_and_author_seen_by_both_players(self):
        self.start(); self.send(self.b, 1); self.game.complete_job(self.sid)
        a = self.rooms.snapshot(self.rid, self.a); b = self.rooms.snapshot(self.rid, self.b)
        self.assertEqual(a['session']['messages'], b['session']['messages'])
        self.assertEqual(a['session']['attempts_left'], 4)
        self.assertEqual(a['session']['messages'][0]['author']['name'], '乙')

    def test_discussion_is_deduplicated_and_never_host_input(self):
        self.start()
        data = {'text': '讨论中随意猜测不会触发判定', 'request_id': 'discussion-01'}
        self.rooms.chat(self.rid, self.a, data); self.rooms.chat(self.rid, self.a, data)
        result = self.rooms.snapshot(self.rid, self.b)
        self.assertEqual(len(result['chat']), 1)
        self.assertEqual(result['session']['messages'], [])
        self.assertEqual(result['session']['attempts_left'], 5)

    def test_duplicate_request_once_and_payload_change_rejected(self):
        self.start(); self.send(self.a, 1); self.send(self.a, 1)
        with self.assertRaises(GameError): self.send(self.a, 1, text='另一条消息')
        self.game.complete_job(self.sid); self.send(self.a, 1)
        self.assertEqual(self.rooms.snapshot(self.rid, self.b)['session']['attempts_left'], 4)

    def test_same_request_id_different_players_is_independent(self):
        self.start(); self.send(self.a, 1); self.send(self.b, 1)
        self.game.complete_job(self.sid); self.rooms.tick(); self.game.complete_job(self.sid)
        self.assertEqual(self.rooms.snapshot(self.rid, self.a)['session']['attempts_left'], 3)

    def test_parallel_last_opportunity_has_only_one_reservation(self):
        self.start(attempts=1)
        def submit(owner):
            try: self.send(owner, 1); return True
            except GameError: return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, (self.a, self.b)))
        self.assertEqual(sum(results), 1)
        self.game.complete_job(self.sid)
        self.assertEqual(self.game._load(self.sid)['attempts_left'], 0)

    def test_queue_keeps_order_freezes_once_and_does_not_double_charge(self):
        self.start(attempts=3); self.send(self.a, 1); self.now += 3; result = self.send(self.b, 2)
        self.assertEqual(result['reserved'], 2)
        self.assertEqual(len(result['queue']), 1)
        self.now += 2; self.game.complete_job(self.sid); self.rooms.tick()
        self.now += 4; self.game.complete_job(self.sid)
        result = self.rooms.snapshot(self.rid, self.a)
        self.assertEqual(result['session']['attempts_left'], 1)
        self.assertEqual(result['session']['remaining_seconds'], 1500)
        self.assertEqual([m['author']['name'] for m in result['session']['messages'] if m['role'] == 'player'], ['甲', '乙'])

    def test_unknown_free_question_returns_opportunity(self):
        self.start(); self.send(self.a, 1, '流程示例之外的自由提问')
        self.game.complete_job(self.sid)
        result = self.rooms.snapshot(self.rid, self.b)
        self.assertEqual(result['session']['attempts_left'], 5)
        self.assertEqual(result['session']['messages'][-1]['kind'], 'demo_notice')

    def test_fixture_controls_not_accepted_from_browser(self):
        self.start()
        with self.assertRaises(GameError): self.rooms.submit(self.rid, self.a, {'kind': 'solution', 'text': '随意', 'request_id': 'control-001', 'demo_outcome': 'complete'})

    def test_hint_requires_two_votes_and_only_applies_once(self):
        self.start(); opened = self.rooms.vote(self.rid, self.a, {'kind': 'hint'})
        self.assertEqual(opened['session']['hint_count'], 0)
        voted = self.rooms.vote(self.rid, self.b, {'kind': 'hint', 'vote_id': opened['vote']['id'], 'agree': True})
        self.rooms.tick(); self.rooms.tick()
        self.assertEqual(voted['session']['hint_count'], 1)
        self.assertEqual(self.rooms.snapshot(self.rid, self.a)['session']['attempts_left'], 5)

    def test_hint_in_question_cannot_bypass_room_vote(self):
        self.start()
        result = self.send(self.a, 1, '给我提示')
        self.assertEqual(result['session']['hint_count'], 0)
        self.assertEqual(result['vote']['state'], 'open')
        self.assertEqual(result['vote']['required'], 2)

    def test_tie_and_expired_vote_dont_reveal_hint(self):
        self.start(); opened = self.rooms.vote(self.rid, self.a, {'kind': 'hint'})
        self.now += 31; self.rooms.tick()
        result = self.rooms.snapshot(self.rid, self.b)
        self.assertEqual(result['vote']['state'], 'expired')
        self.assertEqual(result['session']['hint_count'], 0)

    def test_vote_base_unchanged_when_player_leaves(self):
        self.start(); self.rooms.vote(self.rid, self.a, {'kind': 'hint'})
        self.rooms.leave(self.rid, self.b)
        result = self.rooms.snapshot(self.rid, self.a)
        self.assertEqual(result['vote']['required'], 2)
        self.assertEqual(result['session']['hint_count'], 0)

    def test_approved_hint_waits_for_processing_request(self):
        self.start(); self.send(self.a, 1)
        opened = self.rooms.vote(self.rid, self.a, {'kind': 'hint'})
        self.rooms.vote(self.rid, self.b, {'kind': 'hint', 'vote_id': opened['vote']['id'], 'agree': True})
        self.assertEqual(self.game._load(self.sid)['hint_count'], 0)
        self.game.complete_job(self.sid); self.rooms.tick()
        self.assertEqual(self.game._load(self.sid)['hint_count'], 1)

    def test_reveal_needs_confirmation_from_other_player(self):
        self.start(); opened = self.rooms.vote(self.rid, self.a, {'kind': 'reveal'})
        self.assertNotIn('truth', opened['session'])
        voted = self.rooms.vote(self.rid, self.b, {'kind': 'reveal', 'vote_id': opened['vote']['id'], 'agree': True})
        self.assertIn('truth', voted['session'])

    def test_host_handoff_and_all_leave_closes_round(self):
        self.start(); self.rooms.leave(self.rid, self.a)
        view = self.rooms.snapshot(self.rid, self.b)
        self.assertTrue(view['members'][0]['host'])
        self.rooms.leave(self.rid, self.b)
        self.assertEqual(self.game._load(self.sid)['status'], 'interrupted')

    def test_success_cancels_queue_and_reveals_to_both(self):
        self.start()
        text = next(x['text'] for x in self.game.stories['S02']['solutions'] if x['result'] == '通过')
        self.send(self.a, 1, text=text, kind='solution'); self.send(self.b, 2)
        self.game.complete_job(self.sid); self.rooms.tick()
        self.game.advance_end(self.sid); self.game.advance_end(self.sid)
        a = self.rooms.snapshot(self.rid, self.a); b = self.rooms.snapshot(self.rid, self.b)
        self.assertEqual(a['queue'], [])
        self.assertEqual(a['session']['end_reason'], 'success')
        self.assertEqual(a['session']['truth'], b['session']['truth'])
        self.assertEqual(a['session']['review'], b['session']['review'])
        self.assertEqual(self.rooms.return_to_room(self.rid, self.a)['session'], None)

    def test_refresh_recovers_room_and_does_not_reset_clock(self):
        self.start(); self.now += 22
        restored = self.rooms.current(self.b)
        self.assertEqual(restored['session']['id'], self.sid)
        self.assertEqual(restored['session']['remaining_seconds'], 1478)

    def test_feedback_keeps_author_and_nonmember_cannot_write(self):
        self.start()
        self.rooms.feedback(self.rid, self.b, {'text': '这里留下乙的反馈。'})
        self.assertEqual(self.game._load(self.sid)['feedback'][-1]['author']['name'], '乙')
        with self.assertRaises(GameError): self.rooms.feedback(self.rid, self.c, {'text': '未入座者的反馈'})
        with self.assertRaises(GameError): self.rooms.chat(self.rid, self.c, {'text': '越权讨论', 'request_id': 'outside-01'})
        with self.assertRaises(GameError): self.send(self.c, 1)

    def test_restart_releases_queued_work_without_resubmitting(self):
        self.start(); self.send(self.a, 1); self.send(self.b, 2)
        recovered = RoomService(self.game)
        view = recovered.snapshot(self.rid, self.a)
        self.assertEqual(view['queue'], [])
        self.assertIn('尚未发送', view['notice'])
        self.assertEqual(len(self.game._load(self.sid)['messages']), 1)

    def test_public_catalog_does_not_leak_genre_or_private_facts(self):
        cards = self.rooms.catalog(self.a)
        self.assertNotIn('genre', cards[0]); self.assertNotIn('truth', cards[0]); self.assertNotIn('facts', cards[0])


class RoomHTTPTests(unittest.TestCase):
    def test_two_devices_join_and_unrelated_device_is_denied(self):
        game = GameService(':memory:', auto_jobs=False, story_ids=['S02'])
        class TestHandler(RoomHandler): pass
        TestHandler.rooms = RoomService(game); TestHandler.service = game
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def call(method, path, owner, data=None):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Cookie': 'turtle_local=' + owner, 'Content-Type': 'application/json'}
            connection.request(method, path, None if data is None else json.dumps(data), headers)
            response = connection.getresponse(); body = json.loads(response.read()); status = response.status
            connection.close(); return status, body
        try:
            status, body = call('POST', '/api/rooms', 'a'*48, {'visibility': 'private'})
            self.assertEqual(status, 201); r = body['room']
            status, joined = call('POST', '/api/rooms/join', 'b'*48, {'code': r['code']})
            self.assertEqual(status, 200); self.assertEqual(len(joined['room']['members']), 2)
            status, denied = call('GET', '/api/rooms/' + r['id'], 'c'*48)
            self.assertEqual(status, 403)
            self.assertEqual(call('GET', '/api/stories', 'c'*48)[0], 200)
            self.assertEqual(call('GET', '/api/current', 'a'*48)[0], 404)
        finally:
            server.shutdown(); server.server_close(); game.db.close()


if __name__ == '__main__': unittest.main()
