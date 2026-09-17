"""Per-install configuration; never consult the original Personal OS .env."""
import json
import os
from pathlib import Path


def data_root():
    return Path(os.environ.get('SUFE_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'SufeStudyAssistant'))).resolve()


class Settings:
    def __init__(self):
        root = data_root()
        path = root / 'settings.json'
        self.values = json.loads(path.read_text('utf-8')) if path.exists() else {}
        self.database_url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://sufe:sufe@127.0.0.1:55439/sufe_study')
        self.personal_os_host = '127.0.0.1'
        self.personal_os_port = int(os.environ.get('SUFE_API_PORT', '18763'))
        self.personal_os_tool_token = os.environ.get('SUFE_API_TOKEN', '')
        self.personal_os_timezone = 'Asia/Shanghai'
        self.academic_storage_root = self.values.get('storage_root', str(root / 'courses'))
        self.canvas_expected_user_id = self.values.get('canvas_user_id', '')
        self.canvas_base_url = 'https://canvas.shufe.edu.cn'
        self.canvas_browser_command = 'integrations/canvas-browser/cli.mjs'
        self.kzkt_base_url = 'https://dm.shufe.edu.cn'
        self.kzkt_browser_command = 'integrations/kzkt-browser/cli.mjs'
        self.kzkt_whisper_model = self.values.get('whisper_model', 'small')
        self.kzkt_ollama_base_url = self.values.get('ai', {}).get('base_url', 'http://127.0.0.1:11434')
        self.kzkt_ollama_model = self.values.get('ai', {}).get('text_model', 'qwen3:8b')
        self.wecom_ui_executor_enabled = False
        self.wecom_ui_auto_acquire_enabled = False


def get_settings():
    return Settings()


def save_settings(values):
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    pending = root / 'settings.pending'
    pending.write_text(json.dumps(values, ensure_ascii=False, indent=2), 'utf-8')
    pending.replace(root / 'settings.json')
