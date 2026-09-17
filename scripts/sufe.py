"""Public CLI: JSON on stdout, stable exit codes, no direct database access."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class CLIError(Exception):
    def __init__(self, code, message): self.code, self.message = code, message


def connection(data_dir=None):
    root = Path(data_dir or os.environ.get('SUFE_DATA_DIR') or Path(os.environ.get('APPDATA', Path.home())) / 'SufeStudyAssistant')
    try:
        info = json.loads((root / 'connection.json').read_text('utf-8'))
        url = urllib.parse.urlparse(info['url'])
        if info.get('app') != 'sufe-study-assistant' or info.get('protocol') != 1 or url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
            raise ValueError()
        import win32crypt
        token = win32crypt.CryptUnprotectData((root / 'connection.dpapi').read_bytes(), None, None, None, 0)[1].decode()
        return info, token
    except (OSError, ValueError, KeyError, ImportError):
        raise CLIError('not_running', '请先启动上财学业助手；也可用 --data-dir 指定独立实例的数据目录') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def request(info, token, path, method='GET', body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(info['url'] + path, data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={'X-Personal-OS-Token':token, 'Content-Type':'application/json'})
    try:
        with opener.open(req, timeout=40) as response: return json.load(response)
    except urllib.error.HTTPError as exc:
        raise CLIError('api_error', f'操作未完成（HTTP {exc.code}），请在学业助手中检查连接或任务状态') from None
    except (OSError, ValueError):
        raise CLIError('unavailable', '学业助手服务不可用，请重新启动；写入结果不确定时请先查询再重试') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='上财学业助手 CLI v1（应用需已启动；不读取 Personal OS 数据库）')
    parser.add_argument('--data-dir')
    parser.add_argument('--json', action='store_true', help='输出结构化 JSON（默认）')
    parser.add_argument('command', choices=['status','courses','calendar','tasks','sync','jobs','open'])
    parser.add_argument('action', nargs='?')
    parser.add_argument('id', nargs='?')
    parser.add_argument('--start'); parser.add_argument('--end')
    args = parser.parse_args(argv)
    try:
        info, token = connection(args.data_dir)
        # Validate the live application identity before any write.
        health = request(info, token, '/health')
        if health.get('app') != 'sufe-study-assistant': raise CLIError('wrong_service', '目标不是学业助手')
        cmd, action = args.command, args.action
        if cmd == 'status': result = request(info, token, '/api/integration/v1/status')
        elif cmd == 'open':
            import webbrowser
            webbrowser.open(info['url'] + '/#token=' + token)
            result = {'opened':True}
        elif cmd == 'courses' and action == 'list': result = request(info, token, '/api/academics/courses')
        elif cmd == 'calendar' and action == 'list':
            query = urllib.parse.urlencode({k:v for k,v in {'start':args.start,'end':args.end}.items() if v})
            result = request(info, token, '/api/calendar' + ('?' + query if query else ''))
        elif cmd == 'tasks' and action == 'list': result = request(info, token, '/api/tasks')
        elif cmd == 'tasks' and action in ('complete','reopen') and args.id:
            from uuid import UUID
            result = request(info, token, '/api/tasks/' + str(UUID(args.id)), 'PATCH', {'status':'done' if action=='complete' else 'todo'})
        elif cmd == 'sync' and action in ('canvas','kzkt'):
            result = request(info, token, '/api/integration/v1/sync/' + action, 'POST', {})
        elif cmd == 'jobs' and action == 'status' and args.id:
            from uuid import UUID
            result = request(info, token, '/api/integration/v1/jobs/' + str(UUID(args.id)))
        else: raise CLIError('usage', '无效命令，请使用 --help 查看调用方式')
        print(json.dumps({'ok':True,'protocol':1,'data':result},ensure_ascii=False,default=str)); return 0
    except (CLIError, ValueError) as exc:
        print(json.dumps({'ok':False,'protocol':1,'error':{'code':getattr(exc,'code','usage'),'message':getattr(exc,'message','参数无效')}},ensure_ascii=False)); return 2 if getattr(exc,'code','usage')=='usage' else 3


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
