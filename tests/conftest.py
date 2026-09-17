import os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
os.environ.setdefault('SUFE_API_TOKEN','test-only-token')


@pytest.fixture
def isolated_settings(tmp_path,monkeypatch):
    monkeypatch.setenv('SUFE_DATA_DIR',str(tmp_path))
    return tmp_path


@pytest.fixture
def db():
    from personal_os_api.db import engine,Base
    from sqlalchemy.orm import Session
    from personal_os_api import models
    if engine.url.database!='sufe_test':pytest.skip('Set DATABASE_URL to isolated sufe_test database')
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        transaction=conn.begin()
        with Session(bind=conn,join_transaction_mode='create_savepoint') as session:yield session
        transaction.rollback()
