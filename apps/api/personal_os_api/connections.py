"""Browser and local read-only connector lifecycle."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .config import get_settings, save_settings, data_root

router=APIRouter(prefix='/api/connections',tags=['connections'])
ROOT=Path(__file__).resolve().parents[3]


def browser_executable():
    for base in (os.environ.get('PROGRAMFILES',''),os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('LOCALAPPDATA','')):
        for relative in ('Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe'):
            path=Path(base)/relative
            if path.is_file():return str(path)
    raise RuntimeError('请安装 Chrome 或 Edge 后重试')


def browser_env():
    cfg=get_settings()
    return {**os.environ,'ACADEMIC_STORAGE_ROOT':cfg.academic_storage_root,'CANVAS_EXPECTED_USER_ID':cfg.canvas_expected_user_id,
        'CANVAS_BASE_URL':cfg.canvas_base_url,'KZKT_BASE_URL':cfg.kzkt_base_url,'CANVAS_CHROME_PATH':browser_executable(),
        'KZKT_CHROME_PATH':browser_executable(),'PERSONAL_OS_API_BASE_URL':f'http://127.0.0.1:{cfg.personal_os_port}',
        'PERSONAL_OS_TOOL_TOKEN':cfg.personal_os_tool_token,'KZKT_PROCESS':'false'}


def start_browser(provider,command):
    if provider not in ('canvas','kzkt'):raise ValueError('Unknown provider')
    env=browser_env()
    script=ROOT/f'integrations/{provider}-browser/cli.mjs'
    node=os.environ.get('SUFE_NODE') or shutil.which('node')
    if not node:raise RuntimeError('Node runtime not found')
    logs=data_root()/'logs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/f'{provider}.log').open('ab') as output:
        subprocess.Popen([node,str(script),command],cwd=ROOT,env=env,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'status':'started'}


@router.post('/{provider}/login')
def login(provider: str):
    try:return start_browser(provider,'login')
    except (RuntimeError,ValueError) as e:raise HTTPException(409,str(e))


@router.post('/canvas/identify')
def identify():
    try:return start_browser('canvas','identify')
    except RuntimeError as e:raise HTTPException(409,str(e))


class CanvasIdentity(BaseModel):
    user_id: str = Field(pattern=r'^\d+$')


@router.post('/canvas/bind')
def bind_canvas(body:CanvasIdentity):
    cfg=get_settings(); path=Path(cfg.academic_storage_root)/'.runtime/canvas/identity.json'
    if not path.exists() or json.loads(path.read_text('utf-8')).get('user_id')!=body.user_id:
        raise HTTPException(409,'请先读取当前登录账号并核对')
    if cfg.canvas_expected_user_id and cfg.canvas_expected_user_id!=body.user_id:
        raise HTTPException(409,'已有绑定不能切换账号；请使用独立应用数据目录')
    values=cfg.values;values['canvas_user_id']=body.user_id;save_settings(values)
    return {'bound':True}


@router.get('/canvas/identity')
def canvas_identity():
    p=Path(get_settings().academic_storage_root)/'.runtime/canvas/identity.json'
    return json.loads(p.read_text('utf-8')) if p.exists() else {'status':'not_identified'}


@router.get('/wecom/accounts')
def wecom_accounts():
    root=Path.home()/'Documents/WXWork'
    return [{'account_id':p.name,'data_dir':str(p/'Data')} for p in root.glob('*') if p.name.isdigit() and (p/'Data/message.db').is_file()]


class WecomConfig(BaseModel):
    account_id:str=Field(pattern=r'^\d+$')
    organization_id:str=Field(pattern=r'^\d+$')
    expected_name:str=Field(min_length=1,max_length=80)
    expected_organization:str=Field(default='上海财经大学',min_length=1,max_length=100)


@router.put('/wecom')
def configure_wecom(body:WecomConfig):
    if body.account_id not in {a['account_id'] for a in wecom_accounts()}:raise HTTPException(422,'未找到对应本机企微账号')
    values=get_settings().values
    old=values.get('wecom')
    if old and old['account_id']!=body.account_id:raise HTTPException(409,'更换账号需独立数据目录，避免会话混用')
    values['wecom']=body.model_dump();save_settings(values)
    return {'configured':True,'verified':False}


@router.post('/wecom/discover-key')
def discover_key():
    cfg=get_settings().values.get('wecom')
    if not cfg:raise HTTPException(409,'请先选择企微账号并填写身份')
    private=data_root()/'wecom';private.mkdir(parents=True,exist_ok=True)
    source=Path.home()/'Documents/WXWork'/cfg['account_id']/'Data/message.db'
    result=subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'scripts/discover-wecom-key.ps1'),'-Database',str(source),'-KeyFile',str(private/'key.dpapi')],env={**os.environ,'SUFE_DATA_DIR':str(data_root())},capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise HTTPException(409,'密钥发现失败；请确认本人企微已登录且客户端版本受支持')
    return {'key_saved':True,'identity_verified':False}


def capture_wecom():
    result=subprocess.run([sys.executable,str(ROOT/'scripts/capture-wecom.py')],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'apps/api')},capture_output=True,timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('企微只读同步失败：请检查账号身份、密钥和客户端版本')
    return data_root()/'wecom/evidence.sqlite'


@router.post("/kzkt/check")
def check_kzkt():
    try:return start_browser("kzkt", "status")
    except RuntimeError as e:raise HTTPException(409,str(e))
