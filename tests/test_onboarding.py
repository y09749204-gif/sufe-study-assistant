"""First-run settings must survive restarts without activating draft courses."""
import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from personal_os_api.main import app
from personal_os_api.config import save_settings


def client():
    return TestClient(app,headers={'x-personal-os-token':'test-only-token'})


def test_draft_resume_and_finish_guard(isolated_settings):
    with client() as c:
        assert c.get('/api/onboarding').json()['completed'] is False
        body={'step':2,'draft':{'term_name':'虚构学期','rows':[{'name':'尚未确认的课程','weeks':'1,3,5'}]}}
        assert c.put('/api/onboarding',json=body).status_code==200
        assert c.post('/api/onboarding/finish').status_code==409
    with client() as c:
        state=c.get('/api/onboarding').json()
        assert state['step']==2 and state['draft']['rows'][0]['name']=='尚未确认的课程'
        # Draft persistence never saves a configured term or imports courses.
        assert c.get('/api/setup').json()['configured'] is False
        save_settings({'term_id':'test-term'})
        assert c.post('/api/onboarding/finish').json()['completed'] is True
        c.put('/api/onboarding',json=body)
        assert c.get('/api/onboarding').json()['completed'] is True


def test_draft_rejects_credentials(isolated_settings):
    with client() as c:
        for draft in [{'api_key':'not-a-real-key'},{'rows':[{'api_key':'not-a-real-key'}]},{'periods':[{'token':'not-a-real-key'}]}]:
            assert c.put('/api/onboarding',json={'step':0,'draft':draft}).status_code==422
        assert not (isolated_settings/'onboarding.json').exists()


def test_storage_check_low_space_and_invalid_path(isolated_settings):
    from collections import namedtuple
    Usage=namedtuple('Usage','total used free')
    with client() as c:
        assert c.post('/api/onboarding/storage',json={'path':'relative/path'}).status_code==422
        with patch('personal_os_api.onboarding.shutil.disk_usage',return_value=Usage(1000,999,1)):
            result=c.post('/api/onboarding/storage',json={'path':str(isolated_settings/'materials')}).json()
        assert result['writable'] is True and result['sufficient'] is False
        assert not list((isolated_settings/'materials').iterdir())
