"""Snapshot, run and score the regex H1 baseline without touching pipeline data."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import logging
import math
import os
import platform
from time import perf_counter

from sqlalchemy import select, update
from app.db import Document, Item, Job, NormalizationExperiment, ExpertDataset, ExpertEvaluation, SessionLocal, now
from app.services.execution import ACTIVE_STATUSES
from app.services.expert_evaluation import _prediction, compatible, ratio
from app.services.expert_reference import equal_value
from app.services import regex_normalization as rules


def extraction_summary(rows, predictions):
    counts, fields, families, issue_codes = Counter(), Counter(), Counter(), Counter()
    groups = defaultdict(Counter)
    non_parameters = {'name', 'designation', 'component_designation', 'reference_code', 'variant_code'}
    for row, prediction in zip(rows, predictions):
        passport = prediction['passport']
        keys = {k for k, v in passport['attributes'].items() if v is not None and k != 'name'}
        parameters = keys - non_parameters
        counts['rows'] += 1
        counts['rows_with_attributes'] += bool(keys)
        counts['rows_with_parameters'] += bool(parameters)
        counts['rows_with_designation'] += 'designation' in keys
        counts['review_rows'] += bool(prediction['issues'])
        counts['model_only_rows'] += bool(keys) and not parameters
        fields.update(keys)
        families.update(passport.get('type_hints', []))
        issue_codes.update({i['code'] for i in prediction['issues']})
        group = groups[row.get('source_group') or 'Группа не задана']
        group['rows'] += 1
        group['rows_with_parameters'] += bool(parameters)
        group['review_rows'] += bool(prediction['issues'])
    return {**{key: counts[key] for key in ('rows', 'rows_with_attributes', 'rows_with_parameters', 'rows_with_designation', 'review_rows', 'model_only_rows')},
            'fields': dict(sorted(fields.items())), 'families': dict(sorted(families.items())),
            'issue_codes': dict(sorted(issue_codes.items())),
            'source_groups': [{'name': name, **dict(value)} for name, value in sorted(groups.items())],
            'note': 'Охват правил и количество извлечённых значений не являются точностью, полнотой или F1.'}


def unscored_result():
    return {'evaluation_status': 'NO_REFERENCE', 'metrics': {key: None for key in (
        'h1_precision', 'h1_recall', 'h1_f1', 'name_accuracy', 'full_row_recall', 'exact_row_accuracy',
        'h2_leaf_accuracy', 'h3_recall_at_150')}, 'coverage': {}, 'rows': [], 'fields': [],
        'notes': ['Экспертный эталон не выбран. Извлечение выполнено, показатели качества не рассчитаны.',
                  'Колонка B сохранена как исходная группа, а не как результат классификации H2.']}


def snapshot(db, session, reference):
    if db.scalar(select(Job.id).where(Job.session_id == session.id, Job.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise ValueError('Дождитесь завершения или отмены текущего задания исходной сессии.')
    if not compatible(db, session.id, reference.payload):
        raise ValueError('Excel исходной сессии не совпадает с выбранным эталоном.')
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.session_id == session.id, Document.kind == 'residual'))}
    mappings = {(m['document_id'], m['sheet']): m for m in (session.schema_doc or {}).get('mappings', [])}
    rows = []
    for item in db.scalars(select(Item).where(Item.session_id == session.id, Item.kind == 'residual').order_by(Item.document_id, Item.sheet, Item.row_index)):
        mapping = mappings.get((item.document_id, item.sheet))
        if mapping and item.row_index <= mapping['header_row']:
            continue
        text = item.source_text
        if not text.strip() and mapping:
            text = ' | '.join(str(item.raw[c]) for c in mapping['name_columns'] if c in item.raw)
        if not text.strip():
            raise ValueError(f'Нет исходного текста: лист {item.sheet}, строка {item.row_index}. Сначала подтвердите сопоставление столбцов исходной сессии.')
        if len(text) > 16000:
            raise ValueError('Исходная строка превышает 16000 символов.')
        rows.append({'source_item_id': item.id, 'source_sha256': docs[item.document_id].sha256,
                     'document': docs[item.document_id].filename, 'sheet': item.sheet,
                     'row': item.row_index, 'kind': item.kind, 'source_text': text, 'raw': deepcopy(item.raw)})
    if not rows or len(rows) > 25000:
        raise ValueError('Для эксперимента нужно от 1 до 25000 подготовленных складских строк.')
    rows.sort(key=lambda r: (r['source_sha256'], r['sheet'], r['row']))
    payload = {'rows': rows, 'mappings': deepcopy(list(mappings.values())),
               'sources': [{'filename': d.filename, 'sha256': d.sha256} for d in docs.values()],
               'reference_checksum': reference.checksum, 'reference_version': reference.version,
               'input_scope': 'Сохранённые исходные строки; сопоставление столбцов зафиксировано. Новых LLM-вызовов нет.'}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    if len(serialized) > 64 * 1024 * 1024:
        raise ValueError('Снимок превышает 64 МБ; разделите базу на части.')
    saved = db.scalar(select(ExpertEvaluation).where(
        ExpertEvaluation.session_id == session.id, ExpertEvaluation.dataset_id == reference.id))
    baseline = saved.result if saved and saved.status in ('READY', 'PARTIAL') else {}
    return payload, hashlib.sha256(serialized).hexdigest(), {
        'evaluation_id': saved.id if saved else None,
        'evaluated_at': saved.updated_at.isoformat() if saved else None,
        'metrics': {k: v for k, v in baseline.get('metrics', {}).items() if k.startswith('h1_') or k == 'name_accuracy'},
        'models': baseline.get('models', []), 'timing': baseline.get('timing', {}),
        'source_jobs': baseline.get('source_jobs', []), 'metric_version': baseline.get('metric_version'),
        'note': 'Снимок сохранённых результатов исходной сессии. Время исторического прогона может иметь другой состав этапов и окружение.'}


def score(rows, predictions, reference):
    """Use the same field mapper and value comparator as saved LLM evaluations."""
    expected = [p for p in reference['passports'] if p['kind'] == 'residual']
    by_location = {(r['source_sha256'], r['sheet'], r['row']): (r, p) for r, p in zip(rows, predictions)}
    counts, fields, details = Counter(), defaultdict(Counter), []
    missing, changed, locations = [], [], set()
    for gold in expected:
        loc = (gold['source_sha256'], gold['sheet'], gold['row'])
        locations.add(loc)
        item, prediction = by_location.get(loc, ({}, {}))
        if item and gold['source_cells'] and item['raw'] != gold['source_cells']:
            changed.append(gold['id']); prediction = {}
        passport = prediction.get('passport', {})
        if not passport:
            missing.append(gold['id'])
        else:
            counts['passports'] += 1
        counts['names_correct'] += equal_value(passport.get('name'), gold['name'])
        actual = _prediction(passport, {}, reference)
        row_errors, expected_count, correct_count = [], 0, 0
        for key in set(gold['attributes']) | set(actual):
            g = gold['attributes'].get(key)
            if g and not g['reviewed']:
                continue
            value = g['value'] if g else None
            got = actual.get(key, [])
            hit = value is not None and any(equal_value(v, value) for v in got)
            tp, fp, fn = int(hit), len(got)-int(hit), int(value is not None and not hit)
            expected_count += int(value is not None); correct_count += tp
            for metric, n in [('tp', tp), ('fp', fp), ('fn', fn)]:
                counts[metric] += n; fields[key][metric] += n
            if fp or fn:
                row_errors.append({'key': key, 'expected': value, 'actual': got, 'tp': tp, 'fp': fp, 'fn': fn})
        # This is a separate row metric; its denominator never exceeds the gold row count.
        complete = bool(passport) and expected_count > 0 and correct_count == expected_count
        counts['fully_recovered_rows'] += complete
        counts['exact_rows'] += complete and not row_errors
        details.append({'id': gold['id'], 'sheet': gold['sheet'], 'row': gold['row'], 'name': gold['name'],
                        'missing': not bool(passport), 'errors': row_errors, 'fully_recovered': complete,
                        'exact_row': complete and not row_errors})
    tp, fp, fn = counts['tp'], counts['fp'], counts['fn']
    extras = [r['source_item_id'] for r in rows if (r['source_sha256'], r['sheet'], r['row']) not in locations]
    return {'metric_version': 'regex-h1-v1; field comparison: expert-metrics-v2',
            'metrics': {'h1_precision': ratio(tp, tp+fp), 'h1_recall': ratio(tp, tp+fn),
                        'h1_f1': ratio(2*tp, 2*tp+fp+fn), 'name_accuracy': ratio(counts['names_correct'], len(expected)),
                        'full_row_recall': ratio(counts['fully_recovered_rows'], len(expected)),
                        'exact_row_accuracy': ratio(counts['exact_rows'], len(expected)),
                        'h2_leaf_accuracy': None, 'h3_recall_at_150': None},
            'coverage': {**dict(counts), 'expected_stock': len(expected), 'normalized_stock': counts['passports'],
                         'missing_ids': missing, 'changed_ids': changed, 'extra_stock_ids': extras},
            'fields': [{'key': k, **dict(v), 'f1': ratio(2*v['tp'], 2*v['tp']+v['fp']+v['fn'])} for k,v in sorted(fields.items())],
            'rows': details,
            'formulas': {'precision': 'TP / (TP + FP)', 'recall': 'TP / (TP + FN)', 'f1': '2TP / (2TP + FP + FN)',
                         'full_row_recall': 'Все эталонные параметры найдены / все строки эталона; строка без размеченных значений не считается восстановленной',
                         'exact_row_accuracy': 'Все эталонные параметры найдены без лишних / все строки эталона'},
            'notes': ['H1 micro-F1 считается по значениям параметров. Полнота строк — отдельный показатель.',
                      'Имена полей и значения сопоставляются так же, как при оценке сохранённых LLM-паспортов.',
                      'H2 и H3 не запускались. Их результаты исходной LLM-сессии не присваиваются регуляркам.',
                      'Правила не читают эталон: разметка используется только после извлечения для оценки.']}


def run_experiment(ident):
    """Single-owner claim; completed output is immutable under duplicate delivery."""
    with SessionLocal() as db:
        claimed = db.execute(update(NormalizationExperiment).where(
            NormalizationExperiment.id == ident, NormalizationExperiment.status == 'QUEUED'
        ).values(status='RUNNING', started_at=now(), heartbeat_at=now()))
        db.commit()
        if claimed.rowcount != 1:
            return
        experiment = db.get(NormalizationExperiment, ident)
        try:
            if experiment.rules_sha256 != rules.RULES_SHA256:
                raise ValueError('Версия правил изменилась после постановки в очередь. Создайте новый эксперимент.')
            reference = db.get(ExpertDataset, experiment.dataset_id) if experiment.dataset_id else None
            if experiment.dataset_id and (not reference or reference.checksum != experiment.input_snapshot['reference_checksum']):
                raise ValueError('Эталон недоступен или изменился после создания снимка.')
            rows = experiment.input_snapshot['rows']
            predictions, durations, elapsed = [], [], 0.0
            started = perf_counter()
            for index, row in enumerate(rows, 1):
                t = perf_counter()
                passport, issues = rules.extract(row['source_text'])
                seconds = perf_counter()-t
                elapsed += seconds; durations.append(seconds*1000)
                predictions.append({'source_item_id': row['source_item_id'], 'source_sha256': row['source_sha256'],
                                    'sheet': row['sheet'], 'row': row['row'], 'passport': passport, 'issues': issues,
                                    'elapsed_ms': seconds*1000})
                if index % 250 == 0:
                    alive = db.execute(update(NormalizationExperiment).where(
                        NormalizationExperiment.id == ident, NormalizationExperiment.status == 'RUNNING'
                    ).values(processed_rows=index, heartbeat_at=now()))
                    db.commit()
                    if alive.rowcount != 1:
                        return
            result = score(rows, predictions, reference.payload) if reference else unscored_result()
            if reference:
                result['evaluation_status'] = 'EVALUATED'
            result['extraction'] = extraction_summary(rows, predictions)
            result['is_development_dataset'] = any(r['source_sha256'] == rules.DEVELOPMENT_SOURCE_SHA256 for r in rows)
            if result['is_development_dataset']:
                result.setdefault('notes', []).append('Правила v2 адаптированы по этому файлу. Это проверка на данных разработки; для независимого сравнения нужен другой размеченный набор.')
            ordered = sorted(durations)
            result['timing'] = {'normalization_seconds': elapsed, 'worker_seconds': perf_counter()-started,
                                'rows_per_second': len(rows)/elapsed if elapsed else None,
                                'p50_row_ms': ordered[math.ceil(len(ordered)*.5)-1],
                                'p95_row_ms': ordered[math.ceil(len(ordered)*.95)-1],
                                'scope': 'Нормализация: только извлечение и преобразование каждой строки; без очереди, чтения Excel, таксономии, поиска и оценки эталона.'}
            result['runtime'] = {'python': platform.python_version(), 'system': platform.system(),
                                 'machine': platform.machine(), 'cpu_count': os.cpu_count(), 'device': 'CPU', 'llm_calls': 0}
            db.execute(update(NormalizationExperiment).where(
                NormalizationExperiment.id == ident, NormalizationExperiment.status == 'RUNNING'
            ).values(predictions=predictions, result=result, processed_rows=len(rows),
                     status='COMPLETED', finished_at=now(), heartbeat_at=now()))
            db.commit()
        except Exception as exc:
            db.rollback()
            db.execute(update(NormalizationExperiment).where(
                NormalizationExperiment.id == ident, NormalizationExperiment.status == 'RUNNING'
            ).values(status='FAILED', finished_at=now(), error=f'{type(exc).__name__}: {str(exc)[:500]}'))
            db.commit()
            logging.exception('Regex experiment failed: %s', ident)


def expire_abandoned(db):
    """Never mark partial work as a benchmark; a new request creates a new run."""
    cutoff = now()-timedelta(minutes=10)
    db.execute(update(NormalizationExperiment).where(
        NormalizationExperiment.status == 'RUNNING', NormalizationExperiment.heartbeat_at < cutoff
    ).values(status='FAILED', finished_at=now(), error='CPU-обработчик потерян. Создайте новый запуск; прежний сохранён в истории.'))
