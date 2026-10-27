# -*- coding: utf-8 -*-
"""Terminal-only temporary key setup. This script cannot call a model."""
import getpass
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

STATE=Path('/Users/zhujiayi/Documents/ChatGPT/AI 海龟汤/work/model_api_execution_v014')

def main():
    print('夜汤｜本轮真实主持联调')
    print('使用百炼华北2（北京）业务空间的Key。本步骤不调用模型。')
    print('密钥仅本机临时保存，30分钟有效；本轮结束或停止后删除。')
    if not sys.stdin.isatty():
        raise RuntimeError()
    key=getpass.getpass('粘贴API Key后按回车（输入不显示）：').strip()
    if not key or any(x.isspace() for x in key):
        raise RuntimeError()
    STATE.mkdir(parents=True,exist_ok=True)
    pointer=STATE/'credential_location.json'
    if pointer.exists():
        old=Path(json.loads(pointer.read_text())['path'])
        if str(old).startswith('/private/tmp/ai-turtle-soup-eval-') and not old.is_symlink() and not old.parent.is_symlink() and old.is_file():
            if old.stat().st_uid==os.getuid() and stat.S_IMODE(old.stat().st_mode)==0o600:
                old.unlink()
                try:old.parent.rmdir()
                except OSError:pass
    folder=Path(tempfile.mkdtemp(prefix='ai-turtle-soup-eval-',dir='/private/tmp'))
    os.chmod(folder,0o700)
    path=folder/'credentials.json'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump({'api_key':key,'region':'cn-beijing','created_at':time.time()},f)
    pointer.write_text(json.dumps({'path':str(path)}))
    print('配置完成。请回聊天告诉我“配置好了”，本轮无需重复配置。')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,RuntimeError,EOFError,KeyboardInterrupt):
        print('未完成配置；没有调用模型。')
    if sys.stdin.isatty():
        try:input('按回车关闭此窗口。')
        except (EOFError,KeyboardInterrupt):pass
