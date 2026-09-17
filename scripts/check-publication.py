"""Fail on forbidden tracked artifacts and common credential / private identity patterns."""
import re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
patterns=[re.compile(r'(?i)(?:sk-[a-z0-9_-]{24,}|gh[pousr]_[a-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),re.compile(r'(?:C:[/\\]Users[/\\][^/\\\s]+[/\\]|E:[/\\]PersonalOS|D:[/\\]CodexASR)')]
forbidden={'.dpapi','.sqlite','.db','.dump','.mp4','.mp3','.wav'}
failures=[]
for name in filter(None,paths):
    p=ROOT/name
    if p.suffix.lower() in forbidden or any(part in ('.local','.runtime','node_modules','runtime') for part in p.relative_to(ROOT).parts):failures.append(name+': forbidden artifact');continue
    if p.name=='.env':failures.append(name+': environment secrets');continue
    if not p.is_file():continue
    text=p.read_text('utf-8',errors='replace')
    if any(pattern.search(text) for pattern in patterns):failures.append(name+': possible private data')
if failures:
    print('\n'.join(failures));sys.exit(1)
print(f'Publication scan passed: {len(list(filter(None,paths)))} tracked files. Manual review still required.')
