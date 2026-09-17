"""Resumable first-run drafts, separate from active application settings."""
import json
import shutil
import tempfile
from threading import Lock
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import text

from .config import data_root, get_settings
from .db import get_db
from .sufe_timetable import sufe_periods
from .connections import browser_executable

router = APIRouter(prefix='/api/onboarding', tags=['onboarding'])
_state_lock = Lock()


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    storage_root: str = Field(default='', max_length=2048)
    term_name: str = Field(default='新学期', max_length=100)
    starts_on: str = Field(default='', max_length=10)
    teaching_weeks: int = Field(default=18, ge=1, le=52)
    replay_mode: Literal['text', 'illustrated'] = 'text'
    periods: list[dict] = Field(default_factory=sufe_periods, max_length=30)
    rows: list[dict] = Field(default_factory=list, max_length=300)


    @field_validator('rows')
    @classmethod
    def safe_rows(cls, rows):
        allowed = {'course_code','name','section_code','weekday','start','end','weeks','location','teacher','canvas_id'}
        if any(set(row) - allowed for row in rows):
            raise ValueError('草稿只接受课表字段')
        return rows

    @field_validator('periods')
    @classmethod
    def safe_periods(cls, periods):
        if any(set(period) - {'number','start','end'} for period in periods):
            raise ValueError('节次只接受序号和时间')
        return sufe_periods()


class Progress(BaseModel):
    model_config = ConfigDict(extra='forbid')
    step: int = Field(ge=0, le=5)
    draft: Draft


def read_state():
    path = data_root() / 'onboarding.json'
    if path.exists():
        try:
            state = json.loads(path.read_text('utf-8'))
            state.setdefault('draft', {})['periods'] = sufe_periods()
            return state
        except (OSError, ValueError):
            pass
    cfg = get_settings().values
    return {'step': 0, 'completed': False, 'draft': Draft(**{
        k: v for k, v in cfg.items() if k in Draft.model_fields
    }).model_dump()}


def write_state(state):
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    # Unique temp names prevent concurrent autosaves from sharing a temp file.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, delete=False) as f:
        json.dump(state, f, ensure_ascii=False)
        name = f.name
    Path(name).replace(root / 'onboarding.json')


@router.get('')
def status():
    return read_state()


@router.put('')
def save(body: Progress):
    state = body.model_dump()
    # Drafts contain only timetable fields; AI keys remain in the DPAPI store.
    if len(json.dumps(state)) > 250_000:
        raise HTTPException(413, '课表草稿过大')
    with _state_lock:
        state['completed'] = read_state().get('completed', False)
        write_state(state)
    return {'saved': True}


@router.post('/finish')
def finish():
    if not get_settings().values.get('term_id'):
        raise HTTPException(409, '请先保存学期设置；课表和平台连接可以稍后添加')
    with _state_lock:
        state = read_state()
        state.update(completed=True, step=5)
        write_state(state)
    return {'completed': True}


class StorageCheck(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


@router.post('/storage')
def storage(body: StorageCheck):
    root = Path(body.path)
    if not root.is_absolute():
        raise HTTPException(422, '请选择绝对路径，例如 C:/StudyMaterials')
    try:
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as f:
            f.write(b'sufe-storage-check')
        free = shutil.disk_usage(root).free
    except OSError:
        raise HTTPException(409, '无法写入此目录，请选择有写入权限的文件夹')
    return {'path': str(root), 'writable': True, 'free_bytes': free,
            'sufficient': free >= 512 * 1024**2}


@router.get('/environment')
def environment(db=Depends(get_db)):
    checks = []
    try:
        db.execute(text('SELECT 1'))
        checks.append({'name': '独立数据库', 'ok': True, 'message': '已连接'})
    except Exception:
        db.rollback()
        checks.append({'name': '独立数据库', 'ok': False, 'message': '无法连接，请重启应用后重试'})
    try:
        browser = browser_executable()
        checks.append({'name': '学校登录浏览器', 'ok': True, 'message': Path(browser).name})
    except RuntimeError:
        checks.append({'name': '学校登录浏览器', 'ok': False, 'message': '未找到 Chrome 或 Edge；仍可使用手动课表'})
    return {'checks': checks, 'data_directory': str(data_root())}
