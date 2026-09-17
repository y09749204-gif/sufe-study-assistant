"""Inspect an unpacked Windows build before distributing an installer.

Checks forbidden user-data files, local profile paths and common credentials.
Does not replace dependency licensing or manual release review.
"""
import os
import re
import sys
from pathlib import Path

root=Path(sys.argv[1]).resolve()
if not (root/'resources').is_dir():
    raise SystemExit('Pass a win-unpacked directory')
patterns=[re.compile(rb'(?i)(?:(?<![a-z0-9_-])sk-[a-z0-9_-]{24,256}(?![a-z0-9_-])|gh[pousr]_[a-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----(?:\s|\\n)+[a-z0-9+/]{32})')]
profile=os.environ.get('USERPROFILE','')
needles=[]
if profile:
    for value in (profile,profile.replace('\\','/')):
        needles.extend([value.lower().encode('utf-8'),value.lower().encode('utf-16-le')])
failures=set();count=0
for path in root.rglob('*'):
    if not path.is_file():continue
    count+=1;name=path.relative_to(root).as_posix()
    if path.suffix.lower() in {'.db','.sqlite','.dump','.dpapi','.mp4','.mp3','.wav','.pyc'} or path.name=='.env':
        failures.add(name+': forbidden artifact')
    with path.open('rb') as stream:
        tail=b''
        while block:=stream.read(1024*1024):
            data=tail+block
            if any(n in data.lower() for n in needles) or any(p.search(data) for p in patterns):
                failures.add(name+': possible private data')
                break
            tail=data[-512:]
if failures:
    print('\n'.join(sorted(failures)));raise SystemExit(1)
print(f'Artifact scan passed: {count} files; no detected profile path, common credential, user-data file or bytecode.')
