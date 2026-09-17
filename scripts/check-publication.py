"""Scan working tree, index and all reachable Git blobs before publication.

Pattern checks complement review; they cannot prove absence of every secret.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r'(?i)(?:sk-[a-z0-9_-]{24,}|gh[pousr]_[a-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
    re.compile(r'(?:C:[/\\]Users[/\\][^/\\\s]+[/\\]|E:[/\\]PersonalOS|D:[/\\]CodexASR)'),
]
FORBIDDEN = {'.dpapi', '.sqlite', '.db', '.dump', '.mp4', '.mp3', '.wav'}
failures = set()
scanned = set()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def check(name, content, context):
    p = Path(name)
    if (p.suffix.lower() in FORBIDDEN or p.name == '.env'
            or any(part in ('.local', '.runtime', 'node_modules', 'runtime') for part in p.parts)):
        failures.add(f'{context}: {name}: forbidden artifact')
    if any(pattern.search(content.decode('utf-8', errors='replace')) for pattern in PATTERNS):
        failures.add(f'{context}: {name}: possible private data')


for entry in git('ls-files', '--stage', '-z').decode().split('\0'):
    if not entry:
        continue
    metadata, name = entry.split('\t', 1)
    oid = metadata.split()[1]
    check(name, git('cat-file', 'blob', oid), 'index')
    scanned.add(oid)
    path = ROOT / name
    if path.is_file():
        check(name, path.read_bytes(), 'worktree')

if subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    for line in git('rev-list', '--objects', '--all').decode().splitlines():
        oid, _, name = line.partition(' ')
        if not name or oid in scanned or git('cat-file', '-t', oid).strip() != b'blob':
            continue
        check(name, git('cat-file', 'blob', oid), 'history')
        scanned.add(oid)

if failures:
    print('\n'.join(sorted(failures)))
    sys.exit(1)
print(f'Publication scan passed: index, worktree and {len(scanned)} unique Git blobs. Manual review still required.')
