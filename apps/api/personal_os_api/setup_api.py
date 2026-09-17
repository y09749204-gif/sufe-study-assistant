import base64
import csv
import hashlib
import io
import json
import shutil
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from .config import get_settings, save_settings, data_root
from .db import get_db
from .sufe_timetable import sufe_periods
from .models import AcademicTerm, AcademicCourse, Project, ProjectStatus, AutomationProfile, CalendarItem, CalendarItemType, ClassSession, DeliveryMode, CourseMeetingRule
from .ai_provider import store_key, chat_json, validate_config, key_path, normalized_url, list_models

router = APIRouter(prefix='/api/setup', tags=['setup'])


class Setup(BaseModel):
    storage_root: str
    term_name: str = Field(min_length=1, max_length=100)
    starts_on: date
    teaching_weeks: int = Field(default=18, ge=1, le=52)
    replay_mode: Literal['text', 'illustrated']

    @model_validator(mode='after')
    def check(self):
        if self.starts_on.weekday() != 0: raise ValueError('学期第一周起始日请选择周一')
        if not Path(self.storage_root).is_absolute(): raise ValueError('资料目录必须是绝对路径')
        return self


@router.get('')
def status():
    cfg = dict(get_settings().values)
    cfg["periods"] = sufe_periods()
    ai = dict(cfg.get('ai', {'provider': 'none'}))
    ai['api_key_set'] = key_path(ai.get('key_ref')).exists()
    ai.pop('key_ref', None)
    if ai.get('vision_service'):
        vision = dict(ai['vision_service'])
        vision['api_key_set'] = key_path(vision.get('key_ref')).exists()
        vision.pop('key_ref', None)
        ai['vision_service'] = vision
    cfg['ai'] = ai
    return {'configured': bool(cfg.get('term_id')), 'settings': cfg, 'api_key_set': (data_root() / 'api-key.dpapi').exists(), 'default_storage_root': get_settings().academic_storage_root}


@router.post('')
def configure(body: Setup, db=Depends(get_db)):
    root = Path(body.storage_root).resolve(); root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 512 * 1024**2: raise HTTPException(409, '资料目录剩余空间不足 512 MB')
    cfg = get_settings().values
    if cfg.get('storage_root') and cfg['storage_root'] != str(root):
        raise HTTPException(409, '已配置资料目录；更换目录需先迁移已有资料')
    term = db.get(AcademicTerm, uuid.UUID(cfg['term_id'])) if cfg.get('term_id') else None
    if term and (term.starts_on != body.starts_on or term.teaching_weeks != body.teaching_weeks):
        if db.scalar(select(AcademicCourse.id).where(AcademicCourse.term_id == term.id).limit(1)):
            raise HTTPException(409, '已有课程时不能直接修改学期起始日和周数')
    if not term:
        term = AcademicTerm(code='term-' + uuid.uuid4().hex[:12], name=body.term_name, starts_on=body.starts_on, teaching_weeks=body.teaching_weeks)
        db.add(term)
    else:
        term.name=body.term_name; term.starts_on=body.starts_on; term.teaching_weeks=body.teaching_weeks
    db.commit(); db.refresh(term)
    cfg.update(body.model_dump(mode='json')); cfg['storage_root']=str(root); cfg['term_id']=str(term.id); cfg['periods']=sufe_periods()
    save_settings(cfg)
    return status()


class AIService(BaseModel):
    provider: Literal['none','ollama','openai']
    base_url: str = 'http://127.0.0.1:11434'
    text_model: str = ''
    vision_model: str = ''
    cloud_consent: bool = False
    api_key: str | None = None
    vendor: str = 'custom'


class AIConfig(AIService):
    vision_reuse: bool = True
    vision_service: AIService | None = None


@router.put('/ai')
def configure_ai(body: AIConfig):
    settings = get_settings().values
    old = settings.get('ai', {})
    pending = []
    def prepare(service, previous):
        cfg = service.model_dump(exclude={'api_key', 'vision_service', 'vision_reuse'})
        cfg['base_url'] = normalized_url(cfg['base_url'])
        validate_config(cfg)
        if cfg['provider'] == 'openai':
            same = previous.get('provider') == cfg['provider'] and normalized_url(previous.get('base_url', '')) == cfg['base_url']
            if service.api_key:
                cfg['key_ref'] = uuid.uuid4().hex
                pending.append((service.api_key, cfg['key_ref']))
            elif same and key_path(previous.get('key_ref')).exists():
                if previous.get('key_ref'): cfg['key_ref'] = previous['key_ref']
            else: raise ValueError('请为该服务填写 API Key；更换地址不能沿用旧密钥')
        return cfg
    try:
        cfg = prepare(body, old)
        cfg['vision_reuse'] = body.vision_reuse
        if not body.vision_reuse:
            cfg['vision_service'] = prepare(body.vision_service or AIService(provider='none'), old.get('vision_service') or {})
        for value, ref in pending: store_key(value, ref)
    except ValueError as e: raise HTTPException(422, str(e))
    settings['ai']=cfg; save_settings(settings)
    return {'provider': body.provider, 'saved': True}


@router.post('/ai/test')
def test_ai(purpose: Literal['text', 'vision'] = 'text'):
    try:
        if purpose == 'vision':
            import secrets
            from PIL import Image, ImageDraw
            code = ''.join(secrets.choice('23456789') for _ in range(5))
            image = Image.new('RGB', (240, 80), 'white')
            ImageDraw.Draw(image).text((20, 15), code, fill='black', font_size=40)
            output = io.BytesIO(); image.save(output, format='PNG')
            result = chat_json('Read the digits in this image. Return JSON only: {"digits":"the digits you see"}', image='data:image/png;base64,' + base64.b64encode(output.getvalue()).decode())
            if result.get('digits') != code: raise RuntimeError('看图测试未通过，请选择支持图片输入的模型')
        else:
            result=chat_json('Return exactly this JSON object: {"ok":true}')
            if result.get('ok') is not True: raise RuntimeError('模型返回不符合要求')
        return {'ok': True}
    except RuntimeError as e: raise HTTPException(409, str(e))


@router.get('/ai/models')
def ai_models(purpose: Literal['text', 'vision'] = 'text'):
    try: return {'models': list_models(purpose)}
    except RuntimeError as e: raise HTTPException(409, str(e))


class Row(BaseModel):
    course_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    section_code: str = Field(default='default', max_length=32)
    weekday: int = Field(ge=0, le=6)
    start: time
    end: time
    weeks: list[int] = Field(min_length=1)
    location: str = ''
    teacher: str = ''
    canvas_id: str = ''

    @model_validator(mode='after')
    def check(self):
        if self.end <= self.start: raise ValueError('结束时间必须晚于开始时间')
        if any(w < 1 or w > 52 for w in self.weeks): raise ValueError('周数必须在 1–52 之间')
        self.weeks=sorted(set(self.weeks))
        return self


class Timetable(BaseModel):
    rows: list[Row] = Field(min_length=1, max_length=300)


def validate_rows(body):
    weeks=get_settings().values.get('teaching_weeks', 18)
    for row in body.rows:
        if max(row.weeks)>weeks: raise HTTPException(422,'课程周数超过学期长度')
    warnings=[]
    for i,a in enumerate(body.rows):
        for b in body.rows[i+1:]:
            if a.weekday==b.weekday and set(a.weeks)&set(b.weeks) and a.start<b.end and b.start<a.end:
                warnings.append(f'{a.name} 与 {b.name} 时间重叠，请核对')
    return {'rows':[r.model_dump(mode='json') for r in body.rows], 'warnings':warnings, 'requires_confirmation':True}


@router.post('/timetable/preview')
def preview(body: Timetable): return validate_rows(body)


class CSVBody(BaseModel):
    text: str = Field(max_length=500000)


@router.post('/timetable/csv')
def parse_csv(body: CSVBody):
    try:
        rows=[]
        for r in csv.DictReader(io.StringIO(body.text.lstrip('\ufeff'))):
            r['weeks']=[int(w) for w in r['weeks'].split(';')]
            rows.append(Row.model_validate(r))
        return validate_rows(Timetable(rows=rows))
    except (ValueError, KeyError) as e: raise HTTPException(422,'CSV 格式无效，请使用模板并用分号分隔周数') from None


class Screenshot(BaseModel):
    image: str = Field(max_length=12_000_000)


@router.post('/timetable/recognize')
def recognize(body: Screenshot):
    if not body.image.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')):
        raise HTTPException(422,'只支持 PNG、JPEG 和 WebP 图片')
    try: base64.b64decode(body.image.split(',',1)[1],validate=True)
    except ValueError: raise HTTPException(422,'图片编码无效')
    settings=get_settings().values
    prompt='识别课表图片。只输出 JSON，格式为 '+json.dumps(Timetable.model_json_schema(),ensure_ascii=False)+'。weekday 周一为0；weeks 是实际教学周序号。不可识别或缺少必填信息的条目不要猜测，省略该条目。统一使用上财节次时间（截图仅写节次时按此表换算起止时间；截图明确写出时间时保留原文时间供用户核对）：'+json.dumps(sufe_periods(),ensure_ascii=False)
    try:
        result=chat_json(prompt,image=body.image)
        return {**validate_rows(Timetable.model_validate(result)), 'notice':'AI 可能遗漏或识别错误，请逐项与原图核对；尚未导入'}
    except (ValueError,RuntimeError) as e: raise HTTPException(422,'识别未产生有效课表，请检查看图模型或改用手动录入') from None


@router.post('/timetable/confirm')
def confirm(body: Timetable, db=Depends(get_db)):
    validate_rows(body)
    cfg=get_settings().values
    if not cfg.get('term_id'): raise HTTPException(409,'请先设置学期')
    term=db.get(AcademicTerm,uuid.UUID(cfg['term_id'])); created=0
    for row in body.rows:
        code=row.course_code + ':' + row.section_code
        if len(code)>32: raise HTTPException(422,'课程编号与教学班号合计过长')
        course=db.scalar(select(AcademicCourse).where(AcademicCourse.term_id==term.id,AcademicCourse.course_code==code))
        if not course:
            project=Project(name=row.name,status=ProjectStatus.active);db.add(project);db.flush()
            course=AcademicCourse(term_id=term.id,project_id=project.id,course_code=code,section_code=row.section_code,name=row.name,credits=0,teachers=[row.teacher] if row.teacher else [],automation_profile=AutomationProfile.full)
            db.add(course);db.flush()
        key=hashlib.sha256(json.dumps(row.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
        rule=db.scalar(select(CourseMeetingRule).where(CourseMeetingRule.course_id==course.id,CourseMeetingRule.source_key==key))
        if not rule:
            rule=CourseMeetingRule(course_id=course.id,source_key=key,weekday=row.weekday,start_time=row.start,end_time=row.end,weeks=row.weeks,delivery_mode=DeliveryMode.in_person,location=row.location,instructors=course.teachers)
            db.add(rule);db.flush()
        for week in row.weeks:
            session_key=f'{key}:{week}'
            if db.scalar(select(ClassSession.id).where(ClassSession.course_id==course.id,ClassSession.source_key==session_key)):continue
            day=term.starts_on+timedelta(weeks=week-1,days=row.weekday)
            start=datetime.combine(day,row.start,ZoneInfo('Asia/Shanghai'));end=datetime.combine(day,row.end,ZoneInfo('Asia/Shanghai'))
            event=CalendarItem(title=row.name,item_type=CalendarItemType.hard_event,start_at=start,end_at=end,locked=True,flexible=False,source='timetable',project_id=course.project_id)
            db.add(event);db.flush()
            db.add(ClassSession(course_id=course.id,meeting_rule_id=rule.id,calendar_item_id=event.id,source_key=session_key,week_number=week,start_at=start,end_at=end,delivery_mode=DeliveryMode.in_person,location=row.location,instructors=course.teachers));created+=1
        if row.canvas_id:
            from .models import CourseProviderBinding
            binding=db.scalar(select(CourseProviderBinding).where(CourseProviderBinding.provider=='canvas',CourseProviderBinding.external_id==row.canvas_id))
            if binding and binding.course_id!=course.id:raise HTTPException(409,'Canvas 课程已绑定其他课程')
            if not binding:db.add(CourseProviderBinding(course_id=course.id,provider='canvas',external_id=row.canvas_id,base_url=get_settings().canvas_base_url))
    db.commit()
    return {'created_sessions':created}


class CourseMode(BaseModel):
    mode:Literal['default','text','illustrated']


@router.put('/courses/{course_id}/mode')
def course_mode(course_id:uuid.UUID,body:CourseMode,db=Depends(get_db)):
    if not db.get(AcademicCourse,course_id):raise HTTPException(404,'课程不存在')
    values=get_settings().values; modes=values.get('course_modes',{})
    if body.mode=='default':modes.pop(str(course_id),None)
    else:modes[str(course_id)]=body.mode
    values['course_modes']=modes;save_settings(values)
    return {'mode':body.mode}
