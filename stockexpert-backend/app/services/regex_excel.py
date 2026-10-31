"""Deterministic import of the supplied stock workbook; no LLM or gold labels."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from app.services.regex_normalization import clean, DEVELOPMENT_SOURCE_SHA256

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 25000
HEADERS = ('Предприятие', 'КИП и средства автоматизации', 'Система')


def parse_stock_xlsx(data, filename, reference=None):
    if Path(filename).suffix.lower() != '.xlsx':
        raise ValueError('Загрузите складской файл XLSX.')
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Размер XLSX должен быть от 1 байта до 20 МБ.')
    try:
        with ZipFile(BytesIO(data)) as z:
            if len(z.infolist()) > 1000 or sum(e.file_size for e in z.infolist()) > 64 * 1024 * 1024:
                raise ValueError('Распакованный XLSX превышает допустимый размер.')
        wb = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, KeyError, OSError, ParseError) as exc:
        raise ValueError('Не удалось прочитать XLSX.') from exc
    sha = hashlib.sha256(data).hexdigest()
    filename = Path(filename.replace('\\', '/')).name[:255]
    rows, mappings = [], []
    try:
        if len(wb.worksheets) > 20:
            raise ValueError('Допускается не более 20 листов.')
        for ws in wb.worksheets:
            header = next(ws.iter_rows(min_row=1, max_row=1, max_col=3, values_only=True), ())
            header_values = tuple(clean(str(v or '')) for v in header)
            first_is_data = len(header_values) == 3 and header_values[:2] == HEADERS[:2] and header_values[2].startswith('Система ')
            if (header_values != HEADERS and not first_is_data) or (ws.max_column or 0) > 3:
                raise ValueError(f'Лист «{ws.title}»: ожидаются три столбца «Предприятие», «КИП и средства автоматизации», «Система». Описание читается только из C.')
            if (ws.max_row or 0) > MAX_ROWS + 1:
                raise ValueError('Допускается не более 25000 строк.')
            start = 1 if first_is_data else 2
            for index, cells in enumerate(ws.iter_rows(min_row=start, max_col=3, max_row=min(ws.max_row or MAX_ROWS+2, MAX_ROWS+2)), start):
                if any(c.data_type == 'f' for c in cells):
                    raise ValueError(f'Лист «{ws.title}», строка {index}: замените формулы исходными текстовыми значениями.')
                raw = {str(i): str(c.value) for i, c in enumerate(cells) if c.value is not None and str(c.value).strip()}
                if not raw:
                    continue
                text = raw.get('2', '')
                if not text.strip():
                    raise ValueError(f'Лист «{ws.title}», строка {index}: пустое описание в столбце C.')
                if sum(map(len, raw.values())) > 16000:
                    raise ValueError(f'Лист «{ws.title}», строка {index}: слишком длинный текст.')
                rows.append({'source_item_id': str(uuid5(NAMESPACE_URL, f'{sha}:{ws.title}:{index}')),
                             'source_sha256': sha, 'document': filename, 'sheet': ws.title,
                             'row': index, 'kind': 'residual', 'source_text': text, 'raw': raw,
                             'company': raw.get('0'), 'source_group': raw.get('1')})
                if len(rows) > MAX_ROWS:
                    raise ValueError('Допускается не более 25000 строк.')
            mappings.append({'sheet': ws.title, 'header_row': start - 1, 'name_columns': ['2'],
                             'company_column': '0', 'source_group_column': '1', 'method': 'fixed-stock-three-columns-v1'})
    finally:
        wb.close()
    if not rows:
        raise ValueError('В файле нет складских строк.')
    if reference:
        matching = [s for s in reference.payload['sources'] if s['kind'] == 'residual' and s['sha256'] == sha]
        if not matching:
            raise ValueError('Выбранный эталон относится к другому Excel: SHA-256 не совпадает.')
        locations = {(r['sheet'], r['row']) for r in rows}
        gold = [p for p in reference.payload['passports'] if p['kind'] == 'residual']
        if not gold or any(p['source_sha256'] != sha or (p['sheet'], p['row']) not in locations for p in gold):
            raise ValueError('Все складские паспорта эталона должны относиться к строкам загруженного Excel.')
    payload = {'rows': rows, 'mappings': mappings, 'sources': [{'filename': filename, 'sha256': sha}],
               'source_type': 'xlsx', 'reference_checksum': reference.checksum if reference else None,
               'reference_version': reference.version if reference else None,
               'is_development_dataset': sha == DEVELOPMENT_SOURCE_SHA256,
               'input_scope': 'Столбец C: исходное описание. A и B сохранены как метаданные и не подаются извлекателю. Строка 1 сохраняется, если содержит изделие.'}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    if len(serialized) > 64 * 1024 * 1024:
        raise ValueError('Снимок строк превышает 64 МБ.')
    return payload, hashlib.sha256(serialized).hexdigest()
