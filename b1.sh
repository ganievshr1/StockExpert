#!/usr/bin/env bash
# Isolated B1 installation. Never deletes data volumes.
set -euo pipefail
B1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export STOCKEXPERT_ENV_FILE="$B1_ROOT/stockexpert-backend/.env"
export STOCKEXPERT_NETWORK="${STOCKEXPERT_NETWORK:-stockexpert-b1-network}"
export BACKEND_PORT="${BACKEND_PORT:-8002}"
export FRONTEND_PORT="${FRONTEND_PORT:-5175}"
export MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9003}"
B1_PROJECT="${B1_PROJECT:-stockexpert-b1}"
B1_FRONTEND_PROJECT="${B1_PROJECT}-frontend"
if [[ "$B1_PROJECT" == stockexpert-backend ]]; then
  B1_FRONTEND_PROJECT=stockexpert-frontend
fi
B1_MODE_FILE="$B1_ROOT/.b1-mode"
mode=shared
if [[ -f "$B1_MODE_FILE" ]]; then mode="$(cat "$B1_MODE_FILE")"; fi
usage() {
  cat <<'TEXT'
Usage: bash b1.sh init|start [shared|split]|models|status|logs|stop
Isolated defaults: API :8002, UI :5175, MinIO :9003.
Optional: B1_CPU=1 (Compose >=2.24.4) disables the NVIDIA reservation.
Use the same B1_CPU and environment values for every command and both profiles.
TEXT
}
backend_compose() {
  local args=(docker compose --project-name "$B1_PROJECT" --env-file "$STOCKEXPERT_ENV_FILE"
    -f "$B1_ROOT/stockexpert-backend/docker-compose.yml" -f "$B1_ROOT/compose.b1.$mode.yml")
  if [[ "${B1_CPU:-0}" == 1 ]]; then args+=(-f "$B1_ROOT/compose.b1.cpu.yml"); fi
  "${args[@]}" --profile backend-experiments "$@"
}
frontend_compose() {
  docker compose --project-name "$B1_FRONTEND_PROJECT" -f "$B1_ROOT/stockexpert-frontend/docker-compose.yml" "$@"
}
guard_idle() {
  local backend_id active
  backend_id="$(backend_compose ps -q backend)"
  if [[ -n "$backend_id" ]]; then
    active="$(backend_compose exec -T backend python -c "from sqlalchemy import select,func; from app.db import SessionLocal,Job,LabExperiment,NormalizationExperiment; from app.services.b1_experiments import active_runs; db=SessionLocal(); print(len(active_runs()) + sum(db.scalar(select(func.count()).select_from(model).where(model.status.in_(['QUEUED','RUNNING','CANCEL_REQUESTED']))) or 0 for model in (Job,LabExperiment,NormalizationExperiment))); db.close()")"
    if [[ "$active" != 0 ]]; then
      printf 'Found %s active jobs. Wait for completion/cancellation before restarting or switching queues.\n' "$active" >&2
      exit 1
    fi
  fi
}
action="${1:-help}"
case "$action" in
  init)
    if [[ -f "$STOCKEXPERT_ENV_FILE" ]]; then
      printf 'Existing backend .env preserved.\n'
    else
      python3 "$B1_ROOT/stockexpert-backend/scripts/init_env.py"
    fi
    ;;
  start)
    next_mode="${2:-shared}"
    if [[ "$next_mode" != shared && "$next_mode" != split ]]; then usage; exit 2; fi
    if [[ ! -f "$STOCKEXPERT_ENV_FILE" ]]; then printf 'Run bash b1.sh init first.\n' >&2; exit 1; fi
    guard_idle
    mode="$next_mode"
    backend_compose up -d --build
    frontend_compose up -d --build
    printf '%s\n' "$mode" > "$B1_MODE_FILE"
    printf 'Started %s. UI http://localhost:%s ; API http://localhost:%s/docs\n' "$mode" "$FRONTEND_PORT" "$BACKEND_PORT"
    ;;
  models)
    backend_compose exec -T ollama ollama pull bge-m3
    model="$(python3 - "$STOCKEXPERT_ENV_FILE" <<'PY'
import sys
from pathlib import Path
values={}
for line in Path(sys.argv[1]).read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1); values[key.strip()]=value.strip().strip('\"').strip("'")
if values.get('LLM_PROVIDER','ollama')=='ollama':
    print(values.get('LLM_MODEL','qwen3:8b-q4_K_M'))
PY
)"
    if [[ -n "$model" ]]; then backend_compose exec -T ollama ollama pull "$model"; fi
    ;;
  status) backend_compose ps; frontend_compose ps ;;
  logs) backend_compose logs --tail=100 backend worker worker-batch worker-control worker-b1 beat ;;
  stop) frontend_compose stop; backend_compose stop ;;
  *) usage; [[ "$action" == help ]] || exit 2 ;;
esac
