"""Fresh independent schema. Refuse unknown versions rather than silently downgrading."""
from sqlalchemy import text
from personal_os_api.db import engine,Base
from personal_os_api import models
from personal_os_api.config import data_root
VERSION='1'
with engine.begin() as conn:
    conn.execute(text('SELECT pg_advisory_xact_lock(8810971)'))
    conn.execute(text('CREATE TABLE IF NOT EXISTS sufe_schema_version (version INTEGER PRIMARY KEY)'))
    current=conn.execute(text('SELECT version FROM sufe_schema_version')).scalar()
    if current not in (None,1):raise RuntimeError('Unsupported database version; restore the matching application')
    Base.metadata.create_all(conn)
    if current is None:conn.execute(text('INSERT INTO sufe_schema_version VALUES (1)'))
data_root().mkdir(parents=True,exist_ok=True)
(data_root()/'schema-version').write_text(VERSION,'utf-8')
