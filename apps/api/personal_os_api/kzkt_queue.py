"""Durable stage-based queue. Only the dedicated worker executes long operations."""
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import select, text, or_, case
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from personal_os_api.db import get_db, engine, SessionLocal
from personal_os_api.models import KzktQueueTask as Job, KzktQueueControl as Control, AcademicCourse, AcademicTerm, AutomationProfile, CourseRecording, LessonReview, RecordingSlideSegment, RecordingTranscript
from personal_os_api.kzkt import REVIEW_VERSION, parse_time, match_course, process_recording, runtime_health, storage_root

router=APIRouter(prefix='/api/academics/kzkt/queue',tags=['academics'])
ROOT=Path(__file__).resolve().parents[3]
def now(): return datetime.now(timezone.utc)

def enqueue_requirements(db,course_id=None,recording_id=None):
    from .classroom_requirements import VERSION
    db.execute(text('SELECT pg_advisory_xact_lock(70631986)'))
    eligible={c.id for c in eligible_courses(db,course_id)}
    batch=str(uuid.uuid4());jobs=[];reused=0
    query=select(CourseRecording).where(CourseRecording.provider=='kzkt',CourseRecording.course_id.in_(eligible))
    if recording_id:query=query.where(CourseRecording.id==recording_id)
    for record in db.scalars(query.order_by(CourseRecording.created_at.desc())):
        transcript=db.scalar(select(RecordingTranscript).where(RecordingTranscript.recording_id==record.id).order_by(RecordingTranscript.created_at.desc()))
        if not transcript:continue
        key=f'requirements:{record.id}:{transcript.content_hash}:{VERSION}'
        job=db.scalar(select(Job).where(Job.dedupe_key==key))
        if job:
            reused+=1;job.batches=list(dict.fromkeys([*job.batches,batch]))
        else:
            for old in db.scalars(select(Job).where(Job.kind=='requirements',Job.recording_id==record.id,
                Job.status.in_(['queued','retry_wait','failed','blocked']),Job.dedupe_key!=key)):
                old.status='superseded';old.error='已由新版课堂要求提取替代';old.finished_at=now()
            job=Job(dedupe_key=key,course_id=record.course_id,recording_id=record.id,external_id=record.external_id,title=record.title,
                kind='requirements',stage='requirements',status='queued',batches=[batch],payload={},result={},taught_at=parse_time((record.metadata_ or {}).get("taught_at")))
            db.add(job);db.flush()
        jobs.append(str(job.id))
    db.commit();return {'batch_id':batch,'task_ids':jobs,'enqueued':len(jobs)-reused,'reused':reused}

def eligible_courses(db,course_id=None):
    courses=list(db.scalars(select(AcademicCourse).join(AcademicTerm).where(AcademicCourse.status=='active',AcademicCourse.automation_profile!=AutomationProfile.calendar_only,AcademicTerm.status=='active')))
    today=now().astimezone().date()
    return [c for c in courses if (not course_id or c.id==course_id) and (lambda t:t.starts_on<=today<t.starts_on+timedelta(weeks=t.teaching_weeks))(db.get(AcademicTerm,c.term_id))]

def enqueue(db,course_id=None,external_id=None):
    batch=str(uuid.uuid4()); jobs=[]
    # Transaction-level serialization also protects manual/periodic discovery coalescing.
    db.execute(text('SELECT pg_advisory_xact_lock(70631986)'))
    for c in eligible_courses(db,course_id):
        key=f'discover:{c.id}:{external_id or "all"}'
        job=db.scalar(select(Job).where(Job.dedupe_key==key))
        if not job:
            job=Job(dedupe_key=key,course_id=c.id,title=c.name,kind='discovery',stage='discover',status='queued',external_id=external_id,batches=[batch]);db.add(job)
        else:
            job.batches=[*job.batches,batch]
            if job.status in ('success','failed','blocked'):
                job.status='queued';job.attempt=0;job.error=None;job.next_attempt_at=None;job.finished_at=None
        db.flush();jobs.append(str(job.id))
    db.commit()
    return {'batch_id':batch,'task_ids':jobs,'status':'queued','courses':len(jobs)}

def add_manifest(db,parent,manifest):
    counts={'discovered':len(manifest.get('recordings',[])),'enqueued':0,'reused':0,'needs_confirmation':0,'errors':manifest.get('errors',[])}
    for item in manifest.get('recordings',[]):
        from .config import get_settings
        cfg=get_settings().values
        mode=cfg.get('course_modes',{}).get(str(parent.course_id),cfg.get('replay_mode','text'))
        from .ai_provider import model_identity
        key=f'{parent.course_id}:{item["external_id"]}:{REVIEW_VERSION}:{mode}:{model_identity()}'
        job=db.scalar(select(Job).where(Job.dedupe_key==key))
        if job:
            job.batches=list(dict.fromkeys([*job.batches,*parent.batches]));counts['reused']+=1;continue
        matched=match_course(db,item.get('course_name',''),item.get('section_code'))
        valid=matched and matched.id==parent.course_id and item.get('participation_evidence',{}).get('filter')=='我参与的'
        record=db.scalar(select(CourseRecording).where(CourseRecording.course_id==parent.course_id,CourseRecording.external_id==item['external_id']).order_by(CourseRecording.created_at.desc()))
        review=db.scalar(select(LessonReview).where(LessonReview.recording_id==record.id).order_by(LessonReview.created_at.desc())) if record else None
        done=review and review.model_name==model_identity() and review.summary.get('review_version')==REVIEW_VERSION and db.scalar(select(RecordingSlideSegment.id).where(RecordingSlideSegment.recording_id==record.id).limit(1))
        status='success' if done else 'queued' if valid else 'needs_confirmation'
        db.add(Job(dedupe_key=key,course_id=parent.course_id,recording_id=record.id if record else None,external_id=item['external_id'],title=item.get('title') or parent.title,kind='recording',stage='complete' if done else 'download',status=status,batches=list(parent.batches),payload=item,taught_at=parse_time(item.get('taught_at')),finished_at=now() if done else None))
        counts['reused' if done else 'enqueued' if valid else 'needs_confirmation']+=1
    db.commit();return counts

def browser(command,course,external_id=None):
    from personal_os_api.config import get_settings
    from .connections import browser_env
    cfg=get_settings();env={**browser_env(),'KZKT_COURSE_NAME':course.name,'KZKT_PROCESS':'false','ACADEMIC_STORAGE_ROOT':cfg.academic_storage_root,'KZKT_BASE_URL':cfg.kzkt_base_url,'PERSONAL_OS_API_BASE_URL':f'http://{cfg.personal_os_host}:{cfg.personal_os_port}','PERSONAL_OS_TOOL_TOKEN':cfg.personal_os_tool_token}
    env['KZKT_MEDIA_MODE']=cfg.values.get('course_modes',{}).get(str(course.id),cfg.values.get('replay_mode','text'))
    env.pop('KZKT_RECORDING_ID',None)
    if external_id: env['KZKT_RECORDING_ID']=external_id
    done=subprocess.run([os.environ.get('SUFE_NODE','node'),str(ROOT/'integrations/kzkt-browser/cli.mjs'),command],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=14400,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try: result=json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        state=json.loads((storage_root()/'.runtime/kzkt/status.json').read_text(encoding='utf-8'))
        raise RuntimeError(f'{state.get("status")}: {state.get("error") or state.get("message")}')
    if done.returncode and not (command=='discover' and result.get('recordings')): raise RuntimeError(json.dumps(result,ensure_ascii=False))
    return result

def failure(job,error):
    job.error=str(error)
    blocked=any(word in job.error.lower() for word in ['login_required','模型不可用','cuda 不可用','浏览器正在运行','存储空间不足'])
    job.status='blocked' if blocked else 'failed' if job.attempt>=3 else 'retry_wait'
    job.next_attempt_at=now()+timedelta(minutes=1 if blocked else 15)
    if blocked: job.attempt=max(0,job.attempt-1)

def advance(db,job):
    from .config import get_settings
    if job.course_id not in {c.id for c in eligible_courses(db)}: raise RuntimeError('课程已不属于本学期处理范围')
    course=db.get(AcademicCourse,job.course_id)
    if job.stage=='discover':
        job.result=add_manifest(db,job,browser('discover',course,job.external_id))
        if job.result.get('errors'): raise RuntimeError('；'.join(str(e.get('error','发现失败')).split('\n')[0] for e in job.result['errors']))
        job.status='success'
    elif job.stage=='download':
        record=db.get(CourseRecording,job.recording_id) if job.recording_id else None
        if not record or not (record.metadata_ or {}).get('subtitle_text') or (get_settings().values.get('course_modes',{}).get(str(course.id),get_settings().values.get('replay_mode','text'))=='illustrated' and (not record.local_path or not Path(record.local_path).is_file())):
            report=browser('sync',course,job.external_id)
            if report.get('failed_count') or not report.get('recording_ids'): raise RuntimeError(json.dumps(report,ensure_ascii=False))
            job.recording_id=uuid.UUID(report['recording_ids'][0]);db.commit()
        job.stage='review';job.status='queued'
    elif job.stage=='review':
        health=runtime_health()
        record=db.get(CourseRecording,job.recording_id)
        if not (record.metadata_ or {}).get('subtitle_text') and not get_settings().values.get('whisper_enabled'): raise RuntimeError('尚无字幕，请启用本地转写并使用图文模式下载媒体')
        job.result={**job.result,'review':process_recording(db,record)};job.stage='slides' if get_settings().values.get('course_modes',{}).get(str(course.id),get_settings().values.get('replay_mode','text'))=='illustrated' else 'complete';job.status='queued' if job.stage=='slides' else 'success'
    elif job.stage=='slides':
        from personal_os_api.lesson_knowledge import process_recording_slides
        job.result={**job.result,'slides':process_recording_slides(db,job.recording_id)};job.stage='complete';job.status='success'
    elif job.stage=='requirements':
        from .classroom_requirements import process_requirements
        if get_settings().values.get('ai',{}).get('provider','none')=='none':raise RuntimeError('尚未配置 AI')
        record=db.get(CourseRecording,job.recording_id)
        try:job.result=process_requirements(db,record)
        except Exception:
            db.refresh(record);job.result={**(record.metadata_ or {}).get('classroom_requirements',{}),'partial':True};db.commit();raise
        job.stage='complete';job.status='success'
    job.error=None;job.attempt=0;job.next_attempt_at=None
    if job.status=='success':job.finished_at=now()
    db.commit()

def tick():
    # Stage-wide lock is shared with legacy synchronous processing and all workers.
    with engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(70631985)')):return False
        lock.commit()
        try:
            with SessionLocal() as db:
                control=db.get(Control,1)
                if control and control.paused:return False
                # No other stage holds the lock: running rows are interrupted work.
                for stale in db.scalars(select(Job).where(Job.status=='running')): stale.status='queued';stale.error='程序中断后恢复，复用已完成阶段'
                db.commit()
                job=db.scalar(select(Job).where(Job.status.in_(['queued','retry_wait','blocked']),or_(Job.next_attempt_at.is_(None),Job.next_attempt_at<=now())).order_by(case((Job.kind=='requirements',0),(Job.kind=='discovery',1),else_=2),Job.taught_at.desc().nullslast(),Job.created_at).limit(1))
                if not job:return False
                job.status='running';job.started_at=now();job.attempt+=1;db.commit()
                try:advance(db,job)
                except Exception as exc:
                    db.rollback();db.refresh(job);failure(job,exc);db.commit()
                return True
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(70631985)'));lock.commit()

class BatchRequest(BaseModel):
    course_id:uuid.UUID|None=None
    recording_external_id:str|None=None

class RequirementsBatchRequest(BaseModel):
    course_id:uuid.UUID|None=None
    recording_id:uuid.UUID|None=None

@router.post('/requirements/batches')
def requirements_batch(body:RequirementsBatchRequest,db=Depends(get_db)):
    return enqueue_requirements(db,body.course_id,body.recording_id)

@router.post('/batches')
def create_batch(body:BatchRequest,db=Depends(get_db)):
    result=enqueue(db,body.course_id,body.recording_external_id)
    if not result['courses']:raise HTTPException(400,'没有可处理的本学期课程')
    return result

@router.get('')
def queue_state(batch_id:str|None=None,db=Depends(get_db)):
    jobs=list(db.scalars(select(Job).order_by(Job.created_at.desc())))
    if batch_id:jobs=[j for j in jobs if batch_id in j.batches]
    else:
        latest=set();visible=[]
        for j in jobs:
            if j.kind=='requirements':
                if j.recording_id in latest and j.status!='running':continue
                latest.add(j.recording_id)
            visible.append(j)
        jobs=visible
    counts={s:sum(j.status==s for j in jobs) for s in ['queued','running','success','retry_wait','failed','blocked','needs_confirmation','superseded']}
    progress={}
    if counts['running']:
        try:progress=json.loads((storage_root()/'.runtime/kzkt/status.json').read_text(encoding='utf-8')).get('progress',{})
        except (OSError,ValueError):pass
    control=db.get(Control,1)
    return {'paused':bool(control and control.paused),'counts':counts,'progress':progress,'tasks':[{'id':str(j.id),'course_id':str(j.course_id),'recording_id':str(j.recording_id) if j.recording_id else None,'title':j.title,'kind':j.kind,'stage':j.stage,'status':j.status,'attempt':j.attempt,'error':j.error,'batches':j.batches,'result':j.result,'taught_at':j.taught_at,'next_attempt_at':j.next_attempt_at,'started_at':j.started_at,'finished_at':j.finished_at} for j in jobs]}

@router.post('/{action}')
def control_queue(action:str,db=Depends(get_db)):
    if action not in ('pause','resume','retry'):raise HTTPException(400,'未知队列操作')
    control=db.get(Control,1)
    if not control:control=Control(id=1);db.add(control)
    if action in ('pause','resume'):control.paused=action=='pause'
    if action in ('resume','retry'):
        for job in db.scalars(select(Job).where(Job.status.in_(['blocked'] if action=='resume' else ['failed','retry_wait','blocked']))):
            job.status='queued';job.attempt=0;job.error=None;job.next_attempt_at=None
    db.commit();return {'paused':control.paused}



@router.get('/pending-slides')
def pending_slides(db=Depends(get_db)):
    from sqlalchemy import func
    rows=db.execute(select(CourseRecording.id,CourseRecording.course_id,CourseRecording.title,func.count(RecordingSlideSegment.id)).join(RecordingSlideSegment,RecordingSlideSegment.recording_id==CourseRecording.id).where(RecordingSlideSegment.status=='pending').group_by(CourseRecording.id)).all()
    return [{'recording_id':str(r.id),'course_id':str(r.course_id),'title':r.title,'count':r[3]} for r in rows]


@router.post('/tasks/{task_id}/retry')
def retry_task(task_id:uuid.UUID,db=Depends(get_db)):
    job=db.get(Job,task_id)
    if not job:raise HTTPException(404,'任务不存在')
    if job.status not in ('failed','retry_wait','blocked'):raise HTTPException(409,'此任务无需重试')
    job.status='queued';job.attempt=0;job.next_attempt_at=None;job.error=None;db.commit()
    return {'status':'queued'}
