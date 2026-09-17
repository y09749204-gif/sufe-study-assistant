"""Versioned, app-independent integration entry points."""
import json
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from .config import data_root, get_settings
from .connections import start_browser
from .db import get_db

router = APIRouter(prefix='/api/integration/v1', tags=['integration'])


@router.get('/status')
def status():
    return {'app':'sufe-study-assistant','protocol':1,'capabilities':['courses.read','calendar.read','tasks.read','tasks.complete','sync.canvas','sync.kzkt'], 'configured':bool(get_settings().values.get('term_id'))}


@router.post('/sync/{provider}')
def sync(provider: str, db=Depends(get_db)):
    if provider not in ('canvas','kzkt'): raise HTTPException(422, '不支持该同步来源')
    if provider == 'canvas' and not get_settings().canvas_expected_user_id: raise HTTPException(409, '请先在学业助手中登录并绑定 Canvas 身份')
    if provider == 'kzkt':
        from .kzkt_queue import enqueue, queue_state
        result=enqueue(db)
        if not result['courses']:raise HTTPException(409,'没有可同步的课程')
        return {**result,'job_id':result['batch_id'],'paused':queue_state(result['batch_id'],db)['paused']}
    try: return start_browser(provider, 'sync')
    except (RuntimeError, OSError): raise HTTPException(409, '无法启动同步，请在学业助手中检查平台配置') from None


@router.get('/jobs/{job_id}')
def job(job_id: UUID, db=Depends(get_db)):
    import os
    path = data_root() / 'jobs' / f'{job_id}.json'
    if not path.exists():
        from .kzkt_queue import queue_state
        state=queue_state(str(job_id),db)
        if not state['tasks']:raise HTTPException(404, '任务不存在')
        counts=state['counts']
        if counts['running']:status='running'
        elif counts['queued'] or counts['retry_wait']:status='paused' if state['paused'] else 'queued'
        elif counts['failed'] or counts['blocked']:status='failed'
        elif counts['needs_confirmation']:status='needs_confirmation'
        else:status='success'
        return {'id':str(job_id),'provider':'kzkt','status':status,**state}
    value = json.loads(path.read_text('utf-8'))
    if value['status']=='running' and value.get('run_id')!=os.environ.get('SUFE_RUN_ID'):
        value['status']='interrupted'
    value.pop('run_id',None)
    return value
