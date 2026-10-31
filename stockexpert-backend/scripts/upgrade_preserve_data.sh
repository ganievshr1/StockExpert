#!/usr/bin/env bash
# StockExpert 3.9.11 — additive experiment history and optional H2 upgrade.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
project="${STOCKEXPERT_PROJECT:-stockexpert-backend}"
compose=(docker compose -p "$project")
test -f .env || { echo 'Нет прежнего .env. Скопируйте его из предыдущей установки. Не создавайте новые пароли для существующей базы.' >&2; exit 1; }
"${compose[@]}" config --quiet
docker volume inspect "${project}_postgres_data" >/dev/null 2>&1 || {
  echo "Том ${project}_postgres_data не найден. Остановлено, чтобы не подключить пустую базу. Укажите прежний STOCKEXPERT_PROJECT." >&2
  exit 1
}
"${compose[@]}" up -d --no-deps --wait postgres
table_exists=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT to_regclass('public.jobs') IS NOT NULL")
[[ "$table_exists" == "t" ]] || { echo 'В подключённой базе нет прежних таблиц. Обновление остановлено.' >&2; exit 1; }
active_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')")
if [[ "$active_jobs" != "0" ]]; then
  echo 'Есть незавершённые задания. Дождитесь их завершения или отмените через интерфейс, затем повторите обновление.' >&2
  exit 1
fi
# Check new experiments on subsequent upgrades, without requiring the new table on 3.9.1.
regex_table=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT to_regclass('public.normalization_experiments') IS NOT NULL")
if [[ "$regex_table" == "t" ]]; then
  regex_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM normalization_experiments WHERE status IN ('QUEUED','RUNNING')")
  [[ "$regex_jobs" == "0" ]] || { echo 'Дождитесь завершения regex-экспериментов перед обновлением.' >&2; exit 1; }
fi
lab_table=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT to_regclass('public.lab_experiments') IS NOT NULL")
lab_jobs=0
if [[ "$lab_table" == "t" ]]; then
  lab_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM lab_experiments WHERE status IN ('QUEUED','RUNNING')")
  [[ "$lab_jobs" == "0" ]] || { echo 'Дождитесь завершения лабораторных экспериментов перед обновлением.' >&2; exit 1; }
fi
# Block new API submissions and outbox publication before the final active check.
"${compose[@]}" stop backend beat
active_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')")
regex_jobs=0
if [[ "$regex_table" == "t" ]]; then
  regex_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM normalization_experiments WHERE status IN ('QUEUED','RUNNING')")
fi
if [[ "$lab_table" == "t" ]]; then
  lab_jobs=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "SELECT count(*) FROM lab_experiments WHERE status IN ('QUEUED','RUNNING')")
fi
if [[ "$active_jobs" != "0" || "$regex_jobs" != "0" || "$lab_jobs" != "0" ]]; then
  "${compose[@]}" start backend beat
  echo 'Во время проверки начался новый расчёт. Прежние сервисы возобновлены; дождитесь завершения и повторите обновление.' >&2
  exit 1
fi
"${compose[@]}" stop worker worker-regex
backup_dir="../stockexpert-backup-$(date +%Y%m%d-%H%M%S)"
mkdir "$backup_dir"
cp .env "$backup_dir/backend.env"
"${compose[@]}" exec -T postgres pg_dump -U stockexpert -d stockexpert -Fc > "$backup_dir/postgres.dump"
test -s "$backup_dir/postgres.dump"
experiment_count_sql="0"
if [[ "$regex_table" == "t" ]]; then
  experiment_count_sql="(SELECT count(*) FROM normalization_experiments)"
fi
lab_counts_sql="0"
revision_counts_sql="0"
snapshot_counts_sql="0"
if [[ "$lab_table" == "t" ]]; then
  lab_counts_sql="(SELECT count(*) FROM lab_experiments)"
  revision_counts_sql="(SELECT count(*) FROM metric_revisions)"
  snapshot_counts_sql="(SELECT count(*) FROM run_snapshots)"
fi
history_sql="SELECT json_build_object('sessions',(SELECT count(*) FROM procurement_sessions),'documents',(SELECT count(*) FROM documents),'items',(SELECT count(*) FROM items),'jobs',(SELECT count(*) FROM jobs),'matches',(SELECT count(*) FROM match_results),'regex_experiments',$experiment_count_sql,'lab_experiments',$lab_counts_sql,'metric_revisions',$revision_counts_sql,'run_snapshots',$snapshot_counts_sql)::text"
history_before=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "$history_sql")
printf '%s\n' "$history_before" > "$backup_dir/history-counts-before.json"
echo "Резервная копия базы и .env: $backup_dir. Храните её приватно."
"${compose[@]}" build backend worker worker-regex beat migrate
# Explicit one-off migration avoids reusing an old completed migrate container.
"${compose[@]}" run --rm --no-deps migrate
history_after=$("${compose[@]}" exec -T postgres psql -U stockexpert -d stockexpert -tAc "$history_sql")
[[ "$history_before" == "$history_after" ]] || { echo 'Контроль количества исторических записей не пройден. Сервисы оставлены остановленными; сохранена резервная копия.' >&2; exit 1; }
"${compose[@]}" up -d --no-deps --wait redis
"${compose[@]}" up -d --no-deps backend worker worker-regex beat
"${compose[@]}" ps
echo 'Обновление бэкэнда завершено. Тома, модели, пароли и прежние расчёты сохранены. CPU-очередь регулярных выражений запущена.'
