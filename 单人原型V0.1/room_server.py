"""Local room workflow. No provider credentials or paid-model loading path."""
import argparse
import json
import logging
import mimetypes
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from server import GameError, GameService, Handler, ROOT, ThreadingHTTPServer
from rooms import AVATARS, RoomService


class RoomHandler(Handler):
    rooms = None

    def do_GET(self):
        self.identify(); path = urlsplit(self.path).path
        try:
            if path == '/api/profile': return self.respond(self.rooms.profile(self.owner))
            if path == '/api/room-current': return self.respond({'room': self.rooms.current(self.owner)})
            if path == '/api/rooms': return self.respond({'rooms': self.rooms.listing()})
            if path == '/api/stories': return self.respond({'stories': self.rooms.catalog(self.owner)})
            if path == '/api/runtime': return self.respond({'mode': 'fixture', 'live_enabled': False, 'provider_calls': 0})
            match = re.fullmatch('/api/rooms/([a-f0-9]{32})', path)
            if match: return self.respond({'room': self.rooms.snapshot(match[1], self.owner)})
            static = {'/': 'rooms.html', '/rooms.js': 'rooms.js', '/rooms.css': 'rooms.css'}
            static.update({'/avatars/' + name + '.svg': 'avatars/' + name + '.svg' for name in AVATARS})
            if path in static:
                p = ROOT / 'public' / static[path]; raw = p.read_bytes()
                self.send_response(200); self.send_header('Content-Type', mimetypes.guess_type(str(p))[0] or 'application/octet-stream')
                self._headers(len(raw)); self.end_headers(); self.wfile.write(raw); return
            # Reuse the maintained font, logo and background whitelist only.
            if not path.startswith('/api/'): return super().do_GET()
            raise GameError('没有这个页面。', 404)
        except GameError as error: self.respond({'error': str(error)}, error.status)

    def do_POST(self):
        self.identify()
        try:
            if self.headers.get('Origin') not in (None, 'http://' + self.headers.get('Host', '')):
                raise GameError('请求来源不匹配。', 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise GameError('需要JSON请求。', 415)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000: raise GameError('请求内容过长。', 413)
            data = json.loads(self.rfile.read(length))
            if type(data) is not dict: raise GameError('请求格式无效。')
            path = urlsplit(self.path).path
            if path == '/api/profile': return self.respond(self.rooms.profile(self.owner, data))
            if path == '/api/rooms': return self.respond({'room': self.rooms.create(self.owner, data)}, 201)
            if path == '/api/rooms/join': return self.respond({'room': self.rooms.join(self.owner, data)})
            match = re.fullmatch('/api/rooms/([a-f0-9]{32})/(configure|prepare|start|read|submit|chat|vote|leave|return|feedback)', path)
            if not match: raise GameError('没有这个操作。', 404)
            rid, action = match.groups()
            if action in ('start', 'read', 'leave'): result = getattr(self.rooms, action)(rid, self.owner)
            elif action == 'return': result = self.rooms.return_to_room(rid, self.owner)
            else: result = getattr(self.rooms, action)(rid, self.owner, data)
            self.respond(result if action in ('leave', 'feedback') else {'room': result})
        except GameError as error: self.respond({'error': str(error)}, error.status)
        except (TypeError, ValueError): self.respond({'error': '请求格式无效。'}, 400)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=8889)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'runtime/rooms')
    args = parser.parse_args(); args.data_dir.mkdir(parents=True, exist_ok=True)
    game = GameService(args.data_dir / 'sessions.sqlite3')
    rooms = RoomService(game); RoomHandler.rooms = rooms; RoomHandler.service = game
    server = ThreadingHTTPServer(('127.0.0.1', args.port), RoomHandler)
    stop = threading.Event()
    def tick():
        while not stop.wait(.4):
            try: rooms.tick()
            except Exception: logging.exception('Room state could not advance')
    threading.Thread(target=tick, daemon=True).start()
    print(json.dumps({'url': 'http://127.0.0.1:%d/?quiet=1' % server.server_port,
                      'mode': 'fixture', 'paid_model_calls': 0}, ensure_ascii=False), flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set(); server.server_close()


if __name__ == '__main__': main()
