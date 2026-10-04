"""Run the real Alembic chain in an isolated SQLite DB, including 3.4 data."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('existing',[False,True])
def test_migration_to_model_selection_preserves_data(tmp_path,existing):
    path=tmp_path/'migration.db'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path)}
    def migrate(target):
        subprocess.run([sys.executable,'-m','alembic','upgrade',target],cwd=ROOT,env=env,check=True,capture_output=True,text=True)
    if existing:
        migrate('0004')
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO organizations (id,name,industry_sector,company_type,is_active,created_at) VALUES (1,'Test','Test','head',1,'2026-09-20')")
            db.execute("INSERT INTO users (id,username,password_hash,role,organization_id,is_active) VALUES (1,'test','test','admin',1,1)")
            db.execute("INSERT INTO procurement_sessions (id,organization_id,created_by,title,status,progress,schema_doc,approved,created_at,base_snapshot) VALUES ('old-session',1,1,'Keep this title','MATCHED',100,?,1,'2026-09-20','{}')",(json.dumps({'features':[{'key':'voltage_v'}]}),))
    migrate('head')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0005'
        cols={r[1]:r for r in db.execute('PRAGMA table_info(procurement_sessions)')}
        assert cols['llm_config'][3]==1 and cols['llm_config'][4]=="'{}'"
        if existing:
            title,status,progress,schema,model=db.execute("SELECT title,status,progress,schema_doc,llm_config FROM procurement_sessions WHERE id='old-session'").fetchone()
            assert (title,status,progress)==('Keep this title','MATCHED',100)
            assert json.loads(schema)=={'features':[{'key':'voltage_v'}]}
            assert json.loads(model)=={}

