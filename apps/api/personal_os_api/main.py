import hmac
import os
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException,Depends
from fastapi.responses import JSONResponse,FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text,select
from pydantic import BaseModel,Field
from datetime import datetime
from uuid import UUID
from .config import get_settings
from .db import get_db
from .models import Task,TaskStatus
from .academics import router as academics
from .calendar import router as calendar
from .setup_api import router as setup
from .connections import router as connections
from .kzkt_queue import router as queue
from .components import router as components
from .onboarding import router as onboarding
from .integration import router as integration

app=FastAPI(title='上财学业助手',version='0.1.0.dev2')


@app.middleware('http')
async def authenticate(request:Request,call_next):
    if request.url.path.startswith('/api/'):
        expected=get_settings().personal_os_tool_token
        received=request.headers.get('x-personal-os-token','') or request.cookies.get('sufe-session','')
        if not expected or not hmac.compare_digest(expected,received):return JSONResponse({'detail':'Unauthorized'},status_code=401)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Invalid origin'},status_code=403)
    return await call_next(request)


@app.get('/health')
def health():return {'app':'sufe-study-assistant','status':'ok'}


@app.get('/health/ready')
def ready(db=Depends(get_db)):
    db.execute(text('SELECT 1'));return {'app':'sufe-study-assistant','status':'ready'}


@app.post('/session')
async def session(request:Request):
    token=request.headers.get('x-personal-os-token','')
    expected=get_settings().personal_os_tool_token
    if not expected or not hmac.compare_digest(token,expected):raise HTTPException(401)
    response=JSONResponse({'ok':True});response.set_cookie('sufe-session',expected,httponly=True,samesite='strict')
    return response


class TaskInput(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    description:str|None=None
    project_id:UUID|None=None
    deadline_at:datetime|None=None


@app.get('/api/tasks')
def tasks(db=Depends(get_db)):
    return list(db.scalars(select(Task).order_by(Task.created_at.desc())))


@app.post('/api/tasks')
def create_task(body:TaskInput,db=Depends(get_db)):
    task=Task(**body.model_dump(),status=TaskStatus.todo);db.add(task);db.commit();db.refresh(task);return task


class TaskChange(BaseModel):
    status:TaskStatus


@app.patch('/api/tasks/{task_id}')
def update_task(task_id:UUID,body:TaskChange,db=Depends(get_db)):
    task=db.get(Task,task_id)
    if not task:raise HTTPException(404)
    task.status=body.status;task.completed_at=datetime.now().astimezone() if body.status==TaskStatus.done else None
    db.commit();return {'id':str(task.id),'status':task.status}


for router in (setup,connections,academics,calendar,queue,components,onboarding,integration):app.include_router(router)
web=Path(os.environ.get('SUFE_WEB_DIR',str(Path(__file__).resolve().parents[2]/'web/out')))
if web.exists():app.mount('/',StaticFiles(directory=web,html=True),name='web')
