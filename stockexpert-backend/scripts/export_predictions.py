"""Run inside backend: python scripts/export_predictions.py SESSION_UUID > predictions.jsonl"""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db import SessionLocal,Item
with SessionLocal() as db:
    for i in db.scalars(select(Item).where(Item.session_id==sys.argv[1],Item.kind=='residual',Item.passport.is_not(None))):
        print(json.dumps({'id':f'{i.document_id}:{i.sheet}:{i.row_index}','attributes':i.passport['attributes']},ensure_ascii=False))

