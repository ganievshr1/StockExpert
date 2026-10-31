"""Frozen, deterministic MTR baseline: regex + dictionaries, no model or gold data.

Unknown/contradictory values are retained as review issues, never guessed.
Every extracted value carries the original span, rule and conversion provenance.
"""
import hashlib
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

VERSION = 'regex-mtr-v2.0.0'
DEVELOPMENT_SOURCE_SHA256 = 'eefe7883a334b0587a31fb6a8c0b2c2c13bad269f42d8e0911358f4ffdb244c0'
RULES_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
NUM = r'\d+(?:[.,]\d+)?'
FLAGS = re.IGNORECASE
TYPE_RULES = [
    ('cable', r'\bкабел\w*|\bкаб\.(?=\s)|\b(?:ВВГ|КГ|КВВГ|АВВГ)(?=[\s\d-])'),
    ('pipe', r'\bтруб[аы]\b'),
    ('motor', r'\bэлектродвигател\w*|\bэд\.?\s*в\.?|\bэл[.\s-]*дв(?:иг)?\.?'),
    ('valve', r'\bкран\s+шар\w*|\bзадвижк\w*|\bклапан\w*'),
    ('crane', r'\bкран\s+(?:эл\.|мостов\w*|подъ[её]мн\w*)'),
    ('cabinet', r'\bшкаф\w*|\bщит\b'),
]
FEATURES = {
    'designation': ('Марка / модель / обозначение', 'string', 'none'),
    'manufacturer': ('Производитель', 'string', 'none'),
    'core_count': ('Количество жил основной группы', 'integer', 'pcs'),
    'core_cross_section_mm2': ('Сечение жилы основной группы', 'number', 'mm2'),
    'additional_core_count': ('Количество жил дополнительной группы', 'integer', 'pcs'),
    'additional_core_cross_section_mm2': ('Сечение дополнительной жилы', 'number', 'mm2'),
    'voltage_v': ('Напряжение', 'number', 'V'),
    'current_a': ('Ток', 'number', 'A'),
    'power_kw': ('Мощность', 'number', 'kW'),
    'frequency_hz': ('Частота', 'number', 'Hz'),
    'rotation_speed_rpm': ('Частота вращения', 'number', 'rpm'),
    'pressure_mpa': ('Давление', 'number', 'MPa'),
    'nominal_diameter_mm': ('Номинальный диаметр DN', 'number', 'mm'),
    'outside_diameter_mm': ('Наружный диаметр', 'number', 'mm'),
    'wall_thickness_mm': ('Толщина стенки', 'number', 'mm'),
    'length_mm': ('Длина', 'number', 'mm'),
    'width_mm': ('Ширина', 'number', 'mm'),
    'thickness_mm': ('Толщина', 'number', 'mm'),
    'mass_kg': ('Масса', 'number', 'kg'),
    'protection_rating': ('Степень защиты IP', 'string', 'none'),
    'climate': ('Климатическое исполнение', 'string', 'none'),
    'material_grade': ('Марка материала', 'string', 'none'),
    'thread': ('Обозначение резьбы', 'string', 'none'),
    'connection': ('Присоединение', 'string', 'none'),
    'accessories': ('Комплектация', 'string', 'none'),
    'dimensions_text': ('Размеры в порядке записи; оси и единица не заданы', 'string', 'none'),
    'dimensions_mm': ('Размеры в порядке записи, мм; оси не назначены', 'string', 'mm'),
    'pipe_thread': ('Трубная резьба', 'string', 'none'),
    'thread_tolerance': ('Поле допуска резьбы', 'string', 'none'),
    'supply_code': ('Маркировка питания', 'string', 'none'),
    'drive': ('Привод', 'string', 'none'),
    'cylinder_capacity': ('Вместимость склада баллонов', 'integer', 'pcs'),
    'gas': ('Газ в наименовании склада', 'string', 'none'),
    'reinforcement_mark': ('Маркировка арматуры', 'string', 'none'),
    'f_mark': ('Маркировка F', 'string', 'none'),
    'component_designation': ('Обозначение компонента', 'string', 'none'),
    'variant_code': ('Дополнительный код исполнения', 'string', 'none'),
    'reference_code': ('Дополнительное обозначение', 'string', 'none'),
}

# These patterns identify a product family and copy its designation. Numeric
# segments of an opaque model are NOT assigned engineering meanings or units.
# Boundaries isolate explicitly written parameters from the model itself.
FAMILY_RULES = [
    ('cable', r'Кабел[ья]\s+(?:(?:силов\w*|контрольн\w*)\s+)?(?P<model>(?:К1-)?(?:КП[\w()/-]*|КГ[\w()/-]*|А?ВВГ[\w()/-]*|КВВГ[\w()/-]*|ПВС|ШВВП))', False),
    ('pressure_sensor', r'Датчик\s+давления\s+(?P<model>ДД-\d+)(?=-|\s|$)', False),
    ('hose_test_unit', r'Установка\s+для\s+гидр\.?\s*испыт\.?\s*пож\.?\s*рукавов\s+(?P<model>УГИР-\d+)(?=-|\s|$)', False),
    ('rescue_tool', r'Инструмент\s+аварийно[- ]спасательный\s+(?P<model>Медведь-\d+)(?=-|\s|$)', False),
    ('fire_cabinet', r'Шкаф\s+пожарный\s+(?P<model>ШПК-\d+-[НВ]-[ОЗ])(?=-|\s|$)', False),
    ('radio', r'Радиостанция\s+(?P<model>РС-\d+)(?=-|\s|$)', False),
    ('radio_accessory', r'Устройство\s+(?P<model>InfiNet\s+AUX-ODU-LPU-[A-Z]\d+)(?=-|\s|$)', False),
    ('tank', r'[ЕЁ]мкость\s+(?P<model>\d[\w.,/()-]*)', True),
    ('mast', r'Мачта\s+прожект\.?\s+с\s+молниеотводом\s+(?P<model>ПМС-[\w.,/()-]+)', True),
    ('heat_exchanger', r'Теплообменник\s+(?P<model>GXD-[\w.,/()-]+)', True),
    ('well_filter', r'Фильтр\s+(?P<model>ФЩС-[\w.,/()-]+)', True),
    ('coded_valve', r'(?P<model>ЗК\d+[\w.,/*()-]+)', True),
    ('motor', r'(?:Эл[. ]*дв\.?|Эд\.?\s*в\.?|Электродвигатель)\s+(?P<model>ПВЭДН[\w.,/()-]+)', True),
    ('ex_system', r'Система\s+(?P<model>[ЕE][хxX]СО-[\w.,/()-]+)', True),
    ('flow_control_system', r'(?P<model>СУДРВ\s+\d[\w.,/()-]+)', True),
    ('pig_receiver', r'Устройство\s+при[её]ма\s+(?P<model>О-Л-УПП-[\w.,/()-]+)', True),
    ('compressor', r'Компрессор\s+воздушный\s+(?P<model>БОРЕЙ\s+\d[\d.,/]*\s+ПЭ)(?=-|\s|$)', True),
    ('substation', r'Подстанция\s+(?P<model>\d?КТП\(М\)-[\d.,/]+)', True),
    ('substation_container', r'Блок-бокс\s+трансф\.?\s*подст\.?(?P<model>\d?КТП-[\d.,/]+)', True),
    ('electrical_system', r'(?P<model>СЭО-ГК-Ex-[\w.,()-]+)', True),
    ('thermocolumn', r'Термоколонка\s+(?P<model>ТКС\.[\w.,/()-]+)', True),
    ('screw_pump_unit', r'Установка\s+(?P<model>УЭВНМТ-[\w.,/()-]+)', True),
    ('transformer', r'(?:Тр-р|Трансформатор)\s+(?P<model>ТМПНГ-[\w.,/()-]+)', True),
    ('reagent_block', r'Блок\s+реагента\s+(?P<model>БДПР-\d+\s+\d+\s+\d+/\d+/\d+)', True),
    ('thermostabilizer', r'Термостабилизатор\s+(?P<model>ТК[\w.,/()-]+)', True),
    ('stabilizer', r'Стабилизатор\s+(?P<model>ССГ-[\w.,/()-]+)', True),
    ('concrete_slab', r'Плита\s+(?P<model>ПДН)(?=[,\s]|$)', False),
    ('stud', r'Шпилька\s+(?P<model>А?[МM]\d+-[\w.,хx×-]+)', True),
    ('pump_unit', r'Агрегат\s+(?P<model>АЭНЦСН\s+\d+-\d+)', True),
    ('gate_valve', r'Задвижка\s+(?P<model>\d+[а-я]+\d+[а-я]+)(?=\s|$)', False),
]
FAMILY_RULES = [(name, re.compile(r'(?<!\w)' + pattern, FLAGS), opaque)
                for name, pattern, opaque in FAMILY_RULES]
UNIT_RULES = [
    ('voltage_v', r'кВ|kV|В|V', {'кв': 1000, 'kv': 1000}),
    ('current_a', r'мА|mA|А|A', {'ма': '.001', 'ma': '.001'}),
    ('power_kw', r'кВт|kW|Вт|W', {'вт': '.001', 'w': '.001'}),
    ('frequency_hz', r'МГц|MHz|кГц|kHz|Гц|Hz', {'мгц': 1000000, 'mhz': 1000000, 'кгц': 1000, 'khz': 1000}),
    ('rotation_speed_rpm', r'об\s*/\s*мин|rpm', {}),
    ('pressure_mpa', r'МПа|MPa|кПа|kPa|бар|bar', {'кпа': '.001', 'kpa': '.001', 'бар': '.1', 'bar': '.1'}),
    ('mass_kg', r'кг|kg', {}),
]
for _key, _units, _conversion in UNIT_RULES:
    for _bound, _label in [('min', 'Нижняя граница: '), ('max', 'Верхняя граница: ')]:
        FEATURES[_key+'_'+_bound] = (_label+FEATURES[_key][0].lower(), 'number', FEATURES[_key][2])


def clean(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def extract(source):
    if not isinstance(source, str) or len(source) > 16000:
        raise ValueError('Исходная строка должна быть текстом не длиннее 16000 символов')
    candidates, issues = {}, []
    types = [name for name, pattern in TYPE_RULES if re.search(pattern, source, FLAGS)]
    family_matches = [(name, match, opaque) for name, pattern, opaque in FAMILY_RULES
                      for match in pattern.finditer(source)]
    component_matches = list(re.finditer(r'(?<!\w)[1-9]ВАО-\d+[A-Z]{2}-\d+(?!\w)', source, FLAGS))
    model_spans = [m.span('model') for _, m, _ in family_matches]
    opaque_spans = [m.span('model') for _, m, opaque in family_matches if opaque] + [m.span() for m in component_matches]
    family_names = sorted({name for name, _, _ in family_matches})
    if re.search(r'\bСклад\s+баллонов\b', source, FLAGS):
        family_names.append('gas_cylinder_store')
    types = list(dict.fromkeys(types + family_names))
    structural_spans = list(model_spans)

    def inside(start, end, spans):
        return any(a <= start and end <= b for a, b in spans)

    def add(key, match, value=None, *, group=0, rule, source_unit='none', factor=1, scalar_guard=True):
        quote = match.group(0)
        before = source[max(0, match.start()-24):match.start()]
        if re.search(r'(?:\bне|\bбез|\bкроме)\s*$', before, FLAGS):
            issues.append({'code': 'NEGATED_PROPERTY', 'key': key, 'quote': before + quote, 'rule': rule})
            return
        # Numeric ranges and alternatives are not scalar values.
        after = source[match.end():match.end()+12]
        preceding_number = re.search(r'\d+(?:[.,]\d+)?\s*(?:[-–—/]\s*|\.\.\s*)$', source[:match.start()])
        prior_is_structure = preceding_number and inside(preceding_number.start(), preceding_number.start()+len(preceding_number.group().rstrip('-–—/ .')), structural_spans)
        if FEATURES[key][1] != 'string' and scalar_guard and (
            (preceding_number and not prior_is_structure)
            or re.search(r'(?:\bдо|\bот|[<>≤≥])\s*$', before, FLAGS)
        ):
            issues.append({'code': 'RANGE_NEEDS_REVIEW', 'key': key, 'quote': before + quote + after, 'rule': rule})
            return
        raw = match.group(group) if value is None else value
        kind, target = FEATURES[key][1:]
        if kind != 'string':
            number = Decimal(str(raw).replace(',', '.')) * Decimal(str(factor))
            value = int(number) if kind == 'integer' else float(number)
        else:
            value = clean(str(raw))
        evidence = {'quote': quote, 'start': match.start(), 'end': match.end(),
                    'raw_value': str(raw), 'source_unit': source_unit, 'target_unit': target,
                    'method': 'regex', 'rule': rule, 'factor': factor}
        candidates.setdefault(key, []).append((value, evidence))

    for family, match, opaque in family_matches:
        add('designation', match, group='model', rule='family-designation:'+family)
        if opaque:
            issues.append({'code': 'OPAQUE_MODEL_SEGMENTS', 'quote': match['model'],
                           'message': 'Числа внутри марки сохранены как обозначение. Единицы и смысл кодов не назначены.'})
    for match in component_matches:
        add('component_designation', match, rule='motor-component-mark')
    if 'cable' in types:
        structural_spans += [m.span() for m in re.finditer(r'(?<![\w.,])\d{1,3}\s*[xх×*]\s*'+NUM, source, FLAGS)]
    for key, units, conversion in UNIT_RULES:
        range_spans = []
        pattern = r'(?<![\w.,])(?P<low>'+NUM+r')\s*(?:[-–—]|\.\.)\s*(?P<high>'+NUM+r')\s*(?P<unit>'+units+r')(?!\w)'
        for m in re.finditer(pattern, source, FLAGS):
            if inside(*m.span('low'), structural_spans) or inside(*m.span(), opaque_spans):
                continue
            range_spans.append(m.span())
            if Decimal(m['low'].replace(',', '.')) > Decimal(m['high'].replace(',', '.')):
                issues.append({'code': 'INVALID_RANGE', 'key': key, 'quote': m.group(), 'message': 'Границы диапазона записаны в обратном порядке.'})
                continue
            for group, suffix in [('low', '_min'), ('high', '_max')]:
                add(key+suffix, m, group=group, rule='explicit-range:'+key, source_unit=m['unit'],
                    factor=conversion.get(m['unit'].lower(), 1), scalar_guard=False)
        for m in re.finditer(r'(?<![\w.,])(?P<value>'+NUM+r')\s*(?P<unit>'+units+r')(?!\w)', source, FLAGS):
            if inside(*m.span(), opaque_spans + range_spans):
                continue
            add(key, m, group='value', rule='explicit-unit:'+key,
                source_unit=m['unit'], factor=conversion.get(m['unit'].lower(), 1))
    for key, label in [('length_mm', r'длина|L'), ('width_mm', r'ширина|B'), ('thickness_mm', r'толщина|S')]:
        for m in re.finditer(r'(?<!\w)(?:'+label+r')\s*[:=]?\s*(?P<value>'+NUM+r')\s*(?P<unit>мм|mm|м|m)(?!\w)', source, FLAGS):
            add(key, m, group='value', rule='label-unit:'+key, source_unit=m['unit'], factor=1000 if m['unit'].lower() in ('м','m') else 1)
    for m in re.finditer(r'(?<!\w)(?:DN|Ду)\s*[:=]?\s*(?P<value>'+NUM+r')(?![\w.,])', source, FLAGS):
        add('nominal_diameter_mm', m, group='value', rule='explicit-DN', source_unit='mm')
    for m in re.finditer(r'(?<!\w)(?:PN|Ру)\s*[:=]?\s*(?P<value>'+NUM+r')\s*(?P<unit>МПа|MPa|бар|bar)(?!\w)', source, FLAGS):
        add('pressure_mpa', m, group='value', rule='explicit-PN-unit', source_unit=m['unit'], factor='.1' if m['unit'].lower() in ('бар','bar') else 1)

    if 'cable' in types:
        groups = list(re.finditer(r'(?<![\w.,])(?P<n>\d{1,3})\s*[xх×*]\s*(?P<s>'+NUM+r')(?![\w.,])', source, FLAGS))
        if len(groups) == 1 or (len(groups) == 2 and re.fullmatch(r'\s*\+\s*', source[groups[0].end():groups[1].start()])):
            for index, m in enumerate(groups):
                prefix = 'additional_' if index else ''
                add(prefix+'core_count', m, group='n', rule='cable-group-count', source_unit='pcs', scalar_guard=False)
                add(prefix+'core_cross_section_mm2', m, group='s', rule='cable-group-section', source_unit='mm2', scalar_guard=False)
        elif groups:
            issues.append({'code': 'AMBIGUOUS_CABLE_GROUPS', 'quote': source})
        for m in re.finditer(r'(?<!\w)(?:А?ВВГ(?:нг(?:\([AА]\))?)?(?:-LS|-FRLS)?|КВВГ(?:нг)?|КГ(?:-ХЛ)?|ПВС|ШВВП)(?![А-Яа-яA-Za-z])', source, FLAGS):
            if not any(name == 'cable' for name, _, _ in family_matches):
                add('designation', m, rule='cable-mark-dictionary')
    if 'pipe' in types:
        m = re.search(r'\bтруб[аы]\s+(?P<d>'+NUM+r')\s*[xх×]\s*(?P<t>'+NUM+r')(?![\d.,])', source, FLAGS)
        if m:
            add('outside_diameter_mm', m, group='d', rule='pipe-primary-dimension', source_unit='mm')
            add('wall_thickness_mm', m, group='t', rule='pipe-primary-dimension', source_unit='mm')
            if re.search(r'\+|(?:ф|Ø)\s*\d', source[m.end():], FLAGS):
                issues.append({'code': 'COMPOSITE_PIPE_NEEDS_REVIEW', 'quote': source[m.end():], 'message': 'Дополнительные компоненты не приравнены к основной трубе.'})
    string_rules = [
        ('protection_rating', r'\bIP\s*\d{2}[A-D]?(?!\w)', 'explicit-IP'),
        ('climate', r'(?<![А-Яа-яA-Za-z])(?:УХЛ|ХЛ|У|Т|ОМ)\s*\d(?:[.,]\d)?(?!\w)', 'climate-dictionary'),
        ('material_grade', r'(?<!\w)(?:09Г2С|12Х18Н10Т|08Х18Н10|Ст[ .]?3(?:сп|пс)?)(?!\w)', 'steel-dictionary'),
        ('thread', r'(?<!\w)[МM]\s*\d+(?:[xх×]\s*'+NUM+r')?(?![\w.,])', 'metric-thread'),
        ('connection', r'\b(?:фланцев(?:ый|ое|ая)|фл\.?|муфтов(?:ый|ое|ая)|резьбов(?:ый|ое|ая))(?!\w)', 'connection-dictionary'),
        ('accessories', r'\bбез\s+(?:вилок|вилки|ответных\s+фланцев|электропривода|привода)\b', 'explicit-absence'),
    ]
    for key, pattern, rule in string_rules:
        for m in re.finditer(pattern, source, FLAGS):
            if key == 'thread' and inside(*m.span(), opaque_spans):
                continue
            add(key, m, rule=rule)
    for m in re.finditer(r'(?<!\w)G\s*\d+\s*/\s*\d+(?!\d)', source, FLAGS):
        if not inside(*m.span(), opaque_spans):
            add('pipe_thread', m, value=re.sub(r'\s+', '', m.group()).upper(), rule='pipe-thread-G')
    for m in re.finditer(r'(?<!\w)(?:DC|AC)\s*\d+(?!\w)', source, FLAGS):
        add('supply_code', m, rule='supply-marking-only')
    for m in re.finditer(r'\b(?:эл\s*/\s*прив\.?|электропривод|редуктор)\b\.?', source, FLAGS):
        add('drive', m, rule='explicit-drive')
    for m in re.finditer(r'\bс\s+гайками\b', source, FLAGS):
        add('accessories', m, rule='explicit-nuts')
    if 'stud' in types:
        for m in re.finditer(r'(?<=А)[МM]\d+(?=-)|(?<!\w)[МM]\d+(?=-)', source, FLAGS):
            add('thread', m, rule='stud-metric-thread')
        for m in re.finditer(r'(?<=[-])\d+[a-z](?=[xх×])', source, FLAGS):
            add('thread_tolerance', m, rule='stud-thread-tolerance')
    for m in re.finditer(r'(?<![\w.,])(?P<a>'+NUM+r')\s*[xх×]\s*(?P<b>'+NUM+r')\s*[xх×]\s*(?P<c>'+NUM+r')\s*(?P<unit>мм|mm|м|m)?(?![\w.,])', source, FLAGS):
        if not any(t in types for t in ('fire_cabinet', 'concrete_slab')):
            continue
        if m['unit']:
            factor = 1000 if m['unit'].lower() in ('м', 'm') else 1
            result = '×'.join(format(Decimal(m[g].replace(',', '.'))*factor, 'f').rstrip('0').rstrip('.') if '.' in format(Decimal(m[g].replace(',', '.'))*factor, 'f') else format(Decimal(m[g].replace(',', '.'))*factor, 'f') for g in ('a','b','c'))
            add('dimensions_mm', m, value=result, rule='explicit-dimensions-unit', source_unit=m['unit'], factor=factor)
        else:
            add('dimensions_text', m, value='×'.join(m[g] for g in ('a','b','c')), rule='dimensions-unspecified-unit')
            issues.append({'code': 'DIMENSION_UNIT_UNSPECIFIED', 'quote': m.group(), 'message': 'Единица размеров и порядок осей в строке не указаны.'})
    if 'concrete_slab' in types:
        for key, pattern in [('reinforcement_mark', r'Ат[IVX]+'), ('f_mark', r'\bF\s*\d+')]:
            for m in re.finditer(pattern, source, FLAGS):
                add(key, m, rule='slab-marking:'+key)
    if 'gas_cylinder_store' in types:
        for m in re.finditer(r'(?<!\w)(?P<n>\d+)\s*шт\b', source, FLAGS):
            add('cylinder_capacity', m, group='n', rule='store-cylinder-capacity', source_unit='pcs')
        for m in re.finditer(r'\b(?:азотом|кислород\.?|аргоном)(?!\w)', source, FLAGS):
            add('gas', m, rule='store-gas-name')
        for m in re.finditer(r'(?<!\w)\d+/\d+\s*$', source):
            add('reference_code', m, rule='store-reference-code')
    if 'electrical_system' in types:
        for m in re.finditer(r'(?<!\w)\d+_ОЛ_\d+_\d+-Р-\d+[.]\d+(?!\w)', source, FLAGS):
            add('reference_code', m, rule='electrical-reference-code')
    if 'rescue_tool' in types:
        for m in re.finditer(r'(?<=-)[РНК]\d+(?!\w)', source, FLAGS):
            add('variant_code', m, rule='rescue-variant-code')
    if 'gate_valve' in types:
        m = re.search(r'(?<!\w)\d+\s*[xх×]\s*\d+(?!\w)', source, FLAGS)
        if m:
            issues.append({'code': 'VALVE_CODES_NEED_STANDARD', 'quote': m.group(), 'message': 'Пара кодов без DN/PN и единиц: давление не назначено автоматически.'})
    for key, label in [('designation', 'марка|модель|обозначение'), ('manufacturer', 'производитель|завод')]:
        for m in re.finditer(r'\b(?:'+label+r')\s*[:=]\s*(?P<value>[\w][\w./()+-]{0,79})', source, FLAGS):
            add(key, m, group='value', rule='explicit-label:'+key)

    attrs, evidence, features = {'name': clean(source)[:500]}, {}, []
    evidence['name'] = {'quote': source, 'start': 0, 'end': len(source), 'method': 'source-copy', 'rule': 'grounded-name', 'raw_value': attrs['name'], 'source_unit': 'none', 'target_unit': 'none'}
    for key, values in candidates.items():
        label, kind, target = FEATURES[key]
        features.append({'key': key, 'label': label, 'kind': kind, 'unit': target, 'comparison': 'informational', 'tolerance': 0})
        distinct = {value for value, _ in values}
        if len(distinct) != 1:
            attrs[key] = None
            issues.append({'code': 'CONFLICTING_VALUES', 'key': key, 'values': sorted(distinct, key=str), 'evidence': [e for _, e in values]})
        else:
            attrs[key], evidence[key] = values[0]
    if not any(v is not None for k, v in attrs.items() if k != 'name'):
        issues.append({'code': 'NO_RECOGNIZED_PROPERTIES', 'message': 'Нет параметров, поддержанных текущими правилами.'})
    return {
        'name': attrs['name'], 'name_evidence': source, 'attributes': attrs,
        'evidence': evidence, 'normalized_attributes': attrs.copy(),
        'normalized_evidence': evidence.copy(), 'normalized_features': features,
        'category_id': None, 'category_evidence': None, 'type_hints': types,
        'normalization': {'method': 'regex', 'status': 'rule_based', 'requires_review': bool(issues), 'attempts': 1, 'errors': []},
        'provenance': {'provider': 'regex', 'model': VERSION, 'method': 'regex',
                       'rules_version': VERSION, 'rules_sha256': RULES_SHA256,
                       'source_hash': hashlib.sha256(source.encode()).hexdigest(),
                       'llm_calls': 0, 'prompt_version': None,
                       'development_source_sha256': DEVELOPMENT_SOURCE_SHA256},
    }, issues
