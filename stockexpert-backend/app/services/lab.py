"""Reproducible, isolated H1/H2/H3 experiments. Test item labels never enter inference; the fixed H2 category catalogue is shared."""
from collections import Counter,defaultdict
from copy import deepcopy
from hashlib import sha256
from math import log2
from time import perf_counter
from types import SimpleNamespace
import json
from sqlalchemy import select
from app.db import QualityReview,Match
from app.services import llm,search,passports,normalization_engine
from app.services.expert_evaluation import hungarian_score,category_path,_prediction
from app.services.expert_reference import equal_value,text_key,name_counts
from app.services.normalization import compare
from app.core.config import settings

METHODS={
 'h1_llm_raw':{'hypothesis':'H1','label':'LLM: исходные значения без проверки цитат'},
 'h1_hybrid':{'hypothesis':'H1','label':'LLM + нормализация + дополнение правилами'},
 'h1_saved':{'hypothesis':'H1','label':'Сохранённый результат H1'},
 'h1_regex':{'hypothesis':'H1','label':'Правила и регулярные выражения'},
 'h1_llm':{'hypothesis':'H1','label':'Выбранная LLM + цитаты и единицы'},
 'h2_kmeans':{'hypothesis':'H2','label':'TF-IDF + k-means по исходным строкам'},
 'h2_raw':{'hypothesis':'H2','label':'Векторизация строк + HDBSCAN'},
 'h2_passports':{'hypothesis':'H2','label':'Векторизация паспортов + HDBSCAN'},
 'h2_llm':{'hypothesis':'H2','label':'LLM напрямую по паспортам: фиксированное дерево'},
 'h2_hybrid':{'hypothesis':'H2','label':'Паспорта + HDBSCAN + LLM'},
 'h3_bm25':{'hypothesis':'H3','label':'BM25'},
 'h3_dense':{'hypothesis':'H3','label':'Dense'},
 'h3_rrf':{'hypothesis':'H3','label':'Dense + BM25 + RRF'},
 'h3_rules':{'hypothesis':'H3','label':'RRF + правила без Cross-Encoder'},
 'h3_cascade':{'hypothesis':'H3','label':'Полный каскад: RRF + правила + Cross-Encoder'},
 'h3_no_rules':{'hypothesis':'H3','label':'RRF + Cross-Encoder без правил'},
 'h3_full_ce':{'hypothesis':'H3','label':'Cross-Encoder по всему каталогу'},
 'h3_splade':{'hypothesis':'H3','label':'Каскад Dense + SPLADE + RRF'},
}

def make_input(db,snapshot,job,reference,options):
    stock=[i for i in snapshot['items'] if i['kind']=='residual' and i.get('passport')]
    docs={d['id']:d for d in snapshot['documents']}; gold={};pairs=[];ref=reference.payload if reference else None
    if ref:
        if {d['sha256'] for d in docs.values() if d['kind']=='residual'}!={s['sha256'] for s in ref['sources'] if s['kind']=='residual'}:raise ValueError('SHA-256 базы не соответствует эталону')
        byloc={(docs[i['document_id']]['sha256'],i['sheet'],i['row_index'],i['kind']):i for i in snapshot['items']};aligned={}
        for p in ref['passports']:
            i=byloc.get((p['source_sha256'],p['sheet'],p['row'],p['kind']))
            if not i:continue
            if p['source_cells'] and i['raw']!=p['source_cells']:raise ValueError('Исходные ячейки изменились относительно эталона')
            if i['kind']=='requirement' and text_key(i['source_text'])!=text_key(p['source_name']):raise ValueError('Текст опросного листа отличается от эталона')
            aligned[p['id']]=i['id'];gold[i['id']]={'name':p['name'],'attributes':p['attributes'],'category_path':p['category_path']}
            if p.get('split_group'):gold[i['id']]['split_group']=p['split_group']
        mappings={(m['document_id'],m['sheet']):m for m in snapshot['session'].get('schema_doc',{}).get('mappings',[])}
        by_id={i['id']:i for i in snapshot['items']}
        missing=[]
        for p in ref['passports']:
            if p['kind']!='residual':continue
            item=by_id.get(aligned.get(p['id']))
            if item is None or item['row_index']<=mappings.get((item['document_id'],item['sheet']),{}).get('header_row',0):missing.append(p['id'])
        if missing:raise ValueError(f'Снимок не охватывает {len(missing)} строк эталона (например, {missing[0]}). Проверьте строку заголовка и состав базы; тест на неполной выборке не запускается.')
        for p in ref['pairs']:
            if p['requirement_id'] in aligned and p['item_id'] in aligned:pairs.append({'requirement_id':aligned[p['requirement_id']],'item_id':aligned[p['item_id']],'relevant':p['relevant']})
    else:
        match_by_id={m.id:m for m in db.scalars(select(Match).where(Match.job_id==job.id))}
        for review in db.scalars(select(QualityReview).where(QualityReview.job_id==job.id)):
            if review.kind=='passport':
                a=review.answer;path=category_path(snapshot['session']['schema_doc'],{'category_id':a.get('category_id')}) if a.get('category_reviewed') else []
                if a.get('category_reviewed') and a.get('expected_category'):path=[a['expected_category']]
                gold[review.target_id]={'name':a.get('attributes',{}).get('name'),'attributes':{k:{'value':v,'reviewed':True} for k,v in a.get('attributes',{}).items()},'custom_features':a.get('custom_features',[]),'category_path':path}
            elif review.target_id in match_by_id:
                m=match_by_id[review.target_id];pairs.append({'requirement_id':m.requirement_id,'item_id':m.item_id,'relevant':review.answer.get('suitable')})
    if not gold and not pairs:raise ValueError('Нет разметки выбранного источника. Сначала сохраните ручные ответы или загрузите эталон.')
    return {'snapshot':deepcopy(snapshot),'gold':gold,'pairs':pairs,'reference':ref,'options':options,'input_sha256':sha256(json.dumps({'snapshot':snapshot,'gold':gold,'pairs':pairs},ensure_ascii=False,sort_keys=True).encode()).hexdigest()}

def split_rows(rows,options):
    # Identical source descriptions stay in the same partition. Test labels are unseen.
    train=[];test=[]
    for row in rows:
        key=f"{options['seed']}:{text_key(row['source_text'])}"
        (train if int(sha256(key.encode()).hexdigest()[:8],16)/2**32<options['train_fraction'] else test).append(row)
    return train,test

def passport_text(row):
    p=row.get('passport') or {};attrs=p.get('normalized_attributes',p.get('attributes',{}))
    features={f['key']:f for f in p.get('normalized_features',[])}
    return (p.get('name') or row['source_text'])+'; '+ '; '.join(f"{features.get(k,{}).get('label',k)}: {v} {features.get(k,{}).get('unit','')}" for k,v in sorted(attrs.items()) if k!='name' and v is not None)

def embeddings(texts):
    import numpy as np
    from sklearn.preprocessing import normalize
    vectors=[]
    for pos in range(0,len(texts),16):vectors.extend(llm.embed(texts[pos:pos+16]))
    array=np.asarray(vectors,dtype=float)
    if len(array)!=len(texts) or not np.isfinite(array).all():raise ValueError('Некорректный ответ модели векторизации')
    return normalize(array)

def bootstrap(values,seed):
    import numpy as np
    if len(values)<2:return None
    rng=np.random.default_rng(seed);means=[float(np.mean(rng.choice(values,len(values),replace=True))) for _ in range(1000)]
    return [float(np.quantile(means,.025)),float(np.quantile(means,.975))]

def h1(method,data,rows):
    counts=Counter();predictions={};row_counts={};durations=[];gold=data['gold'];ref=data['reference']
    text_only = ref.get('h1_mode') == 'name_exact' if ref else data['options'].get('h1_mode', 'attributes') == 'name_exact'
    _,rows=split_rows(rows,data['options'])
    if not rows:raise ValueError('Нет строк в закрытой тестовой части')
    for row in rows:
        if row['id'] not in gold:continue
        if text_only and gold[row['id']].get('name') is None:continue
        started=perf_counter()
        if method=='h1_saved':p=row.get('passport') or {}
        elif method=='h1_regex':p,_=normalization_engine.extract(row['source_text'])
        else:
            from app.services.lab_h1 import extract
            p=extract(method,row['source_text'])
        durations.append(perf_counter()-started);predictions[row['id']]=p
        before=counts.copy()
        if text_only:
            for key, value in zip(('tp','fp','fn'), name_counts(p, gold[row['id']]['name'])):counts[key]+=value
            row_counts[row['id']]={k:counts[k]-before[k] for k in ('tp','fp','fn')}
            continue
        if ref:pred=_prediction(p,data['snapshot']['session']['schema_doc'],ref)
        else:
            # Review keys are schema keys. Map by exact label+unit via frozen specs.
            specs={f['key']:f for f in data['snapshot']['session']['schema_doc'].get('features',[])}
            specs.update({f['key']:f for f in gold[row['id']].get('custom_features',[])})
            original=row.get('passport') or {};source_specs={f['key']:f for f in p.get('normalized_features',[])}
            original_specs={f['key']:f for f in original.get('normalized_features',[])}
            pred={}
            for key in gold[row['id']]['attributes']:
                f=specs.get(key) or original_specs.get(key) or {};values=[]
                for pk,pv in p.get('normalized_attributes',p.get('attributes',{})).items():
                    sf=source_specs.get(pk,{})
                    if pk==key or (f and text_key(sf.get('label',''))==text_key(f.get('label','')) and sf.get('unit')==f.get('unit')):values.append(pv)
                pred[key]=values
        expected=gold[row['id']]['attributes'];keys=set(expected)|set(pred) if ref else set(expected)
        for key in keys:
            if key=='name':continue
            g=expected.get(key)
            if g and not g.get('reviewed'):continue
            want=g['value'] if g else None;got=[v for v in pred.get(key,[]) if v is not None]
            hit=want is not None and any(equal_value(v,want) for v in got)
            counts['tp']+=int(hit);counts['fp']+=len(got)-int(hit);counts['fn']+=int(want is not None and not hit)
        row_counts[row['id']]={k:counts[k]-before[k] for k in ('tp','fp','fn')}
    tp,fp,fn=(counts[k] for k in ('tp','fp','fn'));den=2*tp+fp+fn
    return {'h1_mode':'name_exact' if text_only else 'attributes','metrics':{'f1':2*tp/den if den else None,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,**dict(counts)},'predictions':predictions,'row_counts':row_counts,'population':len(predictions),'seconds':sum(durations) if method!='h1_saved' else None,'note':('Точное совпадение текста: одна тестовая строка — одно сравнение; категории и параметры исключены. ' if text_only else 'Micro по проверенным техническим параметрам. ')+('Скорость сохранённого результата не измеряется заново.' if method=='h1_saved' else 'Ошибка значения даёт FP и FN.')}

def h2(method,data,rows):
    from app.services.lab_h2 import evaluate
    return evaluate(method,data,rows,split_rows,bootstrap,hungarian_score,llm.structured,embeddings)


class Splade:
    def __init__(self):
        import torch
        from transformers import AutoTokenizer,AutoModelForMaskedLM
        if not settings.SPLADE_MODEL_PATH:raise ValueError('Укажите SPLADE_MODEL_PATH с локальными весами. Автоматическая загрузка весов отключена.')
        self.torch=torch;self.tokenizer=AutoTokenizer.from_pretrained(settings.SPLADE_MODEL_PATH,local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForMaskedLM.from_pretrained(settings.SPLADE_MODEL_PATH,local_files_only=True,trust_remote_code=False).eval()
    def encode(self,texts):
        from scipy.sparse import csr_matrix,vstack
        chunks=[]
        for pos in range(0,len(texts),4):
            tokens=self.tokenizer(texts[pos:pos+4],padding=True,truncation=True,max_length=512,return_tensors='pt')
            with self.torch.no_grad():
                logits=self.model(**tokens).logits
                weights=self.torch.log1p(self.torch.relu(logits))*tokens['attention_mask'].unsqueeze(-1)
                chunks.append(csr_matrix(weights.max(dim=1).values.cpu().numpy()))
        return vstack(chunks)

def h3(method,data,rows):
    import numpy as np
    from rank_bm25 import BM25Okapi
    options=data['options'];pairs=data['pairs'];ids={r['id'] for r in rows};gold=defaultdict(dict)
    for p in pairs:gold[p['requirement_id']][p['item_id']]=p['relevant']
    reqs=[r for r in data['snapshot']['items'] if r['kind']=='requirement' and r['id'] in gold and ids<=set(gold[r['id']]) and all(gold[r['id']][i] is not None for i in ids)]
    if not reqs:raise ValueError('Для H3 нужна разметка каждой позиции каталога хотя бы для одной потребности, включая позиции вне shortlist')
    use_ce=method in ('h3_cascade','h3_no_rules','h3_full_ce','h3_splade')
    if use_ce and not settings.RERANKER_ENABLED:raise ValueError('Cross-Encoder отключён на сервере; этот метод не измерен')
    objects=[SimpleNamespace(**r) for r in rows];texts=[r['source_text'] for r in rows]
    index_started=perf_counter();dense=None;bm25=None;splade=None;sparse=None
    if method not in ('h3_bm25','h3_full_ce'):dense=embeddings(texts)
    if method not in ('h3_dense','h3_full_ce','h3_splade'):bm25=BM25Okapi([search.tokens(t) or ['_empty_'] for t in texts])
    if method=='h3_splade':splade=Splade();sparse=splade.encode(texts)
    if use_ce:search.reranker()  # Loading weights is setup, excluded from query latency.
    index_seconds=perf_counter()-index_started;schema=data['snapshot']['session']['schema_doc'];enabled=data['snapshot']['session'].get('taxonomy_enabled',True)
    from app.services.taxonomy import features_for,root_id
    def query(req):
        scores={};ranks=[]
        if dense is not None:ranks.append(np.argsort(-(dense@embeddings([req['source_text']])[0]),kind='stable')[:600].tolist())
        if bm25 is not None:
            values=bm25.get_scores(search.tokens(req['source_text']));ranks.append([int(i) for i in np.argsort(-values,kind='stable')[:600] if values[i]>0])
        if splade:
            values=(sparse@splade.encode([req['source_text']]).T).toarray().ravel();ranks.append([int(i) for i in np.argsort(-values,kind='stable')[:600] if values[i]>0])
        for ranking in ranks:
            for rank,i in enumerate(ranking,1):scores[i]=scores.get(i,0)+1/(60+rank)
        ordered=sorted(scores,key=lambda i:(-scores[i],rows[i]['id']))[:600]
        retrieved=list(ordered)
        if method in ('h3_rules','h3_cascade','h3_splade'):
            p=req.get('passport') or {};features=features_for(schema,p.get('category_id')) if enabled else schema.get('features',[])
            allowed=[]
            for i in ordered:
                other=rows[i].get('passport') or {};comparison=compare(p.get('attributes',{}),other.get('attributes',{}),features)
                mismatch=enabled and p.get('category_id') and other.get('category_id') and root_id(schema,p['category_id'])!=root_id(schema,other['category_id']) and not(passports.needs_review(p) or passports.needs_review(other))
                if comparison['state']!='CONTRADICTED' and not mismatch:allowed.append(i)
            ordered=allowed
        after_rules=list(ordered)
        pre=ordered[:150]
        if method=='h3_full_ce':
            scores_ce=search.rerank(req['source_text'],objects);ordered=sorted(range(len(rows)),key=lambda i:(-scores_ce[rows[i]['id']],rows[i]['id']));pre=ordered[:150]
        elif use_ce:
            scores_ce=search.rerank(req['source_text'],[objects[i] for i in pre]);ordered=sorted(pre,key=lambda i:(-scores_ce[rows[i]['id']],rows[i]['id']))
        else:ordered=pre
        return retrieved,after_rules,pre,ordered
    # Warm-up is deliberately excluded and recorded in the protocol.
    query(reqs[0]);times=[];details=[];predictions={}
    latency_groups=[]
    for req in reqs:
        query_times=[]
        for _ in range(options['repeats']):
            start=perf_counter();retrieved,after_rules,pre,ordered=query(req);elapsed=(perf_counter()-start)*1000;times.append(elapsed);query_times.append(elapsed)
        latency_groups.append(query_times)
        relevant={i for i,v in gold[req['id']].items() if v is True and i in ids};top={rows[i]['id'] for i in pre};final=[rows[i]['id'] for i in ordered]
        denominator=len(relevant);hits=len(relevant&top)
        dcg=sum(1/log2(n+2) for n,i in enumerate(final[:10]) if i in relevant);idcg=sum(1/log2(n+2) for n in range(min(10,denominator)))
        details.append({'requirement_id':req['id'],'relevant':denominator,'hits150':hits,'recall150':hits/denominator if denominator else None,'ndcg10':dcg/idcg if idcg else None,'lost_retrieval':len(relevant-{rows[i]['id'] for i in retrieved}) if method!='h3_full_ce' else None,'lost_rules':len(relevant&{rows[i]['id'] for i in retrieved})-len(relevant&{rows[i]['id'] for i in after_rules}) if method!='h3_full_ce' else None,'lost_cutoff':len(relevant&{rows[i]['id'] for i in after_rules})-hits if method!='h3_full_ce' else None})
        predictions[req['id']]={'pre_rerank_ids':[rows[i]['id'] for i in pre],'ranked_ids':final}
    recalls=[d['recall150'] for d in details if d['recall150'] is not None];ndcg=[d['ndcg10'] for d in details if d['ndcg10'] is not None]
    intervals={}
    if len(latency_groups)>1:
        rng=np.random.default_rng(options['seed']);samples={50:[],95:[]}
        for _ in range(1000):
            sample=[t for index in rng.integers(0,len(latency_groups),len(latency_groups)) for t in latency_groups[index]]
            for percentile in samples:samples[percentile].append(float(np.percentile(sample,percentile,method='inverted_cdf')))
        intervals={f'p{p}_ci95_ms':[float(np.quantile(values,.025)),float(np.quantile(values,.975))] for p,values in samples.items()}
    return {'metrics':{**intervals,**{key:sum(d[key] for d in details) if method!='h3_full_ce' else None for key in ('lost_retrieval','lost_rules','lost_cutoff')},'ndcg_ci95':bootstrap(ndcg,options['seed']),'recall150':float(np.mean(recalls)) if recalls else None,'recall_ci95':bootstrap(recalls,options['seed']),'ndcg10':float(np.mean(ndcg)) if ndcg else None,'p50_ms':float(np.percentile(times,50,method='inverted_cdf')),'p95_ms':float(np.percentile(times,95,method='inverted_cdf'))},'population':len(reqs),'catalog_rows':len(rows),'query_samples':len(times),'latency_groups_ms':latency_groups,'index_seconds':index_seconds,'queries':details,'predictions':predictions,'note':'Recall — среднее по запросам с подходящими позициями, cutoff после правил до Cross-Encoder; у полного CE — после ранжирования каталога. Таймер: запрос, извлечение, правила, rerank; индекс и загрузка модели отдельно. Один прогрев исключён. '+('Каталог меньше 12 000: результат не подтверждает целевой масштаб. ' if len(rows)<12000 else '')+('Меньше 20 запросов: p95 предварительный.' if len(reqs)<20 else '')}

def stock_rows(data):
    schema=data['snapshot']['session']['schema_doc'];mappings={(m['document_id'],m['sheet']):m for m in schema.get('mappings',[])}
    rows=[]
    for row in data['snapshot']['items']:
        if row['kind']!='residual':continue
        mapping=mappings.get((row['document_id'],row['sheet']),{})
        if row['row_index']<=mapping.get('header_row',0):continue
        row=deepcopy(row)
        if not row['source_text']:row['source_text']=' | '.join(str(row['raw'].get(k,'')) for k in mapping.get('name_columns',row['raw'].keys()))
        rows.append(row)
    rows.sort(key=lambda r:r['id'])
    return rows

def run(data,progress=lambda result:None,previous=None):
    rows=stock_rows(data)
    result={'methods':{},'input_sha256':data['input_sha256'],'options':data['options'],'source':data['options']['source'],'protocol':'lab-v3-h2-fixed-tree','model_label':data.get('llm_config',{}).get('label',data.get('llm_config',{}).get('model','')),'models':{'llm':data.get('llm_config',{}),'embedding':settings.EMBEDDING_MODEL,'reranker':settings.RERANKER_MODEL,'splade':settings.SPLADE_MODEL_PATH},'conclusions':{}}
    if previous and previous.get('input_sha256')==data['input_sha256'] and previous.get('protocol')==result['protocol']:result['methods']=deepcopy(previous.get('methods',{}))
    if not rows:raise ValueError('Нет нормализованных строк каталога')
    import platform,os,importlib.metadata
    result['environment']={'python':platform.python_version(),'platform':platform.system(),'cpu':platform.processor(),'cpu_count':os.cpu_count(),'packages':{name:importlib.metadata.version(name) for name in ('scikit-learn','hdbscan','numpy')}}
    from app.services.llm import LLMConfig
    with llm.using_config(LLMConfig.model_validate(data['llm_config'])):
        for method in data['options']['methods']:
            if result['methods'].get(method,{}).get('status')=='COMPLETED':continue
            started=perf_counter();spec=dict(METHODS[method])
            if method in ('h1_llm','h1_llm_raw','h1_hybrid','h2_llm','h2_hybrid'):spec['label']+=' · '+data['llm_config'].get('label',data['llm_config'].get('model',''))
            try:
                fn=h1 if method.startswith('h1_') else h2 if method.startswith('h2_') else h3
                output=fn(method,data,rows)
                result['methods'][method]={**spec,'status':'COMPLETED','seconds':perf_counter()-started,**output}
            except Exception as exc:result['methods'][method]={**spec,'status':'ERROR','metrics':{},'note':f'{type(exc).__name__}: {str(exc)[:600]}'}
            progress(result)
    # Paired differences always use identical test rows/queries from this experiment.
    methods=result['methods']
    from app.services.lab_h2 import conclusions
    result['conclusions']['h2']=conclusions(methods,data['options'],bootstrap)
    from app.services.lab_h1 import paired_interval
    baseline_h1=methods.get('h1_regex',{})
    for key,current in methods.items():
        if key.startswith('h1_') and key != 'h1_regex' and current.get('row_counts') and baseline_h1.get('row_counts'):
            delta,interval=paired_interval(current['row_counts'],baseline_h1['row_counts'],rows,data['options']['seed'])
            current['metrics'].update(delta_f1=delta,delta_ci95=interval)
    cascade=methods.get('h3_cascade',{}).get('metrics',{});full=methods.get('h3_full_ce',{}).get('metrics',{})
    result['conclusions']['h3']= {'recall_threshold':cascade.get('recall150',0)>=.985 if cascade.get('recall150') is not None else None,'faster_p95':cascade['p95_ms']<full['p95_ms'] if cascade.get('p95_ms') is not None and full.get('p95_ms') is not None else None,'note':'Порог качества и выигрыш по p95 проверяются раздельно. Для вывода о 12 000 позиций нужен каталог этого размера.'}
    return result


def summarize(result):
    return {**result,'methods':{key:{k:v for k,v in value.items() if k not in ('predictions','queries','tree','mapping','latency_groups_ms','row_counts','approved_tree','edges','split')} for key,value in result.get('methods',{}).items()}}
