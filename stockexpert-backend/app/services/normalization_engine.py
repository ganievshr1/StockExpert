"""H1 dispatch and immutable job snapshots, independent of the H2 model."""
from copy import deepcopy
import hashlib
from pathlib import Path
from sqlalchemy import select
from app.db import Item, Job
from app.services import regex_normalization as rules

ADAPTER_VERSION = 'pipeline-h1-v1'
ADAPTER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def config(method):
    if method == 'llm':
        return {'method': 'llm'}
    if method != 'regex':
        raise ValueError('Неизвестный метод H1')
    return {'method': 'regex', 'rules_version': rules.VERSION,
            'rules_sha256': rules.RULES_SHA256, 'device': 'CPU',
            'adapter_version': ADAPTER_VERSION, 'adapter_sha256': ADAPTER_SHA256}


def pin_job(job):
    # An old queued job was created before method selection existed. It stays LLM.
    saved = job.params.get('normalization_config', {'method': 'llm'})
    if saved.get('method') not in ('llm', 'regex'):
        raise ValueError('Некорректный снимок метода H1')
    job.params = {**job.params, 'normalization_config': deepcopy(saved)}
    return saved


def is_regex(job):
    return job.params.get('normalization_config', {}).get('method') == 'regex'


def check_rules(job):
    if is_regex(job) and job.params['normalization_config'] != config('regex'):
        raise ValueError('Правила H1 изменились после создания задания. Для нового эксперимента создайте новую сессию; сохранённые паспорта не перезаписываются.')


def needs_llm(kind, method, taxonomy_enabled=True):
    return kind != 'match' and (method == 'llm' or (taxonomy_enabled and kind in ('full', 'taxonomy')))


def selection_locked(db, session):
    if db.scalar(select(Job.id).where(Job.session_id == session.id, Job.kind != 'prepare').limit(1)):
        return True
    query = select(Item.id).where(Item.session_id == session.id, Item.passport.is_not(None))
    if session.base_snapshot.get('reused_passports'):
        query = query.where(Item.kind == 'requirement')
    return bool(db.scalar(query.limit(1)))


def column_mapping(sample):
    rows = sample['first_rows']
    if not rows:
        raise ValueError('Пустой лист: выберите файл с данными.')
    from app.services.regex_excel import HEADERS
    first = rows[0]
    cells = first['cells']
    if tuple(rules.clean(cells.get(str(i), '')) for i in range(3)) == HEADERS:
        return {'header_row': first['row_index'], 'name_columns': ['2'], 'category_column': None}
    if cells.get('0') == 'Предприятие' and cells.get('1') == HEADERS[1] and str(cells.get('2', '')).startswith('Система '):
        return {'header_row': 0, 'name_columns': ['2'], 'category_column': None}
    labels = {'наименование', 'описание', 'наименование мтр', 'технические характеристики', 'характеристики', 'система'}
    selected = [key for key, value in cells.items() if rules.clean(value).casefold() in labels]
    if selected:
        return {'header_row': first['row_index'], 'name_columns': selected[:10], 'category_column': None}
    columns = sorted({k for row in rows for k in row['cells']}, key=lambda k: int(k))
    if len(columns) > 10:
        raise ValueError('Регулярки не определили столбцы: используйте XLSX с заголовком «Описание»/«Наименование» или складской формат A–C.')
    # No ungrounded header inference: all rows retained for the mandatory review.
    return {'header_row': 0, 'name_columns': columns, 'category_column': None}


def split_requirement(text):
    # A questionnaire often has many parameter lines for one product. Do not
    # silently turn each line into a separate procurement position.
    issues = []
    if len([line for line in text.splitlines() if line.strip()]) > 1:
        issues.append({'code': 'REGEX_REQUIREMENT_SINGLE_POSITION',
                       'message': 'Опросный лист сохранён одной позицией. Проверьте, что все строки относятся к одному изделию.'})
    return [text], issues


def extract(text, source_only=False):
    passport, issues = rules.extract(text)
    passport['provenance'].update(pipeline_adapter_version=ADAPTER_VERSION, pipeline_adapter_sha256=ADAPTER_SHA256)
    if source_only:
        for key in ('attributes', 'normalized_attributes'):
            passport[key] = {'name': passport['name']}
        for key in ('evidence', 'normalized_evidence'):
            passport[key] = {'name': passport[key]['name']}
        passport['normalized_features'] = []
        passport['normalization']['requires_review'] = True
    # Dimensions are a string in source order, not a scalar with a unit. The
    # Russian label retains "мм"; evidence retains the actual conversion.
    for feature in passport['normalized_features']:
        if feature['kind'] == 'string':
            feature['unit'] = 'none'
    blocking = {'NO_RECOGNIZED_PROPERTIES', 'CONFLICTING_VALUES', 'NEGATED_PROPERTY', 'INVALID_RANGE'}
    passport['normalization']['blocks_classification'] = source_only or any(i['code'] in blocking for i in issues)
    return passport, issues
