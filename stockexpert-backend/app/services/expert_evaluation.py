"""Post-hoc evaluation of saved predictions. Never calls models or edits predictions."""
from collections import Counter, defaultdict
from math import ceil, log2
from sqlalchemy import select

from app.db import Document, ExpertDataset, ExpertEvaluation, Item, Job, Match, ProcurementSession, StageTiming, now
from app.services.execution import ACTIVE_STATUSES
from app.services.expert_reference import equal_value, text_key, name_counts, predicted_name

METRIC_VERSION = 'expert-metrics-v5'


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def hungarian_score(pairs):
    """Maximum one-to-one label assignment, including missing/noise as errors.

    Uses the rectangular Hungarian primal-dual algorithm; no ML dependencies.
    The denominator includes every expert-labelled item, even missing predictions.
    """
    predicted = sorted({p for p, _ in pairs if p})
    expected = sorted({e for _, e in pairs})
    if not predicted or not expected:
        return 0, {}
    counts = Counter((p, e) for p, e in pairs if p)
    # Gold categories are the rows. Padding only columns avoids a P×P matrix
    # when a poor model creates thousands of singleton clusters.
    rows, columns = len(expected), max(len(expected), len(predicted))
    u, v = [0] * (rows + 1), [0] * (columns + 1)
    match, way = [0] * (columns + 1), [0] * (columns + 1)
    for i in range(1, rows + 1):
        match[0], j0 = i, 0
        mins, used = [float('inf')] * (columns + 1), [False] * (columns + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = match[j0], float('inf'), 0
            for j in range(1, columns + 1):
                if used[j]:
                    continue
                weight = counts[(predicted[j-1], expected[i0-1])] if j <= len(predicted) else 0
                cost = -weight - u[i0] - v[j]
                if cost < mins[j]:
                    mins[j], way[j] = cost, j0
                if mins[j] < delta:
                    delta, j1 = mins[j], j
            for j in range(columns + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    mins[j] -= delta
            j0 = j1
            if not match[j0]:
                break
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
    mapping = {predicted[j-1]: expected[match[j]-1] for j in range(1, len(predicted)+1) if match[j]}
    return sum(counts[(p, e)] for p, e in mapping.items()), mapping


def compatible(db, session_id, payload):
    expected = {s['sha256'] for s in payload['sources'] if s['kind'] == 'residual'}
    actual = list(db.scalars(select(Document.sha256).where(Document.session_id == session_id, Document.kind == 'residual')))
    return bool(expected) and set(actual) == expected and len(actual) == len(expected)


def category_path(schema, passport):
    categories = {c['id']: c for c in schema.get('categories', [])}
    current = (passport or {}).get('category_id')
    result, seen = [], set()
    while current and current in categories and current not in seen:
        seen.add(current)
        node = categories[current]
        if current == 'normalization_review' or 'требует проверки' in text_key(node['name']):
            return []
        result.insert(0, current)
        current = node.get('parent_id')
    return result


def _prediction(passport, schema, reference):
    aliases = {text_key(alias): f['key'] for f in reference['features'] for alias in [f['key'], f['label'], *f['aliases']]}
    units = {f['key']: f['unit'] for f in reference['features']}
    attrs = passport.get('normalized_attributes', passport.get('attributes', {}))
    specs = passport.get('normalized_features', schema.get('features', []))
    by_key = {f['key']: f for f in specs}
    values = defaultdict(list)
    for key, value in attrs.items():
        if key == 'name' or value is None:
            continue
        feature = by_key.get(key, {})
        choices = {aliases[text_key(k)] for k in [key, feature.get('label', ''), *feature.get('aliases', [])] if text_key(k) in aliases}
        canonical = next(iter(choices)) if len(choices) == 1 else 'unmapped:' + key
        unit = feature.get('unit', 'none')
        target = units.get(canonical, unit)
        conversions = {('kV', 'V'): 1000, ('V', 'kV'): .001, ('m', 'mm'): 1000, ('mm', 'm'): .001, ('bar', 'MPa'): .1}
        if unit != target:
            factor = conversions.get((unit, target))
            if factor is not None and isinstance(value, (int, float)):
                value *= factor
            else:
                canonical = 'unit_mismatch:' + canonical
        values[canonical].append(value)
    return values


def _percentile(values, quantile):
    return sorted(values)[ceil(len(values) * quantile) - 1] if values else None


def calculate(db, session, reference, snapshot=None):
    jobs = list(db.scalars(select(Job).where(Job.session_id == session.id).order_by(Job.created_at, Job.id)))
    if snapshot:
        from types import SimpleNamespace
        session=SimpleNamespace(**snapshot['session'])
        jobs=[j for j in jobs if j.id in snapshot['job_ids']]
    if not snapshot and any(j.status in ACTIVE_STATUSES for j in jobs):
        return 'WAITING', {'note': 'Расчёт метрик начнётся после завершения обработки.'}
    completed = [j for j in jobs if j.status == 'COMPLETED' and j.kind in ('normalize', 'taxonomy', 'match', 'full')]
    if not completed:
        return 'WAITING', {'note': 'В сессии ещё нет завершённого расчёта.'}
    compatible_source=compatible(db, session.id, reference) if not snapshot else {d['sha256'] for d in snapshot['documents'] if d['kind']=='residual'}=={s['sha256'] for s in reference['sources'] if s['kind']=='residual'}
    if not compatible_source:
        return 'INCOMPATIBLE', {'note': 'Состав или SHA-256 Excel не соответствует эталону.'}
    documents = {d.id: d for d in db.scalars(select(Document).where(Document.session_id == session.id))}
    items = list(db.scalars(select(Item).where(Item.session_id == session.id)))
    if snapshot:
        documents={d['id']:SimpleNamespace(**d) for d in snapshot['documents']}
        items=[SimpleNamespace(**i) for i in snapshot['items']]
    by_location = {(documents[i.document_id].sha256, i.sheet, i.row_index, i.kind): i for i in items}
    stock = [p for p in reference['passports'] if p['kind'] == 'residual']
    text_only = reference.get('h1_mode') == 'name_exact'
    reference_locations = {(p['source_sha256'], p['sheet'], p['row'], p['kind']) for p in stock}
    extra_stock = [i.id for i in items if i.kind == 'residual' and i.passport and
                   (documents[i.document_id].sha256, i.sheet, i.row_index, i.kind) not in reference_locations]
    aligned, missing, changed, details = {}, [], [], []
    counts, per_field = Counter(), defaultdict(Counter)
    categories, l3_categories, l1_categories = [], [], []
    model_counts = Counter()
    h1_ready = bool(items) and any(i.passport for i in items)
    h2_ready = h1_ready and getattr(session,'taxonomy_enabled',True) and bool(session.schema_doc.get('categories'))
    for p in reference['passports']:
        item = by_location.get((p['source_sha256'], p['sheet'], p['row'], p['kind']))
        # Copied bases retain original coordinates; content is checked as well.
        if item and p['source_cells'] and item.raw != p['source_cells']:
            changed.append(p['id'])
            item = None
        if item and p['kind'] == 'requirement' and text_key(item.source_text) != text_key(p['source_name']):
            changed.append(p['id'])
            item = None
        if item:
            aligned[p['id']] = item
        if p['kind'] != 'residual':
            continue  # H1/H2 compare the Excel base; H3 uses the TXT requirement.
        passport = (item.passport or {}) if item else {}
        if not passport:
            missing.append(p['id'])
        else:
            counts['passports'] += 1
            prov = passport.get('provenance', {})
            model_counts[prov.get('model') or 'Не записана в старом паспорте'] += 1
            counts['reused'] += bool(passport.get('reuse'))
        if (name_counts(passport, p['name'])[0] if text_only else equal_value(predicted_name(passport), p['name'])):
            counts['names_correct'] += 1
        predicted = {} if text_only else _prediction(passport, session.schema_doc or {}, reference)
        row_errors = []
        if text_only:
            row_tp, row_fp, row_fn = name_counts(passport, p['name'])
            for metric, amount in [('tp', row_tp), ('fp', row_fp), ('fn', row_fn)]:
                counts[metric] += amount
                per_field['name'][metric] += amount
            if row_fp or row_fn:
                row_errors.append({'key': 'name', 'expected': p['name'], 'actual': predicted_name(passport), 'tp': row_tp, 'fp': row_fp, 'fn': row_fn})
        for key in set(p['attributes']) | set(predicted):
            gold = p['attributes'].get(key)
            if gold and not gold['reviewed']:
                continue
            expected = gold['value'] if gold else None
            actual = predicted.get(key, [])
            hit = expected is not None and any(equal_value(v, expected) for v in actual)
            tp, fp, fn = int(hit), len(actual) - int(hit), int(expected is not None and not hit)
            for metric, amount in [('tp', tp), ('fp', fp), ('fn', fn)]:
                counts[metric] += amount
                per_field[key][metric] += amount
            if fp or fn:
                row_errors.append({'key': key, 'expected': expected, 'actual': actual, 'tp': tp, 'fp': fp, 'fn': fn})
        expected_path = p['category_path']
        path = category_path(session.schema_doc or {}, passport) if expected_path else []
        if expected_path:
            categories.append((path[-1] if path else None, ' / '.join(expected_path)))
            l1_categories.append((path[0] if path else None, expected_path[0]))
            if len(expected_path) == 3:
                l3_categories.append((path[2] if len(path) >= 3 else None, ' / '.join(expected_path)))
                counts['l3_available'] += len(path) >= 3
        details.append({'id': p['id'], 'sheet': p['sheet'], 'row': p['row'], 'name': p['name'], 'source_text': p['source_name'], 'source_cells': p['source_cells'], 'expected_attributes': p['attributes'], 'actual_attributes': dict(predicted), 'actual_name': predicted_name(passport), 'item_id': item.id if item else None, 'missing': not bool(passport), 'errors': row_errors, 'expected_category': expected_path, 'actual_category_ids': path})
    tp, fp, fn = counts['tp'], counts['fp'], counts['fn']
    leaf_correct, label_mapping = hungarian_score(categories)
    for row in details:
        leaf = row['actual_category_ids'][-1] if row['actual_category_ids'] else None
        row['mapped_category'] = label_mapping.get(leaf)
        row['category_correct'] = (label_mapping.get(leaf) == ' / '.join(row['expected_category'])) if row['expected_category'] else None
    l3_correct, _ = hungarian_score(l3_categories)
    l1_correct, _ = hungarian_score(l1_categories)
    metrics = {
        'h1_precision': ratio(tp, tp + fp) if h1_ready else None,
        'h1_recall': ratio(tp, tp + fn) if h1_ready else None,
        'h1_f1': ratio(2 * tp, 2 * tp + fp + fn) if h1_ready else None,
        'name_accuracy': ratio(counts['names_correct'], len(stock)) if h1_ready else None,
        'h2_leaf_accuracy': ratio(leaf_correct, len(categories)) if h2_ready else None,
        'h2_l1_accuracy': ratio(l1_correct, len(l1_categories)) if h2_ready else None,
        # Missing L3 is an error; absence of L3 in the entire implementation is explicit.
        'h2_l3_accuracy': ratio(l3_correct, len(l3_categories)) if counts['l3_available'] else None,
        'h3_recall_retrieval': None, 'h3_precision_shortlist': None,
        'h3_mrr': None, 'h3_ndcg10': None,
        **{f'h3_recall_at_{k}': None for k in (1, 5, 10, 150)},
    }
    match_job = next((j for j in reversed(completed) if j.kind in ('match', 'full')), None)
    # Never mix an older match run with passports modified by a newer normalization.
    newer_predictions = bool(match_job and any(j.created_at > match_job.created_at and j.kind in ('normalize', 'taxonomy', 'full') for j in jobs))
    pairs = list(db.scalars(select(Match).where(Match.job_id == match_job.id))) if match_job and not newer_predictions else []
    if snapshot and 'matches' in snapshot and not newer_predictions:
        pairs = [SimpleNamespace(**m) for m in snapshot['matches']]
    by_pair = {(m.requirement_id, m.item_id): m for m in pairs}
    requirements = {p['requirement_id'] for p in reference['pairs']}
    req_documents_ok = {documents[i.document_id].sha256 for i in items if i.kind == 'requirement'} == {s['sha256'] for s in reference['sources'] if s['kind'] == 'requirement'}
    h3_ready = bool(match_job and not newer_predictions and req_documents_ok and all(r in aligned for r in requirements) and requirements)
    # Count the whole labelled catalog before inspecting predictions. A missed
    # position must never disappear from the denominator.
    h3 = Counter(relevant=sum(p['relevant'] is True for p in reference['pairs']),
                 unknown=sum(p['relevant'] is None for p in reference['pairs']),
                 pairs_total=len(reference['pairs']))
    query_scores, pair_details, query_details = [], [], []
    h3_reasons = []
    if not requirements:
        h3_reasons.append('В эталоне нет разметки пар для H3. Разметьте каждую строку базы для выбранной потребности.')
    if not match_job:
        h3_reasons.append('Нет завершённого матчинга. Запустите сопоставление с готовой базой; повторная нормализация базы не нужна.')
    if newer_predictions:
        h3_reasons.append('Паспорта или категории изменились после матчинга. Повторите только сопоставление.')
    if not req_documents_ok or any(r not in aligned for r in requirements):
        h3_reasons.append('Потребность в сессии не совпадает с потребностью эталона. Нужна разметка всех строк базы именно для этой потребности либо сессия с исходной потребностью эталона.')
    if h3['unknown']:
        h3_reasons.append(f"Не определена релевантность {h3['unknown']} пар. Завершите разметку, включая строки вне Top 150.")
    if not h3['relevant']:
        h3_reasons.append('В эталоне нет ни одной подходящей позиции: знаменатель Recall равен нулю.')
    if h3_ready:
        groups = defaultdict(list)
        for pair in reference['pairs']:
            groups[pair['requirement_id']].append(pair)
            req, item = aligned.get(pair['requirement_id']), aligned.get(pair['item_id'])
            prediction = by_pair.get((req.id, item.id)) if req and item else None
            result = prediction.result if prediction else {}
            rank = result.get('rank')
            pre_rank=result.get('pre_rerank_rank',rank)
            positive = pair['relevant'] is True
            h3['top150'] += isinstance(pre_rank,int) and not isinstance(pre_rank,bool) and 1<=pre_rank<=150
            if positive:
                h3['retrieved_positive'] += bool(result.get('retrieved'))
                for k in (1, 5, 10, 150):
                    h3[f'hits_{k}'] += isinstance(pre_rank if k==150 else rank,int) and 1<=(pre_rank if k==150 else rank)<=k
            if result.get('is_shortlisted'):
                h3['shortlisted'] += 1
                h3['shortlist_known'] += pair['relevant'] is not None
                h3['shortlist_positive'] += positive
            pair_details.append({**pair, 'rank': rank, 'pre_rank':pre_rank, 'retrieved': bool(result.get('retrieved')), 'shortlisted': bool(result.get('is_shortlisted')), 'match_id': prediction.id if prediction else None})
        for req, gold_pairs in groups.items():
            relevant_ids = {p['item_id'] for p in gold_pairs if p['relevant'] is True}
            predictions = [p for p in pair_details if p['requirement_id'] == req]
            top150 = [p for p in predictions if isinstance(p['pre_rank'],int) and 1<=p['pre_rank']<=150]
            hits = sum(p['relevant'] is True for p in top150)
            unknown = sum(p['relevant'] is None for p in gold_pairs)
            query_details.append({'requirement_id': req, 'catalog_rows': len(stock),
                                  'pairs_reviewed': len(gold_pairs) - unknown, 'unknown': unknown,
                                  'relevant': len(relevant_ids), 'hits_150': hits,
                                  'missed_relevant': len(relevant_ids) - hits,
                                  'top150': len(top150), 'retained_share': ratio(len(top150), len(stock)),
                                  'recall_at_150': ratio(hits, len(relevant_ids)) if not unknown else None})
            if not relevant_ids:
                continue
            ranks = [p['rank'] for p in pair_details if p['requirement_id'] == req and p['item_id'] in relevant_ids and isinstance(p['rank'], int) and p['rank'] > 0]
            dcg = sum(1 / log2(r + 1) for r in ranks if r <= 10)
            idcg = sum(1 / log2(r + 1) for r in range(1, min(10, len(relevant_ids)) + 1))
            query_scores.append((1 / min(ranks) if ranks else 0, dcg / idcg))
        if not h3['unknown']:
            metrics.update({f'h3_recall_at_{k}': ratio(h3[f'hits_{k}'], h3['relevant']) for k in (1, 5, 10, 150)})
            metrics['h3_recall_retrieval'] = ratio(h3['retrieved_positive'], h3['relevant'])
        metrics['h3_precision_shortlist'] = ratio(h3['shortlist_positive'], h3['shortlist_known'])
        # Unknown judgements cannot be treated as negatives in ranking metrics.
        if not h3['unknown']:
            metrics['h3_mrr'] = ratio(sum(s[0] for s in query_scores), len(query_scores))
            metrics['h3_ndcg10'] = ratio(sum(s[1] for s in query_scores), len(query_scores))
    recalls=[q['recall_at_150'] for q in query_details if q['recall_at_150'] is not None]
    metrics['h3_recall_micro_at_150']=metrics['h3_recall_at_150']
    metrics['h3_recall_at_150']=sum(recalls)/len(recalls) if recalls and not h3['unknown'] else None
    stage='before_cross_encoder' if pairs and all('pre_rerank_rank' in m.result for m in pairs) else 'legacy_after_cross_encoder'
    timings = list(db.scalars(select(StageTiming).where(StageTiming.session_id == session.id,StageTiming.job_id.in_([j.id for j in jobs]))))
    stage_seconds = defaultdict(float)
    for t in timings:
        if t.elapsed_seconds is not None:
            stage_seconds[t.stage] += t.elapsed_seconds
    # Latencies are query-level measurements, never a percentile of pipeline stages.
    query_times = (match_job.params.get('query_timings_ms') or []) if h3_ready else []
    notes = [
        ('H1: одна строка — одно сравнение текста наименования. Удаляются только пробелы по краям; регистр, внутренние пробелы и знаки сохраняются. Категории и технические параметры исключены. Ошибка текста даёт FP и FN; отсутствие текста — FN. Этот показатель не равен прежнему micro-F1 технических параметров.' if text_only else 'H1: micro по техническим параметрам; наименование оценивается отдельно. Ошибочное значение даёт FP и FN. Неизвестный ключ модели считается FP.'),
        'H2 текущего запуска: взаимно-однозначное сопоставление категорий по размеченной выборке (описательная оценка). Для закрытого теста используйте лабораторный эксперимент: соответствие категорий фиксируется на обучении. Шум и пропуски — ошибки.',
        'Время этапов — все сохранённые попытки сессии; повторные запуски и повторное использование паспортов влияют на сравнение скорости.',
    ]
    if not counts['l3_available']:
        notes.append('В результатах модели нет третьего уровня дерева. Accuracy L3 не измерена; доступна вспомогательная Accuracy конечных категорий.')
    if len(stock) <= 150:
        notes.append('В базе меньше 150 позиций. Recall@150 не подтверждает качество отбора на 12 000 строках; сравнивайте также Recall@1/5/10.')
    if missing or changed:
        notes.append('Неполное покрытие: отсутствующие паспорта учитываются как пропуски. Сравнение скорости неполной базы с полной некорректно.')
    if extra_stock:
        notes.append('В сессии есть дополнительные нормализованные строки вне эталона (например, строка 1). Они не размечены и не включены в метрики качества; время их обработки входит во время сессии.')
    if query_times and len(query_times) < 20:
        notes.append('Мало запросов для устойчивой оценки p95: число замеров указано отдельно. Время одного запроса не подтверждает серверный SLA.')
    if not query_times:
        notes.append('В старом запуске нет измерений времени отдельных запросов: p50/p95 недоступны. Повторный запуск модели для метрик качества не требуется.')
    if newer_predictions:
        notes.append('После подбора изменялись паспорта/таксономия. H3 для устаревшего подбора не вычисляется.')
    notes.extend(h3_reasons)
    h2_reasons = []
    if not categories:h2_reasons.append('H2 не входит в этот эталон: категории не размечены.')
    if not getattr(session,'taxonomy_enabled',True):h2_reasons.append('Этап H2 отключён пользователем; метрика неприменима.')
    if len(categories)!=len(stock):h2_reasons.append(f'Конечные категории размечены для {len(categories)} из {len(stock)} строк.')
    if not h2_ready and getattr(session,'taxonomy_enabled',True):h2_reasons.append('Нет готовой таксономии.')
    full_h3_reasons = list(h3_reasons)
    if extra_stock:
        full_h3_reasons.append(f'Ещё {len(extra_stock)} нормализованных строк сессии отсутствуют в эталоне. Уточните состав базы и разметьте все позиции для H3.')
    hypotheses = [
        {'id': 'H1', 'label': 'F1 совпадения текста по строкам' if text_only else 'F1 извлечения параметров', 'target': .94, 'value': metrics['h1_f1'],
         'formula': '2 × TP / (2 × TP + FP + FN)', 'numerator': 2 * tp, 'denominator': 2 * tp + fp + fn,
         'substitution': f'2 × {tp} / (2 × {tp} + {fp} + {fn})',
         'scope': (f'{len(stock)} описаний МТР; по одному тексту на строку. Верно: {tp}; распознано: {tp + fp}; эталон: {tp + fn}. Precision={tp}/{tp + fp}; Recall={tp}/{len(stock)}. H2 и параметры исключены.' if text_only else f'{len(stock)} строк эталона; TP={tp}, FP={fp}, FN={fn}. Micro: сначала суммируются значения всех параметров.'),
         'reason': '' if metrics['h1_f1'] is not None else 'Нет проверенных значений для расчёта F1.'},
        {'id': 'H2', 'label': 'Диагностика категорий сохранённого запуска', 'target': .85, 'diagnostic': True,
         'value': metrics['h2_leaf_accuracy'] if not h2_reasons else None,
         'formula': 'M / N', 'numerator': leaf_correct if not h2_reasons else None, 'denominator': len(stock),
         'substitution': f'{leaf_correct} / {len(stock)}' if not h2_reasons else None,
         'scope': f'N={len(stock)} строк, включая шум и пропуски; M — число верных назначений конечной категории после взаимно-однозначного сопоставления категорий.',
         'reason': ' '.join(h2_reasons)+' Венгерское сопоставление выполнено на оцениваемых данных. Это дополнительная диагностика, не закрытый тест H2. Для проверки по презентации запустите лабораторное сравнение H2.'},
        {'id': 'H3', 'label': 'Recall@150 по всей базе', 'target': .985,
         'value': metrics['h3_recall_at_150'] if not full_h3_reasons else None,
         'formula': 'Среднее (найденные подходящие / все подходящие) по запросам',
         'numerator':h3['hits_150'] if len(recalls)==1 else None,'denominator':h3['relevant'] if len(recalls)==1 else len(recalls),
         'substitution': (f"{h3['hits_150']} / {h3['relevant']}" if len(recalls)==1 else '('+' + '.join(f"{q['hits_150']}/{q['relevant']}" for q in query_details if q['relevant'])+f') / {len(recalls)}') if recalls and len(recalls)<=10 and not full_h3_reasons else None,
         'scope': f"Все {len(stock)} строк эталона × {len(requirements)} потребностей = {len(reference['pairs'])} пар. Размечено {len(reference['pairs']) - h3['unknown']}; подходящих во всей базе: {h3['relevant']}.",
         'reason': ' '.join(full_h3_reasons)+(' Старый запуск: cutoff после Cross-Encoder; для методики презентации повторите подбор.' if stage=='legacy_after_cross_encoder' else '')},
    ]
    applicable = [h for h in hypotheses if h['id'] == 'H1' or (h['id'] == 'H2' and categories) or (h['id'] == 'H3' and requirements)]
    return ('READY' if all(h['value'] is not None or (h['id']=='H2' and not getattr(session,'taxonomy_enabled',True)) for h in applicable) else 'PARTIAL'), {
        'metric_version': METRIC_VERSION, 'h1_mode':reference.get('h1_mode', 'attributes'),'recall_stage':stage,'taxonomy_enabled':getattr(session,'taxonomy_enabled',True), 'metrics': metrics, 'hypotheses': hypotheses,
        'h3_population': {'catalog_rows': len(stock), 'extra_rows': len(extra_stock),
                          'requirements': len(requirements), 'pairs_total': len(reference['pairs']),
                          'pairs_reviewed': len(reference['pairs']) - h3['unknown'], 'unknown': h3['unknown'],
                          'relevant': h3['relevant'], 'hits_150': h3['hits_150'] if h3_ready else None,
                          'top150': h3['top150'] if h3_ready else None,
                          'retained_share': ratio(h3['top150'], len(reference['pairs'])) if h3_ready else None,
                          'reduction_rate': 1 - ratio(h3['top150'], len(reference['pairs'])) if h3_ready else None},
        'h3_queries': query_details,
        'coverage': {'expected_stock': len(stock), 'normalized_stock': counts['passports'], 'extra_stock_ids': extra_stock, 'missing_ids': missing, 'changed_ids': changed, 'tp': tp, 'fp': fp, 'fn': fn, 'categories': len(categories), 'l3_available': counts['l3_available'], 'reused_passports': counts['reused'], 'requirements': len(requirements), **dict(h3)},
        'models': [{'model': model, 'passports': number} for model, number in sorted(model_counts.items())],
        'selected_model': session.llm_config or {},
        'normalization_method': session.normalization_method,
        'source_jobs': [{'id': j.id, 'kind': j.kind, 'status': j.status, 'runtime': j.params.get('runtime', {}), 'llm_config': j.params.get('llm_config', {})} for j in jobs],
        'match_job_id': match_job.id if match_job else None,
        'taxonomy_job_id': next((j.id for j in reversed(completed) if j.kind in ('normalize', 'taxonomy', 'full')), None) if h2_ready else None,
        'timing': {'session_processing_seconds': sum(stage_seconds.values()) if timings else None, 'normalization_seconds': stage_seconds.get('PASSPORT_NORMALIZATION', stage_seconds.get('EXTRACTION_CLASSIFICATION')), 'taxonomy_seconds': stage_seconds.get('TAXONOMY_BUILD'), 'matching_seconds': sum(stage_seconds.get(s, 0) for s in ('VECTORIZATION','HYBRID_SEARCH','HARD_RULES','CROSS_ENCODER','PERSIST_MATCHES','EXPLANATIONS')) if match_job and timings else None, 'p50_ms': _percentile(query_times, .5), 'p95_ms': _percentile(query_times, .95), 'query_samples': len(query_times)},
        'fields': [{'key': k, **dict(c), 'f1': ratio(2*c['tp'], 2*c['tp']+c['fp']+c['fn'])} for k, c in sorted(per_field.items())],
        'label_mapping': label_mapping, 'rows': details, 'pairs': pair_details, 'notes': notes,
        'targets': {'h1_f1': .94, 'h2_leaf_accuracy': .85, 'h3_recall_at_150': .985},
    }


def refresh_evaluation(db, evaluation, job_id=None):
    reference = db.get(ExpertDataset, evaluation.dataset_id)
    session = db.get(ProcurementSession, evaluation.session_id)
    from app.services.run_history import get_snapshot
    from app.services.quality import latest_job
    from app.db import MetricRevision
    job=db.get(Job,job_id) if job_id else latest_job(db,session.id)
    snapshot=get_snapshot(db,session,job) if job else None
    if job_id and (not job or job.session_id!=session.id or not snapshot):raise ValueError('Нет снимка выбранного запуска')
    status, result = calculate(db, session, reference.payload,snapshot)
    if job:
        result={**result,'job_id':job.id,'source':'automatic','dataset_id':reference.id,'dataset_version':reference.version}
        db.add(MetricRevision(session_id=session.id,job_id=job.id,source='automatic',dataset_id=reference.id,action='recalculate',result=result,inputs={'reference_checksum':reference.checksum,'reference_author':reference.payload.get('author'),'reference_notes':reference.payload.get('notes',[])}))
    evaluation.status = status
    evaluation.result = result
    evaluation.error = None
    evaluation.updated_at = now()
    db.flush()
    return evaluation


def evaluate_subscriptions(db, session_id):
    """An evaluation failure must not turn a successful inference job into FAILED."""
    for evaluation in db.scalars(select(ExpertEvaluation).where(ExpertEvaluation.session_id == session_id)):
        if evaluation.status=='RESET':continue
        try:
            with db.begin_nested():
                refresh_evaluation(db, evaluation)
        except Exception:
            evaluation.status = 'ERROR'
            evaluation.error = 'Не удалось рассчитать метрики. Повторите расчёт в разделе для разработчиков; результаты моделей сохранены.'
    db.commit()
