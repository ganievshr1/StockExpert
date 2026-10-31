"""Exact label/unit alignment without category induction or LLM calls."""
from copy import deepcopy
from hashlib import sha256
from sqlalchemy import select
from app.db import Item
from app.services.taxonomy import label_key

def build(db, session):
    rows=list(db.scalars(select(Item).where(Item.session_id==session.id,Item.passport.is_not(None))))
    features={}; previous={f['key']:f for f in session.schema_doc.get('features',[])}
    for row in rows:
        if not row.passport: continue
        p=deepcopy(row.passport); attrs={'name':p.get('name')}; evidence={}
        source=p.get('normalized_attributes',p.get('attributes',{}))
        specs=p.get('normalized_features',session.schema_doc.get('features',[]))
        for f in specs:
            if f['key']=='name':continue
            signature=(label_key(f['label']),f.get('kind','string'),f.get('unit','none'))
            key='f_'+sha256(repr(signature).encode()).hexdigest()[:20]
            feature={**f,'key':key,'comparison':'informational','tolerance':0}
            if key in previous:feature.update(comparison=previous[key].get('comparison','informational'),tolerance=previous[key].get('tolerance',0))
            features[key]=feature
            value=source.get(f['key'])
            if key in attrs and attrs[key]!=value and value is not None:
                attrs[key]=None
                p.setdefault('normalization',{})['requires_review']=True
            elif key not in attrs:attrs[key]=value
            evidence[key]=p.get('normalized_evidence',p.get('evidence',{})).get(f['key'])
        p.update(attributes=attrs,evidence=evidence,category_id=None,taxonomy_version=None)
        row.passport=p;row.category=None
    name={'key':'name','label':'Наименование','kind':'string','unit':'none','comparison':'informational','tolerance':0,'aliases':[]}
    session.schema_doc={**session.schema_doc,'features':[name,*sorted(features.values(),key=lambda f:f['key'])], 'categories':[], 'taxonomy_mode':'passports','taxonomy_status':'skipped','mapping_approved':True}
    session.approved=True;session.taxonomy_version=None
    db.flush()

def configure_reused(db,session):
    if not session.taxonomy_enabled:
        build(db,session)
    elif session.schema_doc.get('taxonomy_status')=='skipped':
        session.schema_doc={**session.schema_doc,'taxonomy_status':'pending','categories':[]}
        session.approved=False;session.taxonomy_version=None;session.status='MAPPING_APPROVED'
