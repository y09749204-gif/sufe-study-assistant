"""Build-only: fetch fixed official Windows runtimes, record checksums and package licenses."""
import hashlib,json,os,shutil,subprocess,sys,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path(os.environ.get('SUFE_BUILD_CACHE',ROOT/'.local/build-cache'))
RUNTIME=ROOT/'runtime'
SOURCES={
 'python':'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip',
 'node':'https://nodejs.org/dist/v22.22.0/node-v22.22.0-win-x64.zip',
 'postgres':'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip'
}


def fetch(url,name):
    target=CACHE/name
    if not target.exists():
        print('Downloading',name,flush=True)
        temp=target.with_suffix('.part')
        with urllib.request.urlopen(url,timeout=120) as response,temp.open('wb') as output:shutil.copyfileobj(response,output)
        temp.replace(target)
    return target


def main():
    CACHE.mkdir(parents=True,exist_ok=True);RUNTIME.mkdir(exist_ok=True);manifest={}
    for key,url in SOURCES.items():
        archive=fetch(url,url.rsplit('/',1)[1]);digest=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
        manifest[key]={'url':url,'sha256':digest}
        if key=='node':
            sums=urllib.request.urlopen('https://nodejs.org/dist/v22.22.0/SHASUMS256.txt',timeout=30).read().decode()
            expected=next(line.split()[0] for line in sums.splitlines() if line.endswith(archive.name))
            if digest!=expected:raise RuntimeError('Node official checksum mismatch')
        target=RUNTIME/('pgsql' if key=='postgres' else key)
        if target.exists():continue
        target.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                parts=Path(info.filename).parts
                if key=='node':parts=parts[1:]
                elif key=='postgres':
                    if not parts or parts[0]!='pgsql':continue
                    parts=parts[1:]
                    if parts and parts[0] not in ('bin','lib','share','server_license.txt','commandlinetools_3rd_party_licenses.txt'):continue
                if not parts:continue
                dest=target.joinpath(*parts)
                if not dest.resolve().is_relative_to(target.resolve()):raise RuntimeError('Unsafe archive entry')
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(info) as src,dest.open('wb') as out:shutil.copyfileobj(src,out)
    py=RUNTIME/'python';pth=py/'python312._pth'
    pth.write_text('python312.zip\n.\nLib/site-packages\n../../apps/api\n../../app/apps/api\nimport site\n','utf-8')
    if not (py/'Lib/site-packages/pip').exists():
        meta=json.load(urllib.request.urlopen('https://pypi.org/pypi/pip/25.3/json',timeout=30))
        entry=next(e for e in meta['urls'] if e['filename'].endswith('.whl'))
        wheel=fetch(entry['url'],entry['filename'])
        if hashlib.file_digest(wheel.open('rb'),'sha256').hexdigest()!=entry['digests']['sha256']:raise RuntimeError('pip wheel checksum mismatch')
        with zipfile.ZipFile(wheel) as z:z.extractall(py/'Lib/site-packages')
    subprocess.run([str(py/'python.exe'),'-m','pip','install','--disable-pip-version-check','-r',str(ROOT/'apps/api/requirements.txt')],check=True)
    for folder in ('integrations/canvas-browser','integrations/kzkt-browser'):
        subprocess.run(['npm.cmd','install','--omit=dev','--no-audit','--no-fund'],cwd=ROOT/folder,check=True,shell=False)
    (RUNTIME/'manifest.json').write_text(json.dumps(manifest,indent=2),'utf-8')
    print('Runtime ready; checksums recorded in runtime/manifest.json',flush=True)


if __name__=='__main__':main()
