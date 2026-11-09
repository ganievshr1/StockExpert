"""H2: inspect every normalized passport, then build a separate tree per material.

LLM inputs are bounded batches, never a sample of the inventory. All semantic
catalogue pages are visited when resolving a new label. Exact duplicate JSONs
share a proposal but every source row receives an assignment and remains counted.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import unicodedata
from sqlalchemy import select
from app.db import Item
from app.services import llm, taxonomy_recovery
from app.services.contracts import Equivalent,SchemaDraft
from app.services.passports import NAME_FEATURE
from app.services.passports import needs_review, needs_normalization_review, blocks_classification, REVIEW_MATERIAL, REVIEW_CATEGORY_ID
from app.services.audit import record

FIELD_MAPPING_POLICY = 'distinct-regex-keys-and-observed-conflicts-v1'

def is_new(schema):return schema.get('taxonomy_mode')=='passports'
def label_key(text):return ' '.join(unicodedata.normalize('NFKC',text).lower().replace('ё','е').split()).strip(' .,:;')
def stable(prefix,value):return prefix+hashlib.sha256(value.encode()).hexdigest()[:20]
def descriptor(f):return (f['key'],label_key(f['label']),f['kind'],f['unit'])
def root_id(schema,category_id):
    cats={c['id']:c for c in schema.get('categories',[])};seen=set()
    while category_id in cats and category_id not in seen:
        seen.add(category_id);parent=cats[category_id].get('parent_id')
        if not parent:return category_id
        category_id=parent
    return None
def features_for(schema,category_id):
    if not is_new(schema):return schema.get('features',[])
    root=root_id(schema,category_id)
    category=next((c for c in schema.get('categories',[]) if c['id']==root),None)
    keys=set(category.get('feature_keys',[])) if category else {'name'}
    return [f for f in schema.get('features',[]) if f['key'] in keys]

def equivalent(name,catalog,tick,context,kind=None,unit=None):
    """Return an existing semantic equivalent, not merely a related object."""
    eligible=[x for x in catalog if (kind is None or (x['kind']==kind and x['unit']==unit))]
    key=label_key(name)
    for entry in eligible:
        if key in {label_key(entry['name']),*(label_key(a) for a in entry.get('aliases',[]))}:return entry
    for start in range(0,len(eligible),24):
        page=eligible[start:start+24];tick('TAXONOMY_BUILD')
        # Counts, inherited field lists and long alias histories are not model input.
        # Exact aliases were checked above; every canonical catalogue name is visited.
        compact=[{'id':x['id'],'name':x['name'],**({'kind':x['kind'],'unit':x['unit']} if kind is not None else {})} for x in page]
        response=Equivalent.model_validate(llm.structured(
            'Сопоставь название только с семантически эквивалентным названием в каталоге. '
            'Учитывай синонимы и единственное/множественное число. '
            'Не объединяй разные изделия по общему сырью, отрасли или похожему написанию. '
            'Для свойств не объединяй разные физические смыслы (например внутренний и наружный размер). '
            'Если точного смыслового соответствия нет или есть сомнение — match_id=null. '
            'Ответ содержит id только из переданного каталога.',
            {'name':name,'context':context,'catalog':compact},Equivalent.model_json_schema()))
        if response.match_id is not None:
            found=next((x for x in page if x['id']==response.match_id),None)
            if found is None:raise ValueError('Taxonomy resolver returned an unknown catalogue identifier')
            return found
    return None

def field_constraints(items):
    """H1 identities and observed disagreements constrain semantic aliases.

    Regex keys have fixed meanings. Distinct keys must remain distinct even
    when their values happen to match, or they occur on different stock rows.
    For open LLM keys, co-occurring unequal values disprove equivalence.
    """
    regex_signatures = set()
    incompatible = defaultdict(set)
    for item in items:
        p = item.passport
        if p.get('provenance', {}).get('provider') == 'regex':
            regex_signatures.update(descriptor(f) for f in p['normalized_features'])
        by_type = defaultdict(list)
        for f in p['normalized_features']:
            value = p['normalized_attributes'].get(f['key'])
            if value is not None:
                by_type[(f['kind'], f['unit'])].append((descriptor(f), value))
        for values in by_type.values():
            for index, (a, av) in enumerate(values):
                for b, bv in values[index + 1:]:
                    if av != bv:
                        incompatible[a].add(b)
                        incompatible[b].add(a)
    return regex_signatures, incompatible


def build_fields(root, items, tick):
    specs = {descriptor(f): f for item in items for f in item.passport['normalized_features']}
    regex_signatures, incompatible = field_constraints(items)
    catalog, fields, mapping = [], [], {}
    members = defaultdict(set)
    for signature, f in sorted(specs.items()):
        eligible = [entry for entry in catalog
                    if not incompatible[signature].intersection(members[entry['id']])
                    and not (signature in regex_signatures and any(
                        other in regex_signatures and other[0] != signature[0]
                        for other in members[entry['id']]))]
        entry = None if root['id'] == REVIEW_CATEGORY_ID else equivalent(
            f['label'], eligible, tick, 'Свойство материала ' + root['name'], f['kind'], f['unit'])
        if entry is None:
            key = stable('f_', root['id'] + '|' + json.dumps(signature, ensure_ascii=False))
            feature = {**f, 'key': key, 'comparison': 'informational', 'tolerance': 0, 'aliases': [f['label']]}
            entry = {'id': key, 'name': f['label'], 'kind': f['kind'], 'unit': f['unit'], 'aliases': feature['aliases']}
            catalog.append(entry)
            fields.append(feature)
        elif f['label'] not in entry['aliases']:
            entry['aliases'].append(f['label'])
        mapping[signature] = entry['id']
        members[entry['id']].add(signature)
    return fields, mapping

def propose(db, items, tick, retry_failed=False):
    grouped = defaultdict(list)
    for item in items:
        passport = item.passport
        if not passport or not passport.get('name') or 'normalized_attributes' not in passport:
            raise ValueError(f'Normalize every position before taxonomy: item={item.id}')
        if blocks_classification(passport):
            item.passport = {**passport, 'taxonomy_proposal': {'material': REVIEW_MATERIAL, 'subtype': None}}
            continue
        payload = {'name': passport['name'], 'attributes': passport['normalized_attributes']}
        signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        cached = passport.get('taxonomy_proposal', {})
        failed = passport.get('classification', {}).get('requires_review', False)
        if cached.get('signature') == signature and not (retry_failed and failed):
            continue
        grouped[signature].append(item)

    def save(signature, rows, result):
        proposal = {'signature': signature, 'material': result.material, 'subtype': result.subtype}
        for item in rows:
            # Exact duplicate passports share the proposal; diagnostics keep each source row.
            item.passport = {
                **item.passport, 'taxonomy_proposal': proposal,
                'classification': {
                    'status': 'review' if result.requires_review else 'classified',
                    'requires_review': result.requires_review, 'attempts': result.attempts,
                    'errors': result.errors, 'raw_response': result.raw_response,
                    'representative_item_id': result.item_id, 'prompt_version': 'taxonomy-proposal-v2',
                },
            }
            item.issues = [x for x in item.issues if x['code'] != 'TAXONOMY_NEEDS_REVIEW']
            if result.requires_review:
                item.issues = [*item.issues, {
                    'code': 'TAXONOMY_NEEDS_REVIEW',
                    'message': 'Модель не вернула однозначную классификацию позиции. '
                               'Паспорт сохранён; требуется проверка категории.',
                    'attempts': result.attempts, 'item_id': item.id,
                    'name': item.passport['name'], **item.passport.get('source_location', {}),
                }]
        # Commit before requesting repairs; already accepted rows survive a later outage.
        db.commit()

    def process(batch):
        by_id = {rows[0].id: (signature, rows) for signature, rows in batch}
        inputs = [{'item_id': rows[0].id, 'name': rows[0].passport['name'],
                   'attributes': rows[0].passport['normalized_attributes'], 'source_rows': len(rows)}
                  for _, rows in batch]
        for result in taxonomy_recovery.classify(inputs, tick):
            signature, rows = by_id[result.item_id]
            save(signature, rows, result)

    batch, size = [], 0
    for signature, rows in sorted(grouped.items()):
        cost = len(json.dumps({'name': rows[0].passport['name'],
                               'attributes': rows[0].passport['normalized_attributes']}, ensure_ascii=False))
        if cost > 10000:
            tick('TAXONOMY_BUILD')
            save(signature, rows, taxonomy_recovery.ClassificationResult(
                rows[0].id, REVIEW_MATERIAL, None, True, 0,
                [{'code': 'TAXONOMY_INPUT_TOO_LARGE', 'message': 'Паспорт превышает лимит контекста классификации.'}], None))
            continue
        if batch and (len(batch) >= 12 or size + cost > 10000):
            process(batch)
            batch, size = [], 0
        batch.append((signature, rows))
        size += cost
    process(batch)

def build(db,s,job,tick):
    if not s.taxonomy_enabled:
        raise ValueError('H2 отключён в этой сессии. Построение таксономии запрещено.')
    items=list(db.scalars(select(Item).where(Item.session_id==s.id).order_by(Item.id)))
    mappings={(m['document_id'],m['sheet']):m for m in s.schema_doc['mappings']}
    items=[i for i in items if i.kind=='requirement' or i.row_index>mappings[(i.document_id,i.sheet)]['header_row']]
    if not items:raise ValueError('No positions for taxonomy')
    # A frozen base is authoritative, including historic passports without a
    # current proposal signature. Only new requirement passports need H2.
    pending=[i for i in items if not (s.approved and (
        (i.kind=='residual' and s.base_snapshot.get('reused_passports'))
        or i.passport.get('taxonomy_version')=='passport-taxonomy-v1'))]
    propose(db,pending,tick,retry_failed=job.kind=='taxonomy')
    job.params={**job.params,'taxonomy_review_count':sum(bool(i.passport.get('classification',{}).get('requires_review')) for i in items),
                'taxonomy_field_mapping_policy':FIELD_MAPPING_POLICY}
    if s.approved:
        assign_frozen(db,s,items,tick)
        job.params={**job.params,'taxonomy_review_count':sum(
            bool(i.passport.get('classification',{}).get('requires_review') or
                 i.passport.get('taxonomy_mapping',{}).get('requires_review')) for i in items)}
        db.commit()
        return
    roots=[];root_alias={};children=defaultdict(list);sub_alias={}
    for name in sorted({i.passport['taxonomy_proposal']['material'] for i in items},key=label_key):
        entry=None if name==REVIEW_MATERIAL else equivalent(name,[r for r in roots if r['id']!=REVIEW_CATEGORY_ID],tick,'Родовой тип изделия из поля name')
        if entry is None:
            entry={'id':REVIEW_CATEGORY_ID if name==REVIEW_MATERIAL else stable('mat_',label_key(name)),'name':name,'parent_id':None,'aliases':[],'feature_keys':['name'],'item_count':0,'requirement_count':0}
            roots.append(entry)
        if name not in entry['aliases']:entry['aliases'].append(name)
        root_alias[name]=entry
    assignments={}
    for item in items:
        p=item.passport['taxonomy_proposal'];root=root_alias[p['material']];sub=p['subtype']
        if sub and label_key(sub)!=label_key(root['name']):
            key=(root['id'],sub)
            if key not in sub_alias:
                child=equivalent(sub,children[root['id']],tick,'Подтип материала '+root['name'])
                if child is None:
                    child={'id':stable('sub_',root['id']+'|'+label_key(sub)),'name':sub,'parent_id':root['id'],'aliases':[],'feature_keys':[], 'item_count':0,'requirement_count':0}
                    children[root['id']].append(child)
                if sub not in child['aliases']:child['aliases'].append(sub)
                sub_alias[key]=child
            category=sub_alias[key]
        else:category=root
        assignments[item.id]=(root,category)
        counter='item_count' if item.kind=='residual' else 'requirement_count'
        root[counter]+=1
        if category is not root:category[counter]+=1
    fields=[deepcopy(NAME_FEATURE)];field_maps={}
    for root in roots:
        root_items=[item for item in items if assignments[item.id][0]['id']==root['id']]
        root_fields,mapping=build_fields(root,root_items,tick)
        fields.extend(root_fields);root['feature_keys'].extend(f['key'] for f in root_fields)
        field_maps[root['id']]=mapping
        for child in children[root['id']]:child['feature_keys']=list(root['feature_keys'])
    categories=[c for root in roots for c in [root,*children[root['id']]]]
    schema=SchemaDraft.model_validate({**s.schema_doc,'categories':categories,'features':fields,
                                      'taxonomy_status':'review','analyzed_items':len(items)}).model_dump()
    # Stage the final assignments without heartbeat commits. Publish one consistent tree.
    tick('TAXONOMY_BUILD')
    for item in items:
        root,category=assignments[item.id]
        assign(item,root,category,field_maps[root['id']])
    s.schema_doc=schema;s.approved=False;s.status='TAXONOMY_REVIEW';s.progress=70
    record(db,s.id,'TAXONOMY_BUILT',{'job_id':job.id,'analyzed_items':len(items),
                                  'material_types':len(roots),'categories':len(categories),'method':'all-normalized-passports-v1',
                                  'field_mapping_policy':FIELD_MAPPING_POLICY})
    db.commit()

def assign(item,root,category,mapping):
    p=deepcopy(item.passport);attrs={'name':p['name']};evidence={'name':p['normalized_evidence']['name']};issues=[]
    grouped=defaultdict(list);conflicts=[]
    for f in p['normalized_features']:
        key=mapping.get(descriptor(f));value=p['normalized_attributes'].get(f['key'])
        if key is None:
            if value is not None:issues.append({'code':'UNMAPPED_REQUIREMENT_FIELD','key':f['key']})
            continue
        grouped[key].append({'source_key':f['key'],'label':f['label'],'value':value,
                             'evidence':p['normalized_evidence'].get(f['key'])})
    for key,sources in grouped.items():
        known=[source for source in sources if source['value'] is not None]
        conflicting=known and (any(source['value']!=known[0]['value'] for source in known[1:])
                    or (p.get('provenance',{}).get('provider')=='regex'
                        and len({source['source_key'] for source in known})>1))
        if conflicting:
            # A frozen tree cannot be expanded here. Keep every H1 value and
            # quote, publish no arbitrary winner, and expose the row for review.
            attrs[key]=None
            issue={'code':'TAXONOMY_FIELD_CONFLICT','key':key,'sources':sources,
                   'message':'H2 сопоставил разные характеристики одному полю. Исходные значения и цитаты H1 сохранены; требуется проверка сопоставления.'}
            issues.append(issue);conflicts.append(key)
        elif known:
            chosen=next((source for source in known if source['evidence'] is not None),known[0])
            attrs[key]=chosen['value']
            if chosen['evidence'] is not None:evidence[key]=chosen['evidence']
        else:attrs[key]=None
    p.update(attributes=attrs,evidence=evidence,category_id=category['id'] if category else None,
             material_id=root['id'] if root else None,category_evidence=p['name_evidence'],taxonomy_version='passport-taxonomy-v1')
    p['taxonomy_mapping']={'policy':FIELD_MAPPING_POLICY,'requires_review':bool(conflicts),'conflict_fields':conflicts}
    item.passport=p;item.category=p['category_id']
    item.issues=[x for x in item.issues if x['code'] not in ('CATEGORY_NEEDS_REVIEW','UNMAPPED_REQUIREMENT_FIELD','TAXONOMY_FIELD_CONFLICT')]+issues
    if category is None:item.issues.append({'code':'CATEGORY_NEEDS_REVIEW'})

def assign_frozen(db,s,items,tick):
    roots=[c for c in s.schema_doc['categories'] if not c['parent_id'] and c['id']!=REVIEW_CATEGORY_ID]
    cache={};field_cache={};pending=[]
    for item in items:
        if item.kind=='residual' and s.base_snapshot.get('reused_passports'):continue
        if item.passport.get('taxonomy_version')=='passport-taxonomy-v1':continue
        if blocks_classification(item.passport) or item.passport.get('classification', {}).get('requires_review'):
            item.passport={**item.passport,'category_id':None,'category_evidence':None,
                           'taxonomy_version':'passport-taxonomy-v1'}
            item.category=None
            if not any(x['code']=='CATEGORY_NEEDS_REVIEW' for x in item.issues):
                item.issues=[*item.issues,{'code':'CATEGORY_NEEDS_REVIEW'}]
            continue
        proposal=item.passport['taxonomy_proposal'];name=proposal['material']
        if name not in cache:cache[name]=equivalent(name,roots,tick,'Тип изделия в утверждённой базе')
        root=cache[name];category=root;mapping={}
        if root:
            sub=proposal['subtype']
            if sub:
                child=equivalent(sub,[c for c in s.schema_doc['categories'] if c['parent_id']==root['id']],tick,'Подтип '+root['name'])
                if child:category=child
            catalog=[{'id':f['key'],'name':f['label'],'kind':f['kind'],'unit':f['unit'],'aliases':f.get('aliases',[])} for f in features_for(s.schema_doc,root['id']) if f['key']!='name']
            for f in item.passport['normalized_features']:
                signature=descriptor(f);key=(root['id'],signature)
                if key not in field_cache:field_cache[key]=equivalent(f['label'],catalog,tick,'Свойство '+root['name'],f['kind'],f['unit'])
                entry=field_cache[key]
                if entry:mapping[signature]=entry['id']
        pending.append((item,root,category,mapping))
    tick('TAXONOMY_BUILD')
    for args in pending:assign(*args)
    schema=deepcopy(s.schema_doc)
    for category in schema['categories']:
        members=[i for i in items if i.category==category['id'] or (category['parent_id'] is None and i.passport.get('material_id')==category['id'])]
        category['item_count']=sum(i.kind=='residual' for i in members)
        category['requirement_count']=sum(i.kind=='requirement' for i in members)
    schema['analyzed_items']=len(items);s.schema_doc=schema
    db.commit()

def check_review_edit(old,new):
    """Protect assignments and normalized units; only labels and rules are editable."""
    if not is_new(old):return
    for key in ('taxonomy_mode','mapping_approved','taxonomy_status','analyzed_items'):
        if old.get(key)!=new.get(key):raise ValueError('Pipeline state cannot be edited through schema JSON')
    if old.get('mapping_approved') and old['mappings']!=new['mappings']:
        raise ValueError('Approved column mapping is immutable; create a new session')
    if old.get('taxonomy_status')=='pending':
        if new['categories'] or new['features']:raise ValueError('Categories are built only from normalized passports')
        return
    name=next((f for f in new['features'] if f['key']=='name'),None)
    if name is None or name['comparison']!='informational':raise ValueError('name is the classification anchor and must remain informational')
    for section,identity,editable in [('categories','id',{'name'}),('features','key',{'label','comparison','tolerance'})]:
        previous={x[identity]:x for x in old[section]};current={x[identity]:x for x in new[section]}
        if set(previous)!=set(current):raise ValueError('Rebuild taxonomy to change its structure')
        for key,entry in previous.items():
            if {k:v for k,v in entry.items() if k not in editable}!={k:v for k,v in current[key].items() if k not in editable}:
                raise ValueError('Taxonomy identifiers, units, and assignments are immutable during review')
