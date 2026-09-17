import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

spec=importlib.util.spec_from_file_location('sufe_cli',Path(__file__).resolve().parents[1]/'scripts/sufe.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


def test_missing_instance_is_json(tmp_path,capsys):
    assert cli.main(['--data-dir',str(tmp_path),'status','--json'])==3
    assert json.loads(capsys.readouterr().out)['error']['code']=='not_running'


def test_complete_routes_only_to_assistant(capsys):
    calls=[]
    def request(info,token,path,method='GET',body=None):
        calls.append((path,method,body))
        return {'app':'sufe-study-assistant'} if path=='/health' else {'status':'done'}
    with patch.object(cli,'connection',return_value=({'url':'http://127.0.0.1:1234'},'private-token')),patch.object(cli,'request',side_effect=request):
        assert cli.main(['tasks','complete','00000000-0000-0000-0000-000000000001','--json'])==0
    assert calls[-1][1:] == ('PATCH',{'status':'done'})
    assert 'private-token' not in capsys.readouterr().out


def test_wrong_app_never_written(capsys):
    with patch.object(cli,'connection',return_value=({},'')),patch.object(cli,'request',return_value={'app':'personal-os'}) as request:
        assert cli.main(['sync','canvas'])==3
        assert request.call_count==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='wrong_service'


def test_no_redirects():
    assert cli.NoRedirect().redirect_request(None,None,None,None,None,None) is None


def test_classroom_cli_sync_uses_mode_aware_queue():
    from personal_os_api.integration import sync
    with patch('personal_os_api.kzkt_queue.enqueue',return_value={'batch_id':'test-batch','courses':1,'status':'queued'}),patch('personal_os_api.kzkt_queue.queue_state',return_value={'paused':True}),patch('personal_os_api.integration.start_browser') as browser:
        result=sync('kzkt',db=object())
        assert result['job_id']=='test-batch' and result['paused']
        browser.assert_not_called()
