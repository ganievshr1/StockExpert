"""H1 ablations. No expert answers enter extraction."""
from copy import deepcopy
from app.services import llm, passports, normalization_engine
from app.services.contracts import OpenExtraction


def extract(method, text):
    if method == 'h1_llm_raw':
        raw = llm.structured(passports.PROMPT, {'text': text}, OpenExtraction.model_json_schema())
        parsed = OpenExtraction.model_validate(raw)
        # The ablation retains original values/units without evidence repair.
        # Invalid schema is an explicit method error, never a successful empty row.
        values = {v.key: v.value for v in parsed.values}
        if len(values) != len(parsed.values) or 'name' in values:
            raise ValueError('Повторяющиеся или зарезервированные ключи LLM')
        return {'name': parsed.name, 'attributes': values, 'normalized_attributes': values,
                'normalized_features': [{'key': v.key, 'label': v.label, 'unit': v.unit, 'kind': v.kind} for v in parsed.values],
                'evidence': {v.key: {'quote': v.evidence} for v in parsed.values}}
    passport, _ = passports.extract(text)
    if method != 'h1_hybrid':
        return passport
    rules, _ = normalization_engine.extract(text)
    passport = deepcopy(passport)
    features = {f['key']: f for f in passport.get('normalized_features', [])}
    for feature in rules.get('normalized_features', []):
        key = feature['key']
        value = rules.get('normalized_attributes', {}).get(key)
        evidence = rules.get('normalized_evidence', {}).get(key, {})
        quote = evidence.get('quote')
        if value is None or not quote or quote not in text:
            continue
        # Rules only fill missing properties. Conflicts stay visible for review.
        if key in features and features[key].get('unit') != feature.get('unit'):
            continue
        if passport.get('normalized_attributes', {}).get(key) is None:
            for field in ('attributes', 'normalized_attributes'):
                passport.setdefault(field, {})[key] = value
            for field in ('evidence', 'normalized_evidence'):
                passport.setdefault(field, {})[key] = evidence
            features[key] = feature
    passport['normalized_features'] = list(features.values())
    return passport


def paired_interval(current, baseline, rows, seed):
    """Paired bootstrap of micro-F1; duplicate descriptions are sampled together."""
    import numpy as np
    from collections import defaultdict
    from app.services.expert_reference import text_key
    common = set(current) & set(baseline)
    groups = defaultdict(list)
    for row in rows:
        if row['id'] in common:
            groups[text_key(row['source_text'])].append(row['id'])
    groups = list(groups.values())
    def score(values, ids):
        tp, fp, fn = (sum(values[i][key] for i in ids) for key in ('tp', 'fp', 'fn'))
        return 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    if not common:
        return None, None
    a, b = score(current, common), score(baseline, common)
    delta = a - b if a is not None and b is not None else None
    if len(groups) < 2:
        return delta, None
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(1000):
        ids = [i for index in rng.integers(0, len(groups), len(groups)) for i in groups[index]]
        a, b = score(current, ids), score(baseline, ids)
        if a is not None and b is not None:
            samples.append(a - b)
    return delta, [float(np.quantile(samples, .025)), float(np.quantile(samples, .975))] if samples else None
