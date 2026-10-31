"""Offline, reproducible CPU checks of the supplied development dataset."""
import os,sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ.update(SECRET_KEY='offline-check-not-production',MINIO_SECRET_KEY='offline-check',ADMIN_PASSWORD='offline-check',DATABASE_URL='sqlite://',DB_HOST='',RERANKER_ENABLED='false')
from app.services.expert_reference import parse_reference
from app.services import normalization_engine
from app.services.lab import run
from app.services.model_registry import default_config
from app.services.expert_evaluation import _prediction
from app.services.expert_reference import equal_value

folder=root.parent/'reference';ref,checksum=parse_reference((folder/'StockExpert_reference_99.json').read_bytes(),'reference.json')
if (folder/'StockExpert_reference_99.xlsx').exists():
    excel,excel_sha=parse_reference((folder/'StockExpert_reference_99.xlsx').read_bytes(),'reference.xlsx')
    assert excel_sha==checksum,'XLSX and JSON labels differ'
docs=[{'id':s['sha256'],'sha256':s['sha256'],'kind':s['kind'],'filename':s['filename']} for s in ref['sources']]
items=[];gold={};totals={'tp':0,'fp':0,'fn':0};audit=[]
for p in ref['passports']:
    passport,issues=normalization_engine.extract(p['source_name'])
    items.append({'id':p['id'],'document_id':p['source_sha256'],'sheet':p['sheet'],'row_index':p['row'],'kind':p['kind'],'source_text':p['source_name'],'raw':p['source_cells'],'passport':passport,'issues':issues})
    gold[p['id']]={'name':p['name'],'attributes':p['attributes'],'category_path':p['category_path']}
    if p['kind']!='residual':continue
    got=_prediction(passport,{},ref);counts={'tp':0,'fp':0,'fn':0};errors=[]
    for key in set(got)|set(p['attributes']):
        expected=p['attributes'].get(key,{}).get('value');actual=got.get(key,[])
        hit=expected is not None and any(equal_value(v,expected) for v in actual)
        tp,fp,fn=int(hit),len(actual)-int(hit),int(expected is not None and not hit)
        for k,v in [('tp',tp),('fp',fp),('fn',fn)]:counts[k]+=v;totals[k]+=v
        if fp or fn:errors.append({'key':key,'expected':expected,'actual':actual})
    audit.append({'id':p['id'],'row':p['row'],'source':p['source_name'],'counts':counts,'errors':errors})
options={'source':'automatic','methods':['h1_regex','h2_kmeans','h3_bm25'],'seed':42,'train_fraction':.7,'clusters':8,'min_cluster_size':5,'repeats':3,'h1_mode':'attributes'}
data={'snapshot':{'session':{'id':'offline-development','schema_doc':{'features':[],'categories':[],'mappings':[]},'taxonomy_enabled':False},'documents':docs,'items':items},'gold':gold,'pairs':ref['pairs'],'reference':ref,'options':options,'input_sha256':checksum,'llm_config':default_config().model_dump()}
result=run(data)
result['model_label']='CPU: регулярки, TF-IDF, BM25; LLM не вызывалась'
result['models']['llm']=None
for key,r in result['methods'].items():
    assert r['status']=='COMPLETED',(key,r)
dest=root.parent/'validation';dest.mkdir(exist_ok=True)
tp,fp,fn=totals.values()
whole={**totals,'precision':tp/(tp+fp),'recall':tp/(tp+fn),'f1':2*tp/(2*tp+fp+fn),'rows':99,'details':audit,'note':'Разработочный набор; правила ранее адаптировались к этой базе. Это не независимое подтверждение H1.'}
(dest/'H1_regex_99.json').write_text(json.dumps(whole,ensure_ascii=False,indent=2))
for h in ('H1','H2','H3'):
    output={**{k:v for k,v in result.items() if k!='methods'},'methods':{k:v for k,v in result['methods'].items() if v['hypothesis']==h},'reference_author':ref['author'],'reference_notes':ref['notes']}
    (dest/f'{h}_offline_experiment.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
print(json.dumps({'whole99':{k:v for k,v in whole.items() if k!='details'},'methods':{k:{'metrics':v['metrics'],'population':v.get('population'),'train_count':v.get('train_count'),'test_count':v.get('test_count'),'catalog_rows':v.get('catalog_rows')} for k,v in result['methods'].items()}},ensure_ascii=False,indent=2))
