"""Upgrade a populated 0006 database and compare every existing table row."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from migration_seed import seed_legacy, historic_rows

ROOT = Path(__file__).resolve().parents[1]


def test_0007_preserves_all_old_tables_and_history(tmp_path):
    path = tmp_path/'history.db'
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///'+str(path), 'DB_HOST': '', 'PYTHONPATH': str(ROOT/'tests')+os.pathsep+str(ROOT)}
    def upgrade(version):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', version], cwd=ROOT, env=env, check=True, capture_output=True)
    upgrade('0006')
    seed = '''
from app.db import SessionLocal, Organization, User, ExpertDataset, ExpertEvaluation
from test_expert_evaluation import populate
from app.services.expert_evaluation import refresh_evaluation
from app.services.audit import record
with SessionLocal() as db:
    org = Organization(name='Historical test'); db.add(org); db.flush()
    db.add(User(username='historic',password_hash='keep-hash',role='admin',organization_id=org.id)); db.commit()
    s, ref, _, job = populate(db)
    reference = ExpertDataset(organization_id=org.id, created_by=1, title='Keep reference', version='1', checksum='f'*64, payload=ref)
    db.add(reference); db.flush()
    e = ExpertEvaluation(dataset_id=reference.id, session_id=s.id, requested_by=1); db.add(e); db.flush()
    refresh_evaluation(db,e)
    record(db,s.id,'HISTORICAL_TEST',{'must_keep':True})
    db.commit()
'''
    seed_legacy(path, seed, ROOT, env)
    def old_rows():
        with sqlite3.connect(path) as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','normalization_experiments')")]
            return {name: historic_rows(db, name) for name in names}
    before = old_rows()
    assert len(before['items']) == 99 and len(before['match_results']) == 98
    assert before['expert_evaluations'] and before['audit_logs']
    upgrade('head'); upgrade('head')
    after=old_rows()
    for name,rows in before.items():
        current=after[name]
        if name=='procurement_sessions':current=[row[:-1] for row in current]
        assert current==rows
    assert not after['run_snapshots'] and not after['metric_revisions'] and not after['lab_experiments']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0011',)
        assert db.execute('SELECT count(*) FROM normalization_experiments').fetchone() == (0,)


def test_0008_keeps_saved_regex_v1_predictions_and_constraints(tmp_path):
    path = tmp_path/'regex-history.db'
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///'+str(path), 'DB_HOST': ''}
    def upgrade(target):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', target], cwd=ROOT, env=env, check=True, capture_output=True)
    upgrade('0007')
    seed = '''
from app.db import SessionLocal, Organization, User, ExpertDataset, NormalizationExperiment
with SessionLocal() as db:
    org=Organization(name='Keep history');db.add(org);db.flush()
    user=User(username='keep',password_hash='unchanged',role='admin',organization_id=org.id);db.add(user);db.flush()
    ref=ExpertDataset(organization_id=org.id,created_by=user.id,title='old',version='1',checksum='f'*64,payload={'old':True});db.add(ref);db.flush()
    db.add(NormalizationExperiment(organization_id=org.id,requested_by=user.id,source_session_id='historical',source_title='Keep title',dataset_id=ref.id,request_id='old-request',status='COMPLETED',rules_version='regex-mtr-v1.0.0',rules_sha256='a'*64,input_sha256='b'*64,input_snapshot={'rows':[{'original':'source'}]},predictions=[{'immutable':True}],result={'metrics':{'h1_f1':0.279}},row_count=1,processed_rows=1))
    db.commit()
'''
    seed_legacy(path, seed, ROOT, env)
    def rows():
        with sqlite3.connect(path) as db:
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'")]
            return {name:historic_rows(db, name) for name in names}
    before=rows()
    upgrade('head');upgrade('head')
    after=rows()
    for name,old in before.items():assert after[name]==old
    assert not after['run_snapshots'] and not after['metric_revisions'] and not after['lab_experiments']
    with sqlite3.connect(path) as db:
        columns={r[1]:r for r in db.execute('PRAGMA table_info(normalization_experiments)')}
        assert columns['dataset_id'][3]==0 and columns['source_session_id'][3]==0
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert any(r[2] for r in db.execute('PRAGMA index_list(normalization_experiments)'))
