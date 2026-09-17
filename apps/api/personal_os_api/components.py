import importlib.util
import shutil
import subprocess
import sys
import os
from pathlib import Path
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
from typing import Literal
from .config import data_root,get_settings,save_settings

router=APIRouter(prefix='/api/components',tags=['components'])


@router.get('')
def status():
    return {'whisper_installed':importlib.util.find_spec('faster_whisper') is not None,
        'whisper_enabled':get_settings().values.get('whisper_enabled',False),
        'libreoffice_installed':bool(soffice()),'ollama_installed':bool(shutil.which('ollama')),
        'downloads':{'ollama':'https://ollama.com/download/windows','libreoffice':'https://www.libreoffice.org/download/download-libreoffice/'}}


def soffice():
    candidates=[shutil.which('soffice'), str(Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'LibreOffice/program/soffice.exe')]
    return next((p for p in candidates if p and Path(p).is_file()),None)


class WhisperSetup(BaseModel):
    model:Literal['tiny','base','small','medium']='small'
    device:Literal['auto','cpu']='auto'
    download_confirmed:bool=False


@router.post('/whisper')
def install_whisper(body:WhisperSetup):
    if not body.download_confirmed:raise HTTPException(409,'请确认下载转写组件和模型，可能占用数 GB')
    # Explicit user action only. Run in a child and expose status, never claim success before verification.
    root=data_root();logs=root/'logs';logs.mkdir(parents=True,exist_ok=True)
    marker=root/'whisper-install.json'
    if marker.exists():
        import json
        state=json.loads(marker.read_text('utf-8'))
        if state.get('status')=='running':raise HTTPException(409,'转写组件正在安装，请稍后查看结果')
    import json
    marker.write_text(json.dumps({'status':'running'}),'utf-8')
    script=Path(__file__).resolve().parents[3]/'scripts/install-whisper.py'
    with (logs/'whisper-install.log').open('ab') as log:
        subprocess.Popen([sys.executable,str(script),body.model,body.device],env=os.environ.copy(),stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'status':'running'}


@router.get('/whisper')
def whisper_status():
    import json
    p=data_root()/'whisper-install.json'
    return json.loads(p.read_text('utf-8')) if p.exists() else {'status':'not_installed'}
