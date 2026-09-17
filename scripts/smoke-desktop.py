"""Exercise the packaged supervisor against a fresh disposable user directory.

Pass the unpacked package's resources directory. Does not replace full installer QA.
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

resources = Path(sys.argv[1]).resolve()
root = resources / 'app'
runtime = resources / 'runtime'
data = Path(tempfile.mkdtemp(prefix='sufe-smoke-')) / '独立用户'
env = {**os.environ, 'SUFE_DATA_DIR': str(data), 'SUFE_RUNTIME_DIR': str(runtime)}
process = subprocess.Popen([str(runtime/'python/python.exe'), '-B', str(root/'scripts/desktop-host.py')],
    cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
lines = queue.Queue()
threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
try:
    ready = json.loads(lines.get(timeout=100))
    if 'url' not in ready:
        raise RuntimeError(ready.get('error', 'No startup URL'))
    origin, token = ready['url'].split('/#token=')
    with urllib.request.urlopen(origin+'/health') as response:
        assert json.load(response)['app'] == 'sufe-study-assistant'
    request = urllib.request.Request(origin+'/api/setup', headers={'x-personal-os-token': token})
    with urllib.request.urlopen(request) as response:
        config = json.load(response)
    assert not config.get('configured')
    assert (data/'postgres/PG_VERSION').exists()
    print('PASS: packaged runtime, empty user configuration, Unicode data directory, authenticated API.')
finally:
    if process.poll() is None:
        process.communicate('stop\n', timeout=30)
    assert process.returncode == 0, 'Supervisor failed; inspect disposable data logs'
