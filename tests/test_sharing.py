from datetime import date
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from personal_os_api.setup_api import Setup,Row,Timetable,configure,confirm,validate_rows,configure_ai,AIConfig,course_mode,CourseMode
from personal_os_api.config import get_settings,save_settings
from personal_os_api.models import AcademicCourse,ClassSession,CalendarItem,RecordingTranscript,CourseRecording,AcademicTerm,AutomationProfile
from personal_os_api.ai_provider import chat_json,validate_config


def sample():
    return Row(course_code='TEST101',name='虚构测试课程',section_code='A',weekday=1,start='08:00',end='09:40',weeks=[1,3,5],location='虚构教室')


def setup_term(root,db):
    return configure(Setup(storage_root=str(root/'courses'),term_name='测试学期',starts_on=date(2026,8,31),teaching_weeks=18,replay_mode='text'),db)


def test_no_personal_defaults(isolated_settings):
    cfg=get_settings()
    assert not cfg.canvas_expected_user_id
    assert cfg.personal_os_port!=8000
    assert cfg.academic_storage_root.startswith(str(isolated_settings))
    assert cfg.values=={}


def test_calendar_import_idempotent_and_odd_weeks(isolated_settings,db):
    setup_term(isolated_settings,db)
    body=Timetable(rows=[sample()])
    assert confirm(body,db)['created_sessions']==3
    assert confirm(body,db)['created_sessions']==0
    sessions=list(db.scalars(select(ClassSession).order_by(ClassSession.start_at)))
    assert [s.week_number for s in sessions]==[1,3,5]
    assert all(s.start_at.weekday()==1 for s in sessions)
    assert db.scalar(select(func.count()).select_from(CalendarItem))==3


def test_invalid_time_and_weeks(isolated_settings):
    with pytest.raises(ValueError):Row(**{**sample().model_dump(),'end':'07:00'})
    with pytest.raises(HTTPException):validate_rows(Timetable(rows=[Row(**{**sample().model_dump(),'weeks':[19]})]))


def test_conflicts_are_preview_only(isolated_settings,db):
    setup_term(isolated_settings,db)
    result=validate_rows(Timetable(rows=[sample(),sample()]))
    assert result['warnings'] and result['requires_confirmation']
    assert db.scalar(select(func.count()).select_from(CalendarItem))==0


def test_api_authentication(isolated_settings):
    from personal_os_api.main import app
    with TestClient(app) as client:
        assert client.get('/api/setup').status_code==401
        assert client.get('/health').json()['app']=='sufe-study-assistant'
        assert client.post('/session',headers={'x-personal-os-token':'wrong'}).status_code==401
        assert client.post('/session',headers={'x-personal-os-token':'test-only-token'}).status_code==200
        assert client.get('/api/setup').status_code==200
        assert client.post('/api/setup/ai/test',headers={'origin':'https://other.example'}).status_code==403


def test_cloud_requires_consent_and_https(isolated_settings):
    for cfg in [{'provider':'openai','base_url':'https://example.com/v1'}, {'provider':'openai','base_url':'http://example.com','cloud_consent':True}, {'provider':'ollama','base_url':'http://example.com'}]:
        with pytest.raises(ValueError):validate_config(cfg)
    validate_config({'provider':'openai','base_url':'https://example.com/v1','cloud_consent':True})


def test_no_ai_makes_no_network_calls(isolated_settings):
    with patch('httpx.Client') as client:
        with pytest.raises(RuntimeError,match='尚未配置'):chat_json('hello')
        client.assert_not_called()


def test_text_model_not_used_for_images(isolated_settings):
    save_settings({'ai':{'provider':'ollama','base_url':'http://127.0.0.1:11434','text_model':'text-only'}})
    with patch('httpx.Client') as client:
        with pytest.raises(RuntimeError,match='看图'):chat_json('read',image='data:image/png;base64,AA==')
        client.assert_not_called()


def test_account_rebind_rejected(isolated_settings):
    from personal_os_api.connections import CanvasIdentity,bind_canvas
    root=isolated_settings/'courses';path=root/'.runtime/canvas';path.mkdir(parents=True)
    (path/'identity.json').write_text('{"user_id":"202"}')
    save_settings({'storage_root':str(root),'canvas_user_id':'101'})
    with pytest.raises(HTTPException) as e:bind_canvas(CanvasIdentity(user_id='202'))
    assert e.value.status_code==409


def test_course_mode_override(isolated_settings,db):
    setup_term(isolated_settings,db);confirm(Timetable(rows=[sample()]),db)
    c=db.scalar(select(AcademicCourse))
    course_mode(c.id,CourseMode(mode='illustrated'),db)
    assert get_settings().values['course_modes'][str(c.id)]=='illustrated'
    course_mode(c.id,CourseMode(mode='default'),db)
    assert str(c.id) not in get_settings().values['course_modes']


def test_platform_text_persists_without_ai(isolated_settings,db):
    from personal_os_api.kzkt import process_recording
    setup_term(isolated_settings,db);confirm(Timetable(rows=[sample()]),db)
    course=db.scalar(select(AcademicCourse))
    from personal_os_api.models import CourseProviderBinding
    binding=CourseProviderBinding(course_id=course.id,provider='kzkt',external_id='test-course');db.add(binding);db.flush()
    recording=CourseRecording(provider_binding_id=binding.id,course_id=course.id,provider='kzkt',external_id='test-recording',title='虚构回放',content_hash='a'*64,metadata_={'subtitle_text':'测试原文','subtitle_segments':[{'start':0,'end':2,'text':'测试原文'}]})
    db.add(recording);db.flush()
    with patch('personal_os_api.kzkt.transcribe_locally') as transcribe,patch('personal_os_api.kzkt.summarize_locally') as summary:
        result=process_recording(db,recording)
        assert result['status']=='transcript_ready'
        transcribe.assert_not_called();summary.assert_not_called()
    assert db.scalar(select(RecordingTranscript.source))=='platform_subtitle'


def test_c_drive_storage_is_not_rejected(isolated_settings):
    from personal_os_api.kzkt import storage_root
    assert storage_root().exists()


def test_key_does_not_appear_in_status(isolated_settings):
    from personal_os_api.setup_api import status
    with patch('personal_os_api.setup_api.store_key'):
        configure_ai(AIConfig(provider='openai',base_url='https://example.com/v1',text_model='test',cloud_consent=True,api_key='test-secret-not-real'))
    assert 'test-secret' not in str(status())


def test_whisper_lazy_cuda_failure_restarts_on_cpu(isolated_settings,monkeypatch):
    import sys,json
    from types import SimpleNamespace
    from personal_os_api.kzkt import transcribe_locally
    root=isolated_settings/'courses';root.mkdir();media=root/'fake.wav';media.write_bytes(b'test')
    save_settings({'storage_root':str(root),'whisper_enabled':True})
    devices=[]
    class FakeModel:
        def __init__(self,*args,device,**kwargs):
            assert kwargs['local_files_only'] is True
            self.device=device;devices.append(device)
        def transcribe(self,*args,**kwargs):
            def chunks():
                if self.device=='cuda':
                    yield SimpleNamespace(start=0,end=1,text='discard partial GPU result')
                    raise RuntimeError('CUDA runtime unavailable')
                yield SimpleNamespace(start=0,end=2,text='CPU result')
            return chunks(),SimpleNamespace(duration=2)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setitem(sys.modules,'ctranslate2',SimpleNamespace(get_cuda_device_count=lambda:1))
    text,segments=transcribe_locally(SimpleNamespace(local_path=str(media)))
    assert devices==['cuda','cpu'] and text=='CPU result' and len(segments)==1
    assert json.loads((root/'.runtime/kzkt/status.json').read_text('utf-8'))['fallback'] is True


def test_health_does_not_contact_unconfigured_ai(isolated_settings):
    from personal_os_api.kzkt import runtime_health
    with patch('httpx.get') as get:
        runtime_health()
        get.assert_not_called()


@pytest.mark.parametrize('mode',['text','illustrated'])
def test_queue_commits_text_before_optional_media(isolated_settings,db,mode):
    from personal_os_api.models import CourseProviderBinding,KzktQueueTask
    from personal_os_api.kzkt_queue import advance
    setup_term(isolated_settings,db);confirm(Timetable(rows=[sample()]),db)
    course=db.scalar(select(AcademicCourse))
    course_mode(course.id,CourseMode(mode=mode),db)
    binding=CourseProviderBinding(course_id=course.id,provider='kzkt',external_id='queue-course');db.add(binding);db.flush()
    record=CourseRecording(provider_binding_id=binding.id,course_id=course.id,provider='kzkt',external_id='queue-record',title='虚构课堂',content_hash='b'*64,metadata_={'subtitle_text':'已保存的平台原文'})
    db.add(record);db.flush()
    job=KzktQueueTask(dedupe_key='queue-test',course_id=course.id,recording_id=record.id,external_id=record.external_id,title=record.title,stage='text')
    db.add(job);db.flush()
    with patch('personal_os_api.kzkt_queue.eligible_courses',return_value=[course]),patch('personal_os_api.kzkt_queue.browser',side_effect=RuntimeError('download failed')) as browser:
        advance(db,job)
        assert db.scalar(select(RecordingTranscript.text))=='已保存的平台原文'
        if mode=='text':
            assert job.stage=='transcribe'
            advance(db,job);assert job.stage=='summary'
            advance(db,job);assert job.status=='success'
            browser.assert_not_called()
        else:
            assert job.stage=='media'
            with pytest.raises(RuntimeError,match='download failed'):advance(db,job)
            assert db.scalar(select(RecordingTranscript.text))=='已保存的平台原文'
            course_mode(course.id,CourseMode(mode='text'),db)
            browser.reset_mock()
            advance(db,job);assert job.stage=='transcribe'
            browser.assert_not_called()
