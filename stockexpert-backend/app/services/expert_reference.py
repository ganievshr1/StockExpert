"""Portable, versioned expert data. Contains no session UUIDs or model outputs."""
import hashlib
import json
import math
import unicodedata
from io import BytesIO
from itertools import islice
from typing import Literal

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.parsing import validate_container


def text_key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().replace('ё', 'е').split())


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Source(Strict):
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    filename: str = Field(min_length=1, max_length=255)
    kind: Literal['residual', 'requirement']


class Feature(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    label: str = Field(min_length=1, max_length=160)
    unit: str = Field(default='none', max_length=30)
    aliases: list[str] = Field(default_factory=list, max_length=40)


class Value(Strict):
    value: str | float | None
    evidence: str = Field(default='', max_length=2000)
    reviewed: bool = True


class Passport(Strict):
    id: str = Field(min_length=1, max_length=100)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['residual', 'requirement']
    sheet: str = Field(default='', max_length=100)
    row: int = Field(ge=1)
    source_name: str = Field(min_length=1, max_length=2000)
    source_cells: dict[str, str] = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=2000)
    attributes: dict[str, Value] = Field(default_factory=dict)
    category_path: list[str] = Field(default_factory=list, max_length=3)
    note: str = Field(default='', max_length=4000)
    split_group: str = Field(default='', max_length=100)


class Pair(Strict):
    requirement_id: str
    item_id: str
    relevant: bool | None
    reason: str = Field(default='', max_length=2000)


class Reference(Strict):
    format: Literal['stockexpert-expert-v1']
    h1_mode: Literal['attributes', 'name_exact'] = 'attributes'
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    author: str = Field(default='Эксперт', max_length=200)
    notes: list[str] = Field(default_factory=list, max_length=100)
    sources: list[Source] = Field(min_length=1, max_length=50)
    features: list[Feature] = Field(default_factory=list, max_length=128)
    passports: list[Passport] = Field(min_length=1, max_length=25050)
    pairs: list[Pair] = Field(default_factory=list, max_length=250000)

    @model_validator(mode='after')
    def validate_links(self):
        sources = {s.sha256: s.kind for s in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError('Повторяющийся исходный файл')
        features = {f.key for f in self.features}
        if len(features) != len(self.features) or 'name' in features:
            raise ValueError('Ключи параметров должны быть уникальны; name проверяется отдельно')
        aliases = {}
        for f in self.features:
            for alias in [f.key, f.label, *f.aliases]:
                key = text_key(alias)
                if key in aliases and aliases[key] != f.key:
                    raise ValueError('Один псевдоним относится к разным параметрам: ' + alias)
                aliases[key] = f.key
        ids, locations = {}, set()
        for p in self.passports:
            if self.h1_mode == 'name_exact' and (self.features or p.attributes):
                raise ValueError('Для H1 по тексту оставьте features и attributes пустыми')
            if p.id in ids:
                raise ValueError('Повторяющийся ID паспорта: ' + p.id)
            ids[p.id] = p
            location = (p.source_sha256, p.sheet, p.row, p.kind)
            if location in locations or sources.get(p.source_sha256) != p.kind:
                raise ValueError('Некорректный или повторяющийся источник: ' + p.id)
            locations.add(location)
            if not set(p.attributes) <= features:
                raise ValueError('Неизвестный параметр: ' + p.id)
            evidence_source = ' | '.join(p.source_cells.values()) if p.source_cells else p.source_name
            for v in p.attributes.values():
                if v.value is not None and v.reviewed and (not v.evidence or v.evidence not in evidence_source):
                    raise ValueError('Параметр без цитаты в исходнике: ' + p.id)
        if len({tuple(p.category_path) for p in self.passports if p.category_path}) > 256:
            raise ValueError('Не более 256 экспертных категорий в одном эталоне')
        seen = set()
        for pair in self.pairs:
            key = (pair.requirement_id, pair.item_id)
            if key in seen or pair.requirement_id not in ids or pair.item_id not in ids:
                raise ValueError('Некорректная или повторяющаяся пара')
            if ids[pair.requirement_id].kind != 'requirement' or ids[pair.item_id].kind != 'residual':
                raise ValueError('В паре должны быть потребность и остаток')
            seen.add(key)
        # Recall denominator must include every stock row, not only retrieved hits.
        stock = {p.id for p in self.passports if p.kind == 'residual'}
        requirements = {p.requirement_id for p in self.pairs}
        for req in requirements:
            if {p.item_id for p in self.pairs if p.requirement_id == req} != stock:
                raise ValueError('Для H3 разметьте все строки базы, включая нерелевантные и неопределённые')
        return self


def _from_workbook(data):
    validate_container(data)
    wb = load_workbook(BytesIO(data), read_only=True, data_only=False)
    try:
        def rows(name):
            if name not in wb.sheetnames:
                raise ValueError('Отсутствует лист ' + name)
            ws = wb[name]
            if (ws.max_row or 0) > 250002 or (ws.max_column or 0) > 30:
                raise ValueError('Лист слишком большой')
            values = list(islice(ws.iter_rows(values_only=True), 250003))
            if len(values) > 250002 or any(len(row) > 30 for row in values):
                raise ValueError('Лист слишком большой')
            headers = values[0] if values else []
            return [dict(zip(headers, r)) for r in values[1:] if any(v is not None for v in r)]
        metadata = {r['key']: r['value'] for r in rows('Настройки')}
        value = {key: metadata[key] for key in ('format', 'title', 'version', 'author')}
        value['h1_mode'] = metadata.get('h1_mode', 'attributes')
        value['notes'] = json.loads(metadata['notes'])
        value['sources'] = rows('Источники')
        value['features'] = [{**r, 'aliases': json.loads(r['aliases'] or '[]')} for r in rows('Справочник')]
        passports = rows('Паспорта')
        for p in passports:
            p['source_cells'] = json.loads(p['source_cells'] or '{}')
            p['category_path'] = json.loads(p['category_path'] or '[]')
            p['note'] = p['note'] or ''
            p['sheet'] = p['sheet'] or ''
            p['attributes'] = {}
            if 'split_group' in p:p['split_group'] = p['split_group'] or ''
        by_id = {p['id']: p for p in passports}
        for r in rows('Параметры'):
            if r['passport_id'] not in by_id:
                raise ValueError('Неизвестный паспорт в параметрах')
            attrs = by_id[r['passport_id']]['attributes']
            if r['key'] in attrs:
                raise ValueError('Повторяющийся параметр паспорта')
            attrs[r['key']] = {'value': json.loads(str(r['value_json'])), 'evidence': r['evidence'] or '', 'reviewed': r['reviewed']}
        value['passports'] = passports
        value['pairs'] = [{**r, 'relevant': json.loads(str(r['relevant_json'])), 'reason': r['reason'] or ''} for r in rows('Сопоставление')]
        for p in value['pairs']:
            p.pop('relevant_json')
        return value
    finally:
        wb.close()


def parse_reference(data: bytes, filename: str):
    if filename.lower().endswith('.json'):
        value = json.loads(data.decode('utf-8-sig'))
    elif filename.lower().endswith('.xlsx'):
        value = _from_workbook(data)
    else:
        raise ValueError('Загрузите JSON или XLSX экспертного эталона')
    reference = Reference.model_validate(value)
    payload = reference.model_dump()
    # Optional H2 duplicate groups must not change checksums of existing references.
    for p in payload['passports']:
        if not p['split_group']:p.pop('split_group')
    checksum = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return payload, checksum


def value_key(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return text_key(value) if value is not None else None


def equal_value(actual, expected):
    a, b = value_key(actual), value_key(expected)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return type(a) is type(b) and a == b


def predicted_name(passport):
    """One prediction per source row; category and technical fields are excluded."""
    return passport.get('name', passport.get('attributes', {}).get('name'))


def name_counts(passport, expected):
    actual = predicted_name(passport)
    present = isinstance(actual, str) and bool(actual.strip())
    wanted = isinstance(expected, str) and bool(expected.strip())
    hit = bool(present and wanted and actual.strip() == expected.strip())
    return int(hit), int(present and not hit), int(wanted and not hit)
