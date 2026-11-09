"""Portable model outputs, separate from expert labels and metric summaries."""
from copy import deepcopy
from io import BytesIO
import json
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, Alignment, PatternFill
from app.services.expert_reference import predicted_name

MODEL_FIELDS = ('provider', 'profile_id', 'label', 'model', 'quantization')


def model_info(value):
    return {k: value[k] for k in MODEL_FIELDS if k in (value or {})}


def source_row(item, documents):
    doc = documents.get(item['document_id'], {})
    location = [doc.get('sha256'), item.get('sheet', ''), item['row_index'], item['kind']]
    return {
        'id': json.dumps(location, ensure_ascii=False, separators=(',', ':')),
        'item_id': item['id'], 'source_sha256': doc.get('sha256'),
        'source_filename': doc.get('filename'), 'sheet': item.get('sheet', ''),
        'row': item['row_index'], 'kind': item['kind'],
        'source_text': item.get('source_text', ''), 'source_cells': deepcopy(item.get('raw') or {}),
    }


def base_export(snapshot, job, stage, item_kind):
    session = snapshot['session']
    documents = {d['id']: d for d in snapshot['documents']}
    items = sorted(snapshot['items'], key=lambda i: (i['kind'], i['document_id'], i.get('sheet', ''), i['row_index'], i['id']))
    mappings = {(m['document_id'], m['sheet']): m for m in session.get('schema_doc', {}).get('mappings', [])}
    selected = [i for i in items if (item_kind == 'all' or i['kind'] == item_kind) and
                (i.get('passport') or i['row_index'] > mappings.get((i['document_id'], i.get('sheet', '')), {}).get('header_row', 0))]
    return {
        'format': 'stockexpert-predictions-v1', 'content_kind': 'model_predictions',
        'stage': stage, 'item_kind': item_kind,
        'run': {'session_id': session['id'], 'session_title': session['title'],
                'job_id': job.id, 'kind': job.kind, 'status': job.status,
                'created_at': job.created_at.isoformat(),
                'taxonomy_enabled': session.get('taxonomy_enabled', True),
                'normalization_method': session.get('normalization_method'),
                'model': model_info(session.get('llm_config')),
                'legacy_capture': snapshot.get('legacy_capture', False)},
        'comparison_key': ['source_sha256', 'sheet', 'row', 'kind'],
        'sources': [{k: d.get(k) for k in ('sha256', 'filename', 'kind')} for d in snapshot['documents']],
        'notes': ['Это результаты модели, не экспертная разметка. Для сравнения объединяйте строки по source_sha256, sheet, row, kind; сопоставляйте name с name эталона.',
                  'Повторяющиеся описания разных строк сохранены. Отсутствующий паспорт включён с name=null и status=missing.',
                  'H1 не включает категории H2. По умолчанию выгружаются только складские позиции.'],
    }, selected, documents


def pipeline_export(snapshot, job, stage='all', item_kind='residual', matches=None):
    data, selected, docs = base_export(snapshot, job, stage, item_kind)
    schema = snapshot['session'].get('schema_doc') or {}
    if stage in ('all', 'h1'):
        data['passports'] = []
        for item in selected:
            passport = item.get('passport') or {}
            data['passports'].append({**source_row(item, docs),
                'status': 'ready' if passport else 'missing', 'name': predicted_name(passport),
                'attributes': deepcopy(passport.get('normalized_attributes', passport.get('attributes', {}))),
                'raw_attributes': deepcopy(passport.get('attributes', {})),
                'features': deepcopy(passport.get('normalized_features', schema.get('features', []))),
                'evidence': deepcopy(passport.get('evidence', {})),
                'model': model_info(passport.get('provenance')), 'issues': deepcopy(item.get('issues', []))})
        data['population'] = {'rows': len(selected), 'with_passport': sum(bool(i.get('passport')) for i in selected),
                              'missing': sum(not i.get('passport') for i in selected)}
    if stage in ('all', 'h2'):
        enabled = snapshot['session'].get('taxonomy_enabled', True)
        categories = schema.get('categories', []) if enabled else []
        by_id = {c['id']: c for c in categories}
        def path(ident):
            values, seen = [], set()
            while ident in by_id and ident not in seen:
                seen.add(ident); node = by_id[ident]
                values.insert(0, {'id': ident, 'name': node['name']}); ident = node.get('parent_id')
            return values
        data['h2'] = {'status': 'skipped' if not enabled else 'ready' if categories else 'not_available',
                      'categories': deepcopy(categories), 'assignments': []}
        if enabled:
            data['h2']['assignments'] = [{**source_row(i, docs),
                'name': predicted_name(i.get('passport') or {}),
                'category_id': (i.get('passport') or {}).get('category_id', i.get('category')),
                'category_path': path((i.get('passport') or {}).get('category_id', i.get('category')))} for i in selected]
    if stage in ('all', 'h3'):
        items = {i['id']: i for i in snapshot['items']}
        ready = job.kind in ('match', 'full')
        rows = snapshot.get('matches') if 'matches' in snapshot else matches
        pairs = []
        for match in (rows or []) if ready else []:
            req, stock = items.get(match['requirement_id']), items.get(match['item_id'])
            pairs.append({'id': match['id'], 'requirement_id': match['requirement_id'], 'item_id': match['item_id'],
                          'requirement': source_row(req, docs) if req else None,
                          'stock': source_row(stock, docs) if stock else None,
                          'result': deepcopy(match['result'])})
        pairs.sort(key=lambda p: (p['requirement_id'], p['result'].get('rank') or 10**9, p['item_id']))
        data['h3'] = {'status': 'ready' if ready else 'not_available',
                      'source': 'frozen_snapshot' if 'matches' in snapshot else 'job_match_records',
                      'pair_count': len(pairs), 'pairs': pairs}
        data['notes'].append('H3: все сохранённые пары выбранного запуска, включая позиции вне shortlist. Пары, которые движок не сохранял, не восстанавливаются.')
    return data


def experiment_export(experiment, method=None):
    payload = experiment.payload
    snapshot = payload['snapshot']
    documents = {d['id']: d for d in snapshot['documents']}
    result = deepcopy(experiment.result or {})
    if isinstance(result.get('models', {}).get('llm'), dict):
        result['models']['llm'] = model_info(result['models']['llm'])
    if method is not None:
        result['methods'] = {method: result.get('methods', {})[method]}
    for key, value in result.get('methods', {}).items():
        if key.startswith(('h1_', 'h2_')):
            value['expected'] = {ident: deepcopy(payload.get('gold', {}).get(ident)) for ident in value.get('predictions', {})}
        elif key.startswith('h3_'):
            value['judgements'] = [deepcopy(p) for p in payload.get('pairs', []) if p['requirement_id'] in value.get('predictions', {})]
    # Export stored predictions and measured evaluation, never the gold payload or credentials.
    return {'format': 'stockexpert-experiment-results-v1', 'content_kind': 'model_predictions_and_evaluation',
            'experiment': {'id': experiment.id, 'job_id': experiment.job_id, 'session_id': experiment.session_id,
                           'source': experiment.source, 'dataset_id': experiment.dataset_id,
                           'status': experiment.status, 'created_at': experiment.created_at.isoformat()},
            'comparison_key': ['source_sha256', 'sheet', 'row', 'kind'],
            'sources': [{k:d.get(k) for k in ('sha256','filename','kind')} for d in snapshot['documents']],
            'rows': [source_row(i, documents) for i in snapshot['items']],
            'result': result,
            'notes': ['Результаты взяты из выбранного эксперимента. Ключи predictions — item_id из rows.',
                      'Метрики и expected в лабораторном H2 относятся к использованной разметке; исходный эталон находится в отдельном файле.',
                      'H1/H2 лабораторного эксперимента могут охватывать только закрытую тестовую часть; это не выгрузка всей базы.']}


def revision_export(revision):
    return {'format':'stockexpert-evaluation-v1','content_kind':'saved_evaluation',
            'evaluation':{**{k:getattr(revision,k) for k in ('id','session_id','job_id','source','dataset_id','action')},
                          'created_at':revision.created_at.isoformat()},
            'result':deepcopy(revision.result), 'reviews':deepcopy(revision.inputs.get('reviews',[])),
            'reference':{k:revision.inputs.get(k) for k in ('reference_checksum','reference_author','reference_notes')},
            'source_rows':deepcopy(revision.inputs.get('source_rows',[])),
            'sources':[], 'comparison_key':['job_id','target_id'],
            'notes':['Сохранённая версия оценки. source=manual — ручные ответы; source=automatic — сравнение с загруженным эталоном.',
                     'Результаты запуска H1/H2/H3 скачиваются отдельно по job_id.']}


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def excel_bytes(data):
    book = Workbook(write_only=True)
    def sheet(title, headers, rows):
        ws = book.create_sheet(title); ws.freeze_panes = 'A2'
        from openpyxl.utils import get_column_letter
        for index,header in enumerate(headers,1):
            wide = title.startswith('H2_') and any(k in header for k in ('описание','Путь','Причина','Ограничения','Значение','родитель','потомок','Комментарий'))
            ws.column_dimensions[get_column_letter(index)].width=65 if wide else 25
        if title.startswith('H2_'):ws.row_dimensions[1].height=36
        def cells(values, bold=False):
            for value in values:
                if isinstance(value, (dict, list)):value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and len(value)>30000:value=value[:29900]+'… Полное значение: лист Данные_JSON.'
                cell=WriteOnlyCell(ws, value=value)
                if isinstance(value,str):cell.data_type='s'  # Source text is never an Excel formula.
                if bold:cell.font=Font(bold=True, color='FFFFFF');cell.fill=PatternFill('solid',fgColor='263B59')
                if title.startswith('H2_'):cell.alignment=Alignment(wrap_text=True,vertical='top')
                yield cell
        ws.append(list(cells(headers, True)))
        for row in rows:ws.append(list(cells(row)))
    meta = data.get('run', data.get('experiment', data.get('evaluation', {})))
    sheet('Расчёт', ['Поле', 'Значение'], [*meta.items(), ('format',data['format']),('content_kind',data['content_kind']),('comparison_key',data['comparison_key']), *[(f'Примечание {i+1}',v) for i,v in enumerate(data['notes'])]])
    sheet('Источники', ['sha256','filename','kind'], ([s.get(k) for k in ('sha256','filename','kind')] for s in data['sources']))
    common=['source_sha256','source_filename','sheet','row','kind','item_id','source_text']
    if 'passports' in data:
        cols=common+['name','status','attributes','evidence','model']
        sheet('H1_тексты', cols, ([p.get(k) for k in cols] for p in data['passports']))
        sheet('H1_параметры', ['item_id','source_sha256','sheet','row','key','value','unit','evidence'],
              ([p['item_id'],p['source_sha256'],p['sheet'],p['row'],k,v,next((f.get('unit') for f in p['features'] if f['key']==k),None),p['evidence'].get(k)]
               for p in data['passports'] for k,v in p['attributes'].items()))
    if 'h2' in data:
        sheet('H2_дерево',['status','id','name','parent_id','feature_keys'],
              [[data['h2']['status'],c['id'],c['name'],c.get('parent_id'),c.get('feature_keys')] for c in data['h2']['categories']] or [[data['h2']['status'],None,None,None,None]])
        cols=common+['name','category_id','category_path']
        sheet('H2_позиции',cols,([p.get(k) for k in cols] for p in data['h2']['assignments']))
    if 'h3' in data:
        keys=['rank','pre_rerank_rank','retrieved','is_shortlisted']
        sheet('H3_пары',['requirement_id','item_id','requirement_text','stock_text',*keys,'result'],
              ([p['requirement_id'],p['item_id'],(p['requirement'] or {}).get('source_text'),(p['stock'] or {}).get('source_text'),*[p['result'].get(k) for k in keys],p['result']] for p in data['h3']['pairs']))
    if 'experiment' in data:
        from app.services.h2_report import tables as h2_tables
        for title,headers,records in h2_tables(data):sheet(title,headers,records)
        cols=common
        sheet('Строки эксперимента',cols,([p.get(k) for k in cols] for p in data['rows']))
        sheet('Методы',['method','hypothesis','status','population','metrics','note'],([k,v.get('hypothesis'),v.get('status'),v.get('population'),v.get('metrics'),v.get('note')] for k,v in data['result'].get('methods',{}).items()))
        sheet('Предсказания',['method','item_or_query_id','prediction','expert'],([k,ident,pred,v.get('expected',{}).get(ident)] for k,v in data['result'].get('methods',{}).items() for ident,pred in v.get('predictions',{}).items()))
        if any(v.get('judgements') for v in data['result'].get('methods',{}).values()):
            sheet('H3_эксперт',['method','requirement_id','item_id','relevant'],([k,p['requirement_id'],p['item_id'],p['relevant']] for k,v in data['result'].get('methods',{}).items() for p in v.get('judgements',[])))
    if 'evaluation' in data:
        sheet('Оценка',['Поле','Значение'],data['result'].items())
        sheet('Ручные ответы',['kind','target_id','prediction','answer'],([r.get(k) for k in ('kind','target_id','prediction','answer')] for r in data['reviews']))
        if data.get('source_rows'):
            keys=['id','document_id','sheet','row_index','kind','raw','source_text']
            sheet('Исходные строки',keys,([r.get(k) for k in keys] for r in data['source_rows']))
        if data['result'].get('rows'):
            keys=list(dict.fromkeys(k for r in data['result']['rows'] for k in r))
            sheet('Построчное сравнение',keys,([r.get(k) for k in keys] for r in data['result']['rows']))
        if data['result'].get('pairs'):
            keys=list(dict.fromkeys(k for r in data['result']['pairs'] for k in r))
            sheet('Пары и эксперт',keys,([r.get(k) for k in keys] for r in data['result']['pairs']))
    full=json_bytes(data).decode('utf-8')
    sheet('Данные_JSON',['part','json_chunk'],([n//30000+1,full[n:n+30000]] for n in range(0,len(full),30000)))
    output=BytesIO();book.save(output);return output.getvalue()
