#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
target="$PWD"
project="stockexpert-backend"
docker info >/dev/null
docker compose version >/dev/null
docker volume inspect "${project}_postgres_data" >/dev/null 2>&1 || {
  echo 'Не найдена прежняя база stockexpert-backend_postgres_data. Обновление остановлено.' >&2
  exit 1
}
if [[ ! -f "$target/stockexpert-backend/.env" ]]; then
  container="$(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter 'label=com.docker.compose.service=backend' | head -n 1)"
  previous=""
  if [[ -n "$container" ]]; then
    previous="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container")"
  fi
  for file in "${previous:-/nonexistent}/.env" /home/ganie/Stock3.9.11/stockexpert-backend/.env /home/ganie/StockExpert3.9.{11,10,9,8,7,6,5,4,3,2,1,0}/stockexpert-backend/.env; do
    if [[ -f "$file" ]]; then
      cp "$file" "$target/stockexpert-backend/.env"
      break
    fi
  done
fi
[[ -f "$target/stockexpert-backend/.env" ]] || {
  echo "Скопируйте прежний .env в $target/stockexpert-backend/.env и повторите запуск." >&2
  exit 1
}
chmod 600 "$target/stockexpert-backend/.env"
cd "$target/stockexpert-backend"
docker compose -p "$project" config --quiet
docker compose -p "$project" up -d --wait postgres redis minio qdrant ollama
STOCKEXPERT_PROJECT="$project" bash scripts/upgrade_preserve_data.sh
docker compose -p "$project" up -d --no-deps --wait backend
cd "$target/stockexpert-frontend"
docker compose -p stockexpert-frontend up -d --build --wait
echo 'Обновление завершено: http://localhost:5173'
echo "Результаты: Для разработчика → Ручная / Автоматическая проверка → Сравнение расчётов H1 / H2 / H3."
echo "Выбор H2: Новая сессия или Данные → Режим H2 — таксономия (до начала обработки)."
