"""Local, terminal-only API key handoff. Never print the key."""
import getpass, json, os, stat, sys, tempfile, time
from pathlib import Path

STATE=Path('/Users/zhujiayi/Documents/Codex/2026-10-22/ai-game/work/model_api_execution')

def main():
    print('海龟汤｜本轮千问测试的本机配置')
    print('地域：华北2（北京）。此步骤不调用模型、不产生API费用。')
    print('密钥将暂存于仅当前用户可读的系统临时目录，30分钟后不再接受，测试完成后删除。')
    if not sys.stdin.isatty():raise RuntimeError('请在本机终端运行，避免明文回显。')
    key=getpass.getpass('粘贴百炼API Key后按回车（输入不显示）：').strip()
    if not key or any(ch.isspace() for ch in key):raise RuntimeError('密钥为空或含空白，未保存。')
    STATE.mkdir(parents=True,exist_ok=True)
    pointer=STATE/'credential_location.json'
    if pointer.exists():
        old=Path(json.loads(pointer.read_text())['path'])
        if old.is_file() and str(old).startswith('/private/tmp/ai-turtle-soup-eval-'):
            old.unlink()
            try:old.parent.rmdir()
            except OSError:pass
    d=Path(tempfile.mkdtemp(prefix='ai-turtle-soup-eval-',dir='/private/tmp'))
    os.chmod(d,0o700);p=d/'credentials.json'
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'api_key':key,'region':'cn-beijing','created_at':time.time()},f)
    os.chmod(p,0o600)
    pointer.write_text(json.dumps({'path':str(p)},ensure_ascii=False))
    print('配置已保存。请回聊天告诉我“配置好了”，我再开始6条小样测试。')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,EOFError,KeyboardInterrupt):
        print('配置未完成或已取消；没有调用模型。')
    if sys.stdin.isatty():
        try:input('按回车关闭此窗口。')
        except (EOFError,KeyboardInterrupt):pass
