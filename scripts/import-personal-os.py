"""Local, explicit academic-only migration. Source is always read-only.

Connection manifest and backups contain private data; never publish them.
Run with the destination application's PYTHONPATH / DATABASE_URL / SUFE_DATA_DIR.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from sqlalchemy import create_engine, MetaData, select, text
from personal_os_api.db import engine, Base
from personal_os_api import models
from personal_os_api.config import save_settings, data_root


def migrate(source_manifest, destination, backup):
    spec=json.loads(Path(source_manifest).read_text('utf-8'))
    source=create_engine(spec['database_url'],hide_parameters=True)
    old_root=Path(spec['storage_root']).resolve(); new_root=Path(destination).resolve()
    if old_root==new_root or old_root in new_root.parents: raise RuntimeError('Destination must be independent')
    backup=Path(backup).resolve(); backup.mkdir(parents=True,exist_ok=True)
    metadata=MetaData();metadata.reflect(source)
    selected={}
    roots=['academic_terms','academic_courses','course_provider_bindings','course_meeting_rules','class_sessions','syllabus_snapshots','course_messages','course_resources','course_recordings','recording_transcripts','lesson_reviews','courseware_pages','recording_slide_segments','attachment_acquisitions','attachment_acquisition_events','kzkt_queue_tasks']
    with source.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        with conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            def add(name, query):
                rows=selected.setdefault(name,{})
                for row in conn.execute(query).mappings(): rows[row['id']]=dict(row)
            for name in roots:
                if name in metadata.tables: add(name,select(metadata.tables[name]))
            projects={r['project_id'] for r in selected['academic_courses'].values() if r['project_id']}
            for name in ['tasks','calendar_items','commitments']:
                table=metadata.tables[name];add(name,select(table).where(table.c.project_id.in_(projects)))
            table=metadata.tables['inbox_candidates'];add('inbox_candidates',select(table).where(table.c.candidate_type.in_(['academic_notice','academic_wecom_chat'])))
            while True:
                before=sum(map(len,selected.values()))
                for name,rows in list(selected.items()):
                    for fk in metadata.tables[name].foreign_keys:
                        target=fk.column.table
                        ids={r.get(fk.parent.name) for r in rows.values()}-{None}
                        missing=ids-set(selected.get(target.name,{}))
                        if missing: add(target.name,select(target).where(fk.column.in_(missing)))
                if sum(map(len,selected.values()))==before:break
    # Preserve an untouched academic snapshot before rewriting paths or inserting.
    snapshot=backup/'academic-source.json'
    if snapshot.exists(): raise RuntimeError('Use a new backup directory for each migration')
    snapshot.write_text(json.dumps({name:list(rows.values()) for name,rows in selected.items()},ensure_ascii=False,default=str),'utf-8')
    with engine.connect() as conn:
        if conn.execute(select(models.AcademicCourse.id).limit(1)).first(): raise RuntimeError('Destination already contains courses; refusing duplicate import')
    new_root.mkdir(parents=True,exist_ok=True)
    files={};missing=[]
    def rewrite(value):
        if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
        if isinstance(value,list):return [rewrite(v) for v in value]
        if not isinstance(value,str):return value
        # Only explicit local path fields / strings, never URLs or arbitrary text.
        if len(value)>1024 or '\n' in value or not Path(value).is_absolute():return value
        path=Path(value)
        try:rel=path.resolve().relative_to(old_root)
        except ValueError:return value
        target=new_root/rel
        if path.is_file() and path.suffix.lower() not in ('.db','.sqlite','.dpapi'):
            files[path]=target
        elif not path.exists():missing.append(str(path))
        return str(target)
    rewritten={name:[rewrite(row) for row in rows.values()] for name,rows in selected.items()}
    required=sum(p.stat().st_size for p in files)
    if shutil.disk_usage(new_root).free<required+1024**3:raise RuntimeError('Insufficient space for independent copy')
    print(json.dumps({'stage':'copy','files':len(files),'bytes':required}),flush=True)
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    checks=[]
    for index,(src,dst) in enumerate(files.items()):
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
        checksum=digest(src)
        if digest(dst)!=checksum:raise RuntimeError('File copy verification failed')
        checks.append({'relative':str(dst.relative_to(new_root)),'sha256':checksum,'bytes':dst.stat().st_size})
        if index%100==0:print(json.dumps({'copied':index+1,'total':len(files)}),flush=True)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            rows=rewritten.get(table.name,[])
            for row in rows:
                row={k:v for k,v in row.items() if k in table.c}
                if table.name=='tasks':
                    row.update(assignee_type='human',ai_executor=None,automation_id=None,automation_state=None,automation_config={})
                if table.name=='kzkt_queue_tasks' and row.get('status') in ('running','queued','retry_wait'):
                    row.update(status='blocked',error='迁移后请在学业助手中确认并重试')
                conn.execute(table.insert().values(**row))
            if rows and conn.execute(select(text('count(*)')).select_from(table)).scalar()!=len(rows):raise RuntimeError('Destination count mismatch: '+table.name)
        conn.execute(models.KzktQueueControl.__table__.insert().values(id=1,paused=True))
    term=next((r for r in selected['academic_terms'].values() if r['status']=='active'),next(iter(selected['academic_terms'].values())))
    save_settings({'storage_root':str(new_root),'term_id':str(term['id']),'term_name':term['name'],'starts_on':str(term['starts_on']),'teaching_weeks':term['teaching_weeks'],'replay_mode':'text','ai':{'provider':'none'},'background_sync':False})
    report={'tables':{k:len(v) for k,v in rewritten.items()},'files':checks,'missing_original_files':sorted(set(missing)),'source_preserved':True,'login_migrated':False}
    (backup/'migration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    (data_root()/'migration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'stage':'complete','tables':report['tables'],'copied_files':len(checks),'missing_original_files':len(set(missing))}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-manifest',required=True);p.add_argument('--storage',required=True);p.add_argument('--backup',required=True)
    a=p.parse_args();migrate(a.source_manifest,a.storage,a.backup)
