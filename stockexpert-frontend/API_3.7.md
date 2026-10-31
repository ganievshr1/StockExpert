# Дополнения интерфейса 3.7

Префикс `/api/v1`, авторизация Bearer JWT. Все новые запросы ограничены организацией текущего пользователя. Существующие маршруты 3.6 сохранены. `/system/config` возвращает `api_contract_version: "3.6"` и `ui_contract_version: "3.7"`.

| Метод / путь | Назначение |
| --- | --- |
| GET `/workspace/sessions` | `q`, `status`, `days`, `offset`, `limit`; `{total, items}`, дата последнего события в `updated_at` |
| GET `/workspace/overview` | Организация, число сессий и эффективность последнего завершённого подбора; `days` |
| POST `/procurement/sessions/{sid}/documents/{did}/replace` | Multipart `file`, замена источника до начала обработки; сначала проверяется новый файл |
| GET `/audit/{sid}/search` | `q`, `event_type`, `author_id` (`0` — система), `date_from`, `date_to`, `offset`, `limit`; `{total, events, authors, event_types}` |
| POST `/taxonomy/{sid}/categories` | `{name, parent_id?}`; только эксперт до утверждения; возвращает обновлённую схему |
| POST `/taxonomy/{sid}/features` | `{category_id, feature}`; признак добавляется к корневому материалу и его потомкам; возвращает схему |
| GET `/reports/{sid}/document` | `format=pdf\|xlsx`, `summary`, `candidates`, `evidence`, `history`, `inline`; хотя бы один раздел обязателен |

`GET /quality/{sid}/targets` дополнен `q`, `unreviewed`, `requirement_id` для очереди проверки. Существующие `kind`, `shortlisted`, `offset`, `limit` сохранены. Значения качества сохраняются через прежние `/quality` PUT/DELETE/finalize. Источники кандидатов содержат `sheet`, `row_index`, `document_id`.

Ошибки: 401 — требуется вход, 403 — недостаточно прав, 404 — объект недоступен, 409 — конфликт состояния, 413 — размер файла, 415 — формат, 422 — данные не прошли проверку. Прямые старые маршруты экспорта Excel/PDF, метрик XLSX и аудита CSV также доступны.

История контракта: `API_CONTRACT.md`. Полная схема запросов: `openapi.json`.
