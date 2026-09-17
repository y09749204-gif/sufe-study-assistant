import json,sys,subprocess
from pathlib import Path
from personal_os_api.config import data_root,get_settings,save_settings
marker=data_root()/'whisper-install.json'
try:
    subprocess.run([sys.executable,'-m','pip','install','faster-whisper==1.1.1'],check=True)
    from faster_whisper.utils import download_model
    cfg=get_settings();cache=Path(cfg.academic_storage_root).parent/'WhisperCache/hub'
    download_model(sys.argv[1],cache_dir=str(cache))
    values=get_settings().values;values.update(whisper_enabled=True,whisper_model=sys.argv[1],whisper_device=sys.argv[2]);save_settings(values)
    marker.write_text(json.dumps({'status':'ready','model':sys.argv[1]}),'utf-8')
except Exception:
    marker.write_text(json.dumps({'status':'failed','message':'安装失败，请查看本机日志并重试'}),'utf-8')
    raise
