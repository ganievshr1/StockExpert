"""Independent reports and comparable cohorts for H1, H2 and H3."""
from copy import deepcopy
from hashlib import sha256
import json


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def cohort(snapshot, hypothesis, reference_checksum, options=None, manual_labels=None):
    options = options or {}
    docs = {d['id']: d for d in snapshot['documents']}
    positions = sorted((docs[i['document_id']]['sha256'], i['sheet'], i['row_index'], i['kind'])
                       for i in snapshot['items'] if hypothesis == 'H3' or i['kind'] == 'residual')
    return digest({'hypothesis': hypothesis, 'reference': reference_checksum, 'rows': positions,
                   'manual_labels': manual_labels,
                   'scope': 'closed_test' if options else 'saved_run',
                   'seed': options.get('seed'), 'train_fraction': options.get('train_fraction'),
                   'h1_mode': options.get('h1_mode', 'attributes') if hypothesis == 'H1' else None,
                   'h2_protocol': options.get('h2_protocol','legacy-hungarian') if hypothesis == 'H2' else None,
                   'protocol': 'v5'})


def select_hypothesis(data, stage):
    if stage == 'all':
        return data
    data = deepcopy(data)
    h = stage.upper()
    data['hypothesis'] = h
    result = data.get('result', {})
    if 'methods' in result:
        result['methods'] = {k:v for k,v in result['methods'].items() if v.get('hypothesis') == h}
        result['conclusions'] = {k:v for k,v in result.get('conclusions', {}).items() if k == stage}
        data['rows'] = [r for r in data.get('rows', []) if h == 'H3' or r.get('kind') == 'residual']
        return data
    if 'hypotheses' in result:
        result['hypotheses'] = [r for r in result['hypotheses'] if r['id'] == h]
        result['metrics'] = {k:v for k,v in result.get('metrics', {}).items() if k.startswith(stage + '_') or (h == 'H1' and k == 'name_accuracy')}
        if h == 'H1':
            result['rows'] = [{k:v for k,v in r.items() if 'category' not in k} for r in result.get('rows', [])]
            for key in ('pairs','h3_queries','h3_population','label_mapping'):
                result.pop(key, None)
        elif h == 'H2':
            result['rows'] = [{k:v for k,v in r.items() if k in ('id','sheet','row','source_text','source_cells','name','missing') or 'category' in k} for r in result.get('rows', [])]
            for key in ('pairs','fields','h3_queries','h3_population'):
                result.pop(key, None)
        else:
            for key in ('rows','fields','label_mapping'):
                result.pop(key, None)
    else:
        keys = {'H1': ('f1','text_f1','text_precision','text_recall','fields'), 'H2': ('accuracy',),
                'H3': ('recall_at_150','recall_micro_at_150','precision_shortlist','recall_stage')}[h]
        data['result'] = {k:v for k,v in result.items() if k in (*keys,'job_id','source','finalized','coverage','note','taxonomy_enabled')}
    data['reviews'] = [r for r in data.get('reviews', []) if (r['kind'] == 'candidate') == (h == 'H3')]
    data['source_rows'] = [r for r in data.get('source_rows', []) if h == 'H3' or r.get('kind') == 'residual']
    for review in data['reviews']:
        if h == 'H1':
            review['answer'] = {k:v for k,v in review['answer'].items() if k in ('attributes','comment')}
        elif h == 'H2':
            review['answer'] = {k:v for k,v in review['answer'].items() if k in ('category_id','category_reviewed','expected_category','comment')}
    data['notes'].append('Отдельный отчёт ' + h + '. Числа относятся только к указанной версии расчёта и разметки.')
    return data
