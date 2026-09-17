"""Runs in an isolated process so configured identities cannot leak across runs."""
import json
from pathlib import Path
import win32crypt
from personal_os_api.config import get_settings,data_root
from personal_os_api import wecom_capture as reader
from personal_os_api.wecom_snapshot import snapshot
from personal_os_api.vendor.wecom.crypto import verify_key


def main():
    cfg=get_settings().values['wecom']
    reader.OWNER=int(cfg['account_id']);reader.CORP=int(cfg['organization_id'])
    reader.EXPECTED_NAME=cfg['expected_name'];reader.EXPECTED_ORG=cfg['expected_organization']
    private=data_root()/'wecom';source=Path.home()/'Documents/WXWork'/cfg['account_id']/'Data'
    # PowerShell SecureString DPAPI payload is UTF-16LE encoded text; unwrap with PowerShell without printing secrets.
    import subprocess,os
    command="$s=Get-Content -LiteralPath $env:SUFE_KEY_FILE -Raw | ConvertTo-SecureString; $p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }"
    output=subprocess.run(['powershell.exe','-NoProfile','-Command',command],env={**os.environ,'SUFE_KEY_FILE':str(private/'key.dpapi')},capture_output=True,text=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    keys=json.loads(output.stdout);keys=[keys] if isinstance(keys,str) else keys
    keys=[bytes.fromhex(k) for k in keys]
    for name in ('company.db','user.db','session.db','message.db','file.db'):
        p=source/name
        with p.open('rb') as handle:first=handle.read(4096)
        key=next(k for k in keys if verify_key(k,first))
        snapshot(p,private/'snapshot'/name,key)
    report=reader.capture(private/'snapshot',private/'evidence.sqlite',cache_directory=source.parent/'Cache/File')
    (private/'status.json').write_text(json.dumps({'status':'ready','identity':reader.validate_identity(private/'snapshot')},ensure_ascii=False),'utf-8')


if __name__=='__main__':
    try:main()
    except Exception:
        raise SystemExit('WeCom capture failed; no credentials or raw payload are printed')
