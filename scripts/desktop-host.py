"""Per-user runtime supervisor. Owns only the processes/data it creates."""
import json,os,secrets,socket,subprocess,sys,time,threading,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.environ.get('SUFE_DATA_DIR',Path(os.environ.get('LOCALAPPDATA',Path.home()))/'SufeStudyAssistant')).resolve()
RUNTIME=Path(os.environ.get('SUFE_RUNTIME_DIR',ROOT/'runtime')).resolve()


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def main():
    DATA.mkdir(parents=True,exist_ok=True);logs=DATA/'logs';logs.mkdir(exist_ok=True)
    if os.name=='nt':
        import getpass
        subprocess.run(['icacls',str(DATA),'/inheritance:r','/grant:r',f'{getpass.getuser()}:(OI)(CI)F','SYSTEM:(OI)(CI)F'],stdout=subprocess.DEVNULL,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    log=(logs/'host.log').open('ab',buffering=0)
    pg=RUNTIME/'pgsql/bin';dbdir=DATA/'postgres';credentials=DATA/'database.json'
    if credentials.exists():cred=json.loads(credentials.read_text('utf-8'))
    else:
        cred={'password':secrets.token_hex(24)};credentials.write_text(json.dumps(cred),'utf-8')
    def run(args,env=None):
        return subprocess.run([str(x) for x in args],cwd=ROOT,env=env,stdout=log,stderr=log,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180)
    if not (dbdir/'PG_VERSION').exists():
        pw=DATA/'init-password';pw.write_text(cred['password'],'utf-8')
        try:run([pg/'initdb.exe','-D',dbdir,'-U','sufe','--pwfile',pw,'--auth=scram-sha-256','--encoding=UTF8','--locale=C','--no-locale'])
        finally:pw.unlink(missing_ok=True)
    dbport=port();apiport=port();token=secrets.token_urlsafe(32)
    env={**os.environ,'SUFE_DATA_DIR':str(DATA),'PYTHONPATH':str(ROOT/'apps/api'),'SUFE_API_PORT':str(apiport),'SUFE_API_TOKEN':token,'SUFE_NODE':str(RUNTIME/'node/node.exe'),'PGPASSWORD':cred['password'],'DATABASE_URL':f"postgresql+psycopg://sufe:{cred['password']}@127.0.0.1:{dbport}/sufe_study",'SUFE_WEB_DIR':str(ROOT/'apps/web/out')}
    children=[];started=False
    try:
        # A surviving owned cluster is stopped cleanly before reconfiguration; never use global services.
        check=subprocess.run([str(pg/'pg_ctl.exe'),'status','-D',str(dbdir)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check.returncode==0:run([pg/'pg_ctl.exe','stop','-D',dbdir,'-m','fast','-w'])
        run([pg/'pg_ctl.exe','start','-D',dbdir,'-l',logs/'postgres.log','-o',f'-h 127.0.0.1 -p {dbport}','-w']);started=True
        import psycopg
        with psycopg.connect(host='127.0.0.1',port=dbport,user='sufe',password=cred['password'],dbname='postgres',autocommit=True) as db:
            if not db.execute("SELECT 1 FROM pg_database WHERE datname='sufe_study'").fetchone():db.execute('CREATE DATABASE sufe_study')
        backups=DATA/'backups';backups.mkdir(exist_ok=True)
        if (DATA/'schema-version').exists():run([pg/'pg_dump.exe','-h','127.0.0.1','-p',str(dbport),'-U','sufe','-Fc','-f',backups/f'before-start-{int(time.time())}.dump','sufe_study'],env)
        run([sys.executable,ROOT/'scripts/migrate.py'],env)
        for args in ([sys.executable,'-m','uvicorn','personal_os_api.main:app','--host','127.0.0.1','--port',str(apiport),'--no-access-log'],[sys.executable,'-m','personal_os_api.worker']):
            children.append(subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        for _ in range(120):
            if children[0].poll() is not None:raise RuntimeError('API exited')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{apiport}/health/ready',timeout=1) as response:
                    if response.status==200:break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('API startup timeout')
        print(json.dumps({'url':f'http://127.0.0.1:{apiport}/#token={token}'}),flush=True)
        sys.stdin.readline()
    finally:
        for child in children:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill()
        if started:run([pg/'pg_ctl.exe','stop','-D',dbdir,'-m','fast','-w'])
        log.close()


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'error':'本机服务启动失败，请查看独立应用数据目录 logs/host.log。','type':type(e).__name__}),flush=True)
        sys.exit(1)
