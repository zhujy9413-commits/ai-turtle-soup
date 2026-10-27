# -*- coding: utf-8 -*-
"""One local player session. Unapproved config cannot start it; no automatic retries."""
import argparse
import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK))
from server import GameService, Handler, ThreadingHTTPServer
from live_host import HostFailure, load_live_host

POINTER = Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014/credential_location.json')

def safe_credential_path(value):
    path = Path(value)
    if not re.fullmatch(r'/private/tmp/ai-turtle-soup-eval-[A-Za-z0-9_-]+/credentials\.json',str(path)):
        return None
    try:
        for entry, mode in ((path.parent,0o700),(path,0o600)):
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
                return None
        return path
    except OSError:
        return None

def cleanup_credential(expected_path, pointer=POINTER):
    """Delete only this round's captured temp file; never delete a later round's key."""
    path = safe_credential_path(expected_path)
    removed = False
    if path:
        path.unlink()
        removed = True
        try:path.parent.rmdir()
        except OSError:pass
    try:
        current = json.loads(pointer.read_text())
        if current.get('path') == str(expected_path):
            pointer.unlink()
            pointer_removed = True
        else:
            pointer_removed = False
    except (OSError, ValueError, TypeError):
        pointer_removed = not pointer.exists()
    return {'temporary_key_removed':removed or not Path(expected_path).exists(),
            'pointer_removed':pointer_removed, 'keychain_used':False, 'cloud_key_revoked':False}

class RoundLifecycle:
    def __init__(self, game, budget, deadline, credential_path, record, pointer=POINTER, clock=time.time):
        self.game,self.budget,self.deadline = game,budget,deadline
        self.credential_path,self.record,self.pointer,self.clock = credential_path,Path(record),pointer,clock
        self.finished = False

    def finish(self, reason):
        if self.finished:return
        self.finished = True
        self.game.pause_all(reason)
        self.budget.stop(reason)
        cleanup = cleanup_credential(self.credential_path, self.pointer)
        self.record.parent.mkdir(parents=True,exist_ok=True)
        self.record.write_text(json.dumps({'reason':reason,'finished_at':self.clock(),
            **cleanup,'budget':self.budget.summary()},ensure_ascii=False,indent=2)+'\n')

    def tick(self):
        if self.finished:return
        if self.clock() >= self.deadline:
            self.finish('round_expired');return
        summary=self.budget.summary()
        if summary['blocked']:
            self.finish('round_stopped');return
        with self.game.lock:
            rows=self.game.db.execute('SELECT data FROM sessions').fetchall()
            for (raw,) in rows:
                session=json.loads(raw)
                self.game._expire(session)
                if session['status'] in ('completed','interrupted'):
                    self.finish('round_completed');return

def main():
    args=argparse.ArgumentParser()
    args.add_argument('--authorization',required=True)
    args.add_argument('--data-dir',required=True)
    args.add_argument('--port',type=int,default=8878)
    options=args.parse_args()
    runtime=Path(options.data_dir)
    # Verify explicit count/cost/material approval before touching key metadata.
    try:host=load_live_host(options.authorization,runtime)
    except HostFailure as error:args.error('本轮未启用：'+error.code)
    try:
        metadata=json.loads(POINTER.read_text())
        path=safe_credential_path(metadata['path'])
        deadline=metadata['expires_at']
        if not path or type(deadline) not in (int,float) or not time.time()<deadline<=time.time()+3600:
            raise ValueError()
    except (OSError,ValueError,KeyError,TypeError):
        args.error('请先在本轮终端入口完成临时配置。')
    game=GameService(runtime/'sessions.sqlite3',host=host,story_ids=['S03'],single_session=True)
    Handler.service=game
    # A port conflict is explicit: never accidentally open another service's page.
    try:server=ThreadingHTTPServer(('127.0.0.1',options.port),Handler)
    except OSError:
        host.provider.budget.stop('startup_failed')
        cleanup_credential(str(path))
        args.error('试玩端口被占用，未启动或调用模型；临时配置已清理。')
    lifecycle=RoundLifecycle(game,host.provider.budget,
        min(deadline,host.provider.budget.authorization['expires_at']),str(path),runtime/'cleanup.json')
    stopped=threading.Event()
    def monitor():
        while not stopped.wait(1):lifecycle.tick()
    threading.Thread(target=monitor,daemon=True).start()
    print('夜汤｜本轮单人试玩：http://127.0.0.1:'+str(server.server_port)+'/?v=0.9.3&quiet=1',flush=True)
    print('仅本机开放。本局结束、停止或最多60分钟后删除临时密钥；失败不自动重试。',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        stopped.set()
        lifecycle.finish('server_stopped')
        server.server_close()
        game.db.close()

if __name__=='__main__':main()
