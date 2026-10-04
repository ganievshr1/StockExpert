# Контракт: совместимость и история

Дополнения нового интерфейса описаны в [API_3.7.md](API_3.7.md). Ниже сохранены исходные контракты версий до 3.6.

# StockExpert · API contract 3.6 · release 3.6.0

Оба архива относятся к одной версии 3.6.0. Префикс API: `/api/v1`. JSON-эндпоинты требуют `Authorization: Bearer <JWT>`, кроме выдачи токена. DeepSeek-ключ никогда не является JWT пользователя и не отправляется из браузера.

| Действие | Метод и путь после префикса |
|---|---|
| Вход | POST `/auth/token`, form-urlencoded username/password |
| Пользователь / настройки | GET `/auth/me`, GET `/system/config` |
| Сессии | GET/POST `/procurement/sessions` |
| Снимок сессии | GET `/procurement/sessions/{sid}` |
| Загрузка | POST `/procurement/sessions/{sid}/upload-residuals` или `upload-requirement`, multipart поле `file` |
| Документы / метрики | GET `/procurement/sessions/{sid}/documents` или `metrics` |
| SSE | GET `/procurement/sessions/{sid}/stream-progress`, Bearer в заголовке |
| Подготовка | POST `/pipeline/{sid}/prepare` |
| Схема | GET/PUT `/pipeline/{sid}/schema` |
| Утверждение столбцов | POST `/pipeline/{sid}/approve-mapping` |
| Перестроение таксономии | POST `/taxonomy/{sid}/rebuild` |
| Утверждение таксономии | POST `/taxonomy/{sid}/approve` |
| Полный процесс | POST `/pipeline/{sid}/run` |
| Нормализация | POST `/pipeline/{sid}/start-normalization` |
| Матчинг / статус | POST `/matching/{sid}/run`, GET `/matching/{sid}/status` |
| Повтор неудачного задания | POST `/pipeline/{sid}/jobs/{jid}/retry` |
| Потребности | GET `/matching/{sid}/benchmark` |
| Кандидаты | GET `/matching/{sid}/candidates?limit=20&offset=0&shortlisted=true&status=ALL` |
| Решение | POST `/matching/{sid}/decide` |
| Сводка без всех пар | GET `/reports/{sid}/summary?include_items=false` |
| Экспорт | GET `/reports/{sid}/export/excel` или `pdf` |
| Аудит | GET `/audit/{sid}/events?limit=50&offset=0` |
| Текущий хеш | GET `/audit/{sid}/trail` |
| Проверка хеша | POST `/audit/{sid}/verify-hash` |
| Журнал CSV | GET `/audit/{sid}/export/csv` |

- `decide.item_id` — **ID результата матчинга**, не ID складской позиции. `allocated_qty=0`; комментарий обязателен.
- Статусы алгоритма: `PASSES_KNOWN_RULES`, `NEEDS_REVIEW`, `CONTRADICTED`. Решения эксперта: `RECOMMENDED`, `NEEDS_REVIEW`, `REJECTED_MANUAL`.
- Пагинация кандидатов возвращает `{total, items}`; карточка содержит `residual` и `requirement` с текстом, паспортом и issues.
- `rank`, `rrf_score`, `cross_encoder_score` берутся с сервера; отсутствующая оценка — null, не 0%.
- SSE: `{status, progress, job}`. Закрытие потока само по себе не означает успешное завершение; клиент сверяет статус задания.
- `system/config.api_contract_version` = `3.6`.
- Экспертные действия требуют `admin` или `dmto_expert`. Создание и запуск также разрешены `buyer`.

Полная схема запросов — `openapi.json`, одинаковая в обоих архивах. Runtime Swagger: http://localhost:8000/docs.


## Управление и измерения 3.4

- POST `/procurement/sessions/{sid}/cancel`: запрос остановки; ответ `session`, `pending`, `message`.
- DELETE `/procurement/sessions/{sid}/documents/{did}`: отдельный неиспользованный документ; после начала обработки требуется полная очистка.
- DELETE `/procurement/sessions/{sid}/files`: полная очистка остановленной сессии; активное задание — 409; частичная ошибка очистки — 503, допускается повтор.
- GET `/metrics/{sid}`: `session_id`, `targets`, `extraction`, `taxonomy`, `matching`, `timing`. Значения качества до завершения экспертной проверки или при недостаточных данных — null; деление на нулевой знаменатель — null.
- GET `/metrics/{sid}/export`: XLSX с тремя листами и ID сессии.
- Новые статусы: CANCEL_REQUESTED, CANCELLED, CLEANING, CLEANUP_FAILED. SSE продолжает обновляться до подтверждения остановки.

Все пути имеют префикс `/api/v1`, требуют Bearer-токен и проверяют организацию сессии. Изменения доступны admin, dmto_expert, buyer. Просмотр метрик доступен авторизованным пользователям своей организации.


## Общая база и эффективность 3.4

- `GET /normalized-bases?limit=100&offset=0` — доступные готовые базы своей организации, только доступные запасы.
- `POST /procurement/sessions` принимает опциональный `base_session_id`. При совпадении конфигурации модели сессия получает готовую схему, доступные паспорта и `base_snapshot`. При другой модели переносятся исходные строки для новой обработки.
- `POST /matching/{sid}/candidates/{mid}/use` — эксперт использует рекомендованную позицию целиком; повторное использование — 409.
- `DELETE /normalized-bases/{sid}/stock` — исключает остатки общей базы из активного использования, сохраняя историю. Активные связанные задания — 409.
- `GET /metrics/{sid}` включает `efficiency`: итоговые и текущие операционные показатели. При неизвестном значении возвращается null.
- Выдача shortlist исключает USED/CLEARED; `shortlisted=false` оставляет исторические пары с `residual.stock_state`.

Новые поля PostgreSQL: `procurement_sessions.base_snapshot`, `items.stock_id`; новая таблица `stock_positions`. Для старых строк stock_id назначается при обращении к базе. API и фронтенд этого выпуска требуют парного обновления до 3.6.


## Экспертная разметка и качество 3.4

Все пути под `/api/v1` и требуют Bearer-токен организации сессии.

- GET `/quality/{sid}` — метрики, завершение и объём проверки последнего завершённого задания match/full.
- GET `/quality/{sid}/targets?kind=candidate|passport&shortlisted=false&limit=10&offset=0` — объекты для проверки, включая исторические пары USED/CLEARED.
- PUT `/quality/{sid}/{kind}/{ident}` — экспертная разметка; обязателен актуальный `job_id`. Для candidate: `suitable: boolean`. Для passport: `attributes: {key: value|null}`, `category_reviewed: boolean`, `category_id: string|null`. Включаются только явно проверенные поля. Необязателен `comment`.
- DELETE `/quality/{sid}/{kind}/{ident}?job_id=...` — снять разметку.
- POST `/quality/{sid}/finalize` с `{job_id}` — завершить проверку размеченной выборки. Пустая разметка — 422, устаревшее/активное задание — 409.
- Изменения доступны admin/dmto_expert. Любое изменение разметки удаляет завершение. Предсказания модели не меняются.
- GET `/metrics/{sid}` включает `quality`, а цели F1/Accuracy/Recall содержат измеренное значение, если оно определено. Precision и объём проверки входят в Excel.

При отсутствии завершения/знаменателя значения метрик — null. Подробные формулы и область оценки — UPGRADE_3.4.md.


## Выбор модели 3.5

- GET `/system/llm-models`: авторизованный каталог. Ответ `{default_profile_id, ollama_version, models}`. В каждом варианте безопасная конфигурация, `external_processing`, `availability: {status, available, message}`, `pull_command`. Ни ключей, ни URL учётных записей в ответе нет.
- POST `/procurement/sessions`: добавлено необязательное поле `llm_profile_id`. Допустимы `qwen25-7b-q4`, `qwen25-7b-q5`, `qwen3-8b`, `qwen25-14b`, `deepseek`; для прежней нестандартной серверной настройки каталог может добавлять `server-default`. Произвольные имена модели и внешние URL клиент не задаёт.
- PUT `/procurement/sessions/{sid}/llm`, JSON `{"llm_profile_id":"qwen3-8b"}`: изменить выбор до первого задания/схемы. Нужны права редактора и принадлежность организации. 409 — модель уже зафиксирована, 422 — неизвестный профиль.
- GET `/procurement/sessions/{sid}` и ответ создания содержат `llm_config`, `llm_locked`, `llm_legacy`. Общий список сессий содержит сохранённый `llm_config`; для вычисленных флагов загрузите отдельную сессию.
- `/system/config` содержит `default_llm_config`, `model_selection_supported=true`; прежние `llm_model`/`llm_provider` описывают **серверный вариант по умолчанию**, а не все сессии.
- Задания сохраняют `params.llm_config`; при исполнении `params.runtime` содержит конфигурацию и `llm_fingerprint`. Повтор задания сохраняет этот снимок.
- Перед постановкой prepare/normalize/full в очередь и при исполнении проверяется доступность выбранного генератора. 503 с пояснением — модель/ключ недоступны. Match использует отдельные поисковые модели.
- `/metrics/{sid}` содержит `llm.selected`, `llm.legacy`, `llm.jobs`; Excel включает выбранную конфигурацию.
- `base_snapshot.reused_passports=false`: исходные строки готовой базы перенесены, но паспорта требуется обработать заново. `source_rows` — число доступных исходных позиций, `source_llm_config` — известная конфигурация источника.

Для безопасности `.env` и ключ DeepSeek остаются на сервере; JSON схемы проверяются сервером для всех моделей.


## Таксономия по паспортам 3.6

Новая сессия: prepare → approve-mapping → start-normalization → review → taxonomy/approve → run.

`prepare` создаёт только `mappings`; `features` и `categories` пусты. В `schema_doc`: `taxonomy_mode="passports"`, `mapping_approved=false`, `taxonomy_status="pending"`. Сначала эксперт сохраняет/утверждает сопоставление столбцов. `start-normalization` нормализует все позиции и строит таксономию. Итоговый статус сессии `TAXONOMY_REVIEW`, `approved=false`, `taxonomy_status="review"`, `analyzed_items` — число паспортов остатков и потребности. После утверждения `taxonomy_status="approved"`, `approved=true`.

Вызов `/pipeline/{sid}/run` до утверждения категорий, но после утверждения столбцов, выполняет нормализацию и построение дерева, затем останавливается на проверке. Такое завершённое задание сохраняется как `kind="normalize"`, чтобы не считаться завершённым подбором. После утверждения `/run` выполняет подбор, переиспользуя паспорта.

Паспорт содержит `name`, `name_evidence`, `normalized_attributes`, `normalized_features`, `normalized_evidence`, затем назначенные `category_id`, `material_id`, `attributes` и `evidence`. Исходный нормализованный JSON сохраняется; ключи `attributes` после категоризации согласованы с признаками соответствующего материала.

Категория: `id`, `name`, `parent_id`, `feature_keys`, `aliases`, `item_count`, `requirement_count`. Счётчик корневой категории включает её подкатегории; складывать его со счётчиками детей нельзя. `features` хранит объединённый реестр; применимость определяется через `feature_keys`. Идентификаторы свойств разных материалов независимы. Для каждого материала есть собственные правила сравнения.

Признак дополнен `aliases`. После построения доступны правки `label`, `comparison`, `tolerance`; в категории — `name`. Структура, единицы и назначения не меняются через произвольный JSON. `name` всегда informational. Утверждённая схема неизменяема. `rebuild` до утверждения повторно собирает дерево по сохранённым паспортам и предложениям классификации, сбрасывая ручные правки правил и названий.

`passports?base_type=<root_id>` и `/taxonomy/{sid}/categories/{root_id}/items` включают потомков корневого материала. Пагинация сохранена. Новые стадии: `PASSPORT_NORMALIZATION` и `TAXONOMY_BUILD`; старые стадии остаются для исторических сессий. Метрики заполненности вычисляются по применимым признакам материала.

Исторические схемы без `taxonomy_mode="passports"` работают по прежнему процессу. Обновление не выполняет скрытую переклассификацию старых сессий.

