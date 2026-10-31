"""Build fixture rows with current ORM, then insert only historic columns.

The destination keeps its real old schema; no current-only columns are added.
"""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def seed_legacy(path, seed, root, env):
    source = Path(path).with_name(Path(path).stem + '-fixture.db')
    code = 'from app.db import Base, engine\nBase.metadata.create_all(engine)\n' + seed
    subprocess.run([sys.executable, '-c', code], cwd=root,
                   env={**env, 'DATABASE_URL': 'sqlite:///'+str(source), 'DB_HOST': ''},
                   check=True, capture_output=True)
    with sqlite3.connect(source) as current, sqlite3.connect(path) as old:
        names = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')")]
        for name in names:
            columns = [r[1] for r in old.execute(f'PRAGMA table_info("{name}")')]
            quoted = ','.join('"'+c+'"' for c in columns)
            rows = current.execute(f'SELECT {quoted} FROM "{name}"').fetchall()
            if rows:
                old.executemany(f'INSERT INTO "{name}" ({quoted}) VALUES ({",".join("?" for _ in columns)})', rows)


def historic_rows(db, name):
    columns = [r[1] for r in db.execute(f'PRAGMA table_info("{name}")') if r[1] != 'normalization_method']
    quoted = ','.join('"'+c+'"' for c in columns)
    return sorted(db.execute(f'SELECT {quoted} FROM "{name}"').fetchall(), key=repr)
