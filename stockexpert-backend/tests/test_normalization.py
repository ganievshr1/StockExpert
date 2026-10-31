from app.services.normalization import normalized,compare,explanation

def schema(): return {'features':[{'key':'voltage_v','label':'Напряжение','kind':'number','unit':'V','comparison':'eq','tolerance':0}],'categories':[{'id':'cable','name':'Кабель'}]}
def extracted(value='3,3',evidence='3,3кВ'):
    return {'category_id':'cable','category_evidence':'Кабель','values':[{'key':'voltage_v','value':value,'unit':'кВ','evidence':evidence}]}
def test_voltage_normalization():
    p,issues=normalized(extracted(),'Кабель КПпБП-120 3х16-3,3кВ',schema())
    assert p['attributes']['voltage_v']==3300 and not issues

def test_fabricated_value_and_quote_removed():
    for e in (extracted('6'),extracted('3,3','несуществующая цитата')):
        p,issues=normalized(e,'Кабель 3,3кВ',schema());assert p['attributes']['voltage_v'] is None and issues

def test_missing_is_not_rejection():
    r=compare({'voltage_v':3300},{'voltage_v':None},schema()['features'])
    assert r['state']=='NEEDS_REVIEW' and not r['diff']

def test_known_diff_and_no_invented_climate():
    r=compare({'voltage_v':3300},{'voltage_v':220},schema()['features'])
    assert r['state']=='CONTRADICTED'
    assert '3300' in explanation(r) and 'ХЛ' not in explanation(r)

def test_missing_requirement_cannot_pass():
    r=compare({'voltage_v':None},{'voltage_v':3300},schema()['features'])
    assert r['state']=='NEEDS_REVIEW' and 'requirement:voltage_v' in r['missing_fields']

def test_cable_rule_corrects_model_without_decoding_brand_number():
    spec={'features':[{'key':'core_count','kind':'integer','unit':'pcs'},{'key':'core_cross_section_mm2','kind':'number','unit':'mm2'}],'categories':[{'id':'cable'}]}
    e={'category_id':'cable','category_evidence':'Кабель','values':[{'key':'core_count','value':'120','unit':'pcs','evidence':'120'}]}
    p,issues=normalized(e,'Кабель КПпБП-120 3х16-3,3кВ',spec)
    assert p['attributes']=={'core_count':3,'core_cross_section_mm2':16}
    assert p['evidence']['core_count']['method']=='cable-dimension-rule-v1'
    assert issues[0]['code']=='LLM_CORRECTED_BY_CABLE_DIMENSION_RULE'

def test_unit_must_be_in_evidence():
    p,issues=normalized(extracted('3,3','3,3'),'Кабель 3,3',schema())
    assert p['attributes']['voltage_v'] is None

