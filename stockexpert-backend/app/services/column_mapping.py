"""Ground spreadsheet mappings in actual column IDs before accepting model output."""
import json
import re

from app.services import llm, normalization_engine
from app.services.contracts import ColumnMapping

MAX_ATTEMPTS = 2
PROMPT = (
    'Определи только расположение данных одного листа Excel. '
    'name_columns и category_column содержат идентификаторы СТОЛБЦОВ, '
    'а не содержимое ячеек, названия товаров или товарных групп. '
    'Выбирай только из allowed_column_ids: A = "0", B = "1", C = "2". '
    'header_row — номер строки заголовка, 0 при отсутствии заголовка. '
    'Первую строку с товаром не считай заголовком. '
    'name_columns включают наименование, описание и технические поля. '
    'Не включай складской номер и цену без необходимости. '
    'category_column можно указать как контекст или null. '
    'Категории и признаки не создавай.'
)


def excel_letter(index):
    number = int(index) + 1
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def resolve(mapping, columns, last_row_index, sheet=''):
    """Shared validation for model proposals and manual/API edits."""
    result = ColumnMapping.model_validate(mapping).model_dump()
    columns = set(columns)

    def column(value):
        text = value.strip()
        if text in columns:
            return text
        if re.fullmatch(r'[A-Za-z]+', text):
            number = 0
            for char in text.upper():
                number = number * 26 + ord(char) - ord('A') + 1
            return str(number - 1)
        return text

    result['name_columns'] = [column(c) for c in result['name_columns']]
    if result['category_column'] is not None:
        result['category_column'] = column(result['category_column'])
    if (not set(result['name_columns']) <= columns or
            (result['category_column'] is not None and result['category_column'] not in columns)):
        raise ValueError(
            f'Unknown mapped column: sheet={sheet}; requested={result["name_columns"]}, '
            f'category={result["category_column"]}; available={sorted(columns, key=int)} '
            '(zero-based indexes; Excel letters are also accepted)'
        )
    if len(result['name_columns']) != len(set(result['name_columns'])):
        raise ValueError('Duplicate mapped column: select each description column once')
    if result['header_row'] >= last_row_index:
        raise ValueError('No data after header')
    return result


def response_schema(columns, last_row_index):
    schema = ColumnMapping.model_json_schema()
    # Excel letters remain compatible with older models and the manual editor.
    permitted = [*columns, *(excel_letter(c) for c in columns)]
    schema['properties']['name_columns'].update(
        items={'type': 'string', 'enum': permitted},
        maxItems=min(10, len(columns)), uniqueItems=True,
    )
    schema['properties']['category_column'] = {
        'anyOf': [{'type': 'string', 'enum': permitted}, {'type': 'null'}],
        'default': None,
    }
    schema['properties']['header_row']['maximum'] = last_row_index - 1
    return schema


def discover(sample, tick):
    rows = sample['first_rows']
    if not rows:
        raise ValueError('Пустой лист: выберите файл с данными.')
    columns = sorted(sample.get('available_columns', {k for row in rows for k in row['cells']}), key=int)
    last_row_index = sample.get('last_row_index', max(row['row_index'] for row in rows))
    schema = response_schema(columns, last_row_index)
    data = {**sample, 'allowed_column_ids': columns}
    errors = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        tick('SCHEMA_DISCOVERY')
        try:
            raw = llm.structured(PROMPT, data, schema)
        except llm.LLMResponseError as exc:
            raw = exc.raw_response
            error = str(exc)
        else:
            try:
                mapping = resolve(raw, columns, last_row_index, sample['sheet'])
            except ValueError as exc:
                error = str(exc)
            else:
                return mapping, {'method': 'llm', 'attempts': attempt, 'errors': errors}
        # Only malformed model responses are recoverable here. Outages,
        # authentication failures, cancellation and programming errors propagate.
        errors.append(error[:800])
        data = {**sample, 'allowed_column_ids': columns,
                'previous_response': json.dumps(raw, ensure_ascii=False)[:2000],
                'validation_error': error[:800],
                'repair_instruction': 'Исправь ответ: укажи номера столбцов из allowed_column_ids, не значения ячеек. Сохрани строки с товарами.'}

    # This locates columns only; it does not switch the H1 extractor to regex.
    # REVIEW_REQUIRED and mapping_approved=False still gate all normalization.
    fallback = normalization_engine.column_mapping(sample)
    preview_columns = sorted({k for row in rows for k in row['cells']}, key=int)
    if fallback['header_row'] == 0 and fallback['name_columns'] == preview_columns:
        # An unrecognized headerless sheet must not lose columns appearing
        # after the ten-row preview. The contract still limits input to 10.
        if len(columns) > 10:
            raise ValueError('Не удалось определить столбцы: более 10 колонок без распознанного заголовка. Добавьте заголовки Описание/Наименование и повторите подготовку.')
        fallback['name_columns'] = columns
    mapping = resolve(fallback, columns, last_row_index, sample['sheet'])
    return mapping, {'method': 'deterministic_fallback', 'attempts': MAX_ATTEMPTS,
                     'errors': errors, 'requires_review': True}
