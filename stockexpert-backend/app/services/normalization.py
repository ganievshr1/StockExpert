import re,unicodedata
from decimal import Decimal,InvalidOperation
from app.services.contracts import Extraction, ExtractedValue
CONVERSIONS={('kV','V'):1000,('V','kV'):0.001,('bar','MPa'):0.1,('MPa','bar'):10,('m','mm'):1000,('mm','m'):0.001}
ALIASES={'кв':'kV','в':'V','мм':'mm','мм²':'mm2','мм2':'mm2','мпа':'MPa','бар':'bar','м':'m','кг':'kg','шт':'pcs','шт.':'pcs','none':'none'}
def unit(value): return ALIASES.get((value or 'none').strip().lower(),value or 'none')
def normalized(extraction,source,schema):
    e=Extraction.model_validate(extraction);attrs={};evidence={};issues=[]
    by_key={}
    for x in e.values:
        if x.key in by_key: raise ValueError('Duplicate extracted key')
        by_key[x.key]=x
    allowed={f['key'] for f in schema['features']}
    if set(by_key)-allowed: raise ValueError('Unknown extracted key')
    dimensions=list(re.finditer(r'(\d+)\s*[xх×]\s*(\d+(?:[.,]\d+)?)',source))
    derived=set()
    if 'кабел' in source.lower() and len(dimensions)==1:
        match=dimensions[0]
        for k,value,u in [('core_count',match[1],'pcs'),('core_cross_section_mm2',match[2],'mm2')]:
            if k not in allowed: continue
            old=by_key.get(k)
            if old and old.value is not None and old.value.replace(',','.')!=value.replace(',','.'):
                issues.append({'key':k,'code':'LLM_CORRECTED_BY_CABLE_DIMENSION_RULE'})
            by_key[k]=ExtractedValue(key=k,value=value,unit=u,evidence=match[0]);derived.add(k)
    for f in schema['features']:
        k=f['key'];x=by_key.get(k);attrs[k]=None
        if x is None or x.value is None: continue
        if not x.evidence or x.evidence not in source:
            issues.append({'key':k,'code':'UNSUPPORTED_EVIDENCE'});continue
        try:
            if f['kind']=='string':
                # Preserve case and designation; no steel/alloy synonym guessing.
                v=' '.join(unicodedata.normalize('NFKC',x.value).split())
                if v not in ' '.join(unicodedata.normalize('NFKC',x.evidence).split()): raise ValueError('Unsupported text value')
            else:
                v=Decimal(x.value.replace(',','.').replace(' ',''))
                if not v.is_finite(): raise ValueError('Non-finite number')
                # Value must occur as a number in its quoted evidence before conversion.
                nums=[Decimal(n.replace(',','.')) for n in re.findall(r'[-+]?\d+(?:[.,]\d+)?',x.evidence)]
                if v not in nums: raise ValueError('Value absent from evidence')
                source_unit=unit(x.unit);target=f['unit']
                if source_unit!='none' and k not in derived:
                    aliases=[a for a,b in ALIASES.items() if b==source_unit]+[source_unit.lower()]
                    quote=x.evidence.lower().replace('²','2')
                    if not any(re.search(r'(?<![a-zа-я])'+re.escape(a.replace('²','2'))+r'(?![a-zа-я0-9])',quote) for a in aliases):
                        raise ValueError('Unit is not supported by the quote')
                if source_unit!=target:
                    factor=CONVERSIONS.get((source_unit,target))
                    if factor is None: raise ValueError('Unknown unit conversion')
                    v*=Decimal(str(factor))
                if f['kind']=='integer':
                    if v!=v.to_integral_value(): raise ValueError('Non-integer value')
                    v=int(v)
                else: v=float(v)
            attrs[k]=v;evidence[k]={'quote':x.evidence,'raw_value':x.value,'source_unit':x.unit,'target_unit':f['unit'],'method':'cable-dimension-rule-v1' if k in derived else 'llm'}
        except (ValueError,InvalidOperation): issues.append({'key':k,'code':'INVALID_VALUE_OR_UNIT'})
    category=e.category_id
    if category not in {c['id'] for c in schema['categories']} or not e.category_evidence or e.category_evidence not in source:
        category=None;issues.append({'code':'CATEGORY_NEEDS_REVIEW'})
    return {'attributes':attrs,'evidence':evidence,'category_id':category,'category_evidence':e.category_evidence},issues

def compare(requirement,residual,features):
    diffs=[];missing=[];checked=0
    for f in features:
        k=f['key'];want=requirement.get(k);got=residual.get(k)
        if f['comparison']=='informational': continue
        if want is None: missing.append('requirement:'+k);continue
        if got is None: missing.append(k);continue
        checked+=1;tol=f.get('tolerance',0)
        if isinstance(want,(int,float)) and isinstance(got,(int,float)):
            ok=abs(got-want)<=tol if f['comparison']=='eq' else (got+tol>=want if f['comparison']=='gte' else got-tol<=want)
        else: ok=(got==want) if f['comparison']=='eq' else False
        if not ok: diffs.append({'field':k,'label':f['label'],'required':want,'actual':got,'unit':f['unit'],'operator':f['comparison'],'tolerance':tol})
    state='CONTRADICTED' if diffs else ('NEEDS_REVIEW' if missing or checked==0 else 'PASSES_KNOWN_RULES')
    return {'state':state,'diff':diffs,'missing_fields':missing}
def explanation(result):
    if result['diff']:
        return '; '.join(f"{d['label']}: требуется {d['operator']} {d['required']} {d['unit']}, в остатке {d['actual']} {d['unit']} (допуск {d['tolerance']})" for d in result['diff'])
    if result['state']=='NEEDS_REVIEW': return 'Недостаточно подтверждённых данных для решения; требуется проверка.'
    return 'По проверенным параметрам противоречий не найдено. Требуется экспертное подтверждение применимости.'

