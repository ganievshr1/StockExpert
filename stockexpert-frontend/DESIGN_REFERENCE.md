# Соответствие макетам StockExpert

Файл Figma: `zzgrgg8eyGYRIrJqhkz1GL`. Светлая страница `2:2`, тёмная `2:3`.

| Состояние | Light | Dark | Реализация |
| --- | --- | --- | --- |
| Вход | 4:2 | 12:2 | LoginPage |
| Рабочий стол | 4:3 | 12:103 | DashboardPage |
| Обзор сессии | 4:4 | 12:389 | SessionOverviewPage |
| Загрузка | 4:5 | 12:706 | DataPage, DocumentCard, MappingEditor |
| Классификатор | 4:6 | 12:1043 | TaxonomyPage |
| Подбор | 4:7 | 12:1389 | ShortlistPage |
| Решение эксперта | 4:8 | 12:1737 | Окно ShortlistPage |
| Отчёт | 4:9 | 12:2126 | ReportsPage |
| Проверка паспортов | 4:10 | 12:2385 | QualityPage |
| Проверка кандидатов | 4:11 | 12:2673 | QualityPage |
| Аудит | 4:12 | 12:2966 | AuditPage |
| Метрики | 4:13 | 12:3266 | MetricsPage |
| Новая сессия | 4:14 | 12:3562 | NewSessionDialog |
| Обработка | 4:15 | 12:3886 | ProcessingPage |
| Рекомендация | 11:1169 | 12:4198 | ShortlistPage |
| Использование | 11:1420 | 12:4549 | Окно ShortlistPage |

CSS-токены — `src/index.css`. Иконки — `public/figma` и `src/components/figma-icons.json`. Они экспортированы из оригинальных слоёв: HTTP-адреса временных ассетов возвращали страницу ошибки. Текст источников, сведения о пользователе, модели, организации, сессиях и метрики загружаются из API.
