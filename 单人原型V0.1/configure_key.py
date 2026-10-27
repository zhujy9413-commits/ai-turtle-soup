# -*- coding: utf-8 -*-
"""Hidden terminal input for the approved one-session test, with a one-hour ceiling."""
import getpass
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from player_round import POINTER, cleanup_credential

def main():
    print('夜汤｜本轮模型调用临时配置')
    print('百炼北京业务空间的Key；本步骤不调用模型。')
    print('本机临时保存最多60分钟；本轮结束或停止后删除，不写入钥匙串。')
    if not sys.stdin.isatty():raise RuntimeError()
    secret=getpass.getpass('粘贴API Key后按回车（输入不显示）：').strip()
    if not secret or any(c.isspace() for c in secret):raise RuntimeError()
    if POINTER.exists():
        old=json.loads(POINTER.read_text())
        result=cleanup_credential(old['path'])
        if not result['temporary_key_removed']:raise RuntimeError()
    folder=Path(tempfile.mkdtemp(prefix='ai-turtle-soup-eval-',dir='/private/tmp'))
    os.chmod(folder,0o700)
    created=time.time()
    path=folder/'credentials.json'
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as handle:
        json.dump({'api_key':secret,'region':'cn-beijing','created_at':created,'ttl_seconds':3600},handle)
    POINTER.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(POINTER,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(descriptor,'w') as handle:
        json.dump({'path':str(path),'expires_at':created+3600},handle)
    print('配置完成，请回聊天告诉我“配置好了”。')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,RuntimeError,EOFError,KeyboardInterrupt):
        print('未完成配置；没有调用模型。')
    if sys.stdin.isatty():
        try:input('按回车关闭此窗口。')
        except (EOFError,KeyboardInterrupt):pass
