"""In-place app-only B1 profile switch. Uses existing projects, network and volumes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.b1-parallel.json'
OVERRIDE = ROOT / '.b1-parallel-existing.json'
APP_SERVICES = ('backend', 'worker', 'worker-batch', 'worker-control',
                'worker-laboratory', 'worker-regex', 'worker-b1', 'beat')
INFRA = ('postgres', 'redis', 'minio', 'qdrant', 'ollama')
ACTIVE_SCRIPT = """from sqlalchemy import select,func
from app.db import SessionLocal,Job,LabExperiment,NormalizationExperiment
from app.services.b1_experiments import active_runs
with SessionLocal() as db:
 print(len(active_runs()) + sum(db.scalar(select(func.count()).select_from(model).where(model.status.in_(['QUEUED','PREPARING','RUNNING','CANCEL_REQUESTED']))) or 0 for model in (Job,LabExperiment,NormalizationExperiment)))
"""
GPU_SCRIPT = """import json,torch
available=torch.cuda.is_available()
print(json.dumps({'torch':torch.__version__,'cuda_build':torch.version.cuda,'cuda_available':available,'device':torch.cuda.get_device_name(0) if available else None}))
raise SystemExit(0 if available else 2)
"""


def run(args, *, capture=False, env=None):
    return subprocess.run(args, check=True, text=True, capture_output=capture, env=env)


def docker(*args):
    return run(['docker', *args], capture=True).stdout.strip()


def fail(message):
    raise ValueError(message)


def container(project, service):
    ids = docker('ps', '-q', '--filter', f'label=com.docker.compose.project={project}',
                 '--filter', f'label=com.docker.compose.service={service}').split()
    if len(ids) != 1:
        fail(f'Нужен ровно один работающий контейнер {project}/{service}; найдено {len(ids)}. '
             'Сначала восстановите обычный запуск существующего стенда.')
    return ids[0]


def inspect_field(ident, field):
    return json.loads(docker('inspect', '--format', '{{json .' + field + '}}', ident))


def binding(network, port):
    values = network.get('Ports', {}).get(port) or []
    if len(values) != 1 or values[0].get('HostIp') not in ('127.0.0.1', '::1'):
        fail(f'Неоднозначная или нелокальная публикация {port}; автоматическое изменение остановлено.')
    value = values[0].get('HostPort', '')
    if not value.isdigit():
        fail(f'Не определён опубликованный порт {port}.')
    return value


def mounted_volume(ident, destination):
    values = [m for m in inspect_field(ident, 'Mounts') if m.get('Destination') == destination]
    if len(values) != 1 or values[0].get('Type') != 'volume':
        fail(f'Ожидался существующий Docker volume {destination}; автоматическая замена остановлена.')
    return values[0]['Name']


def selected_environment(ident, keys):
    # Docker credentials and unrelated environment values never enter our state/output.
    return {key: value for item in inspect_field(ident, 'Config.Env') or []
            for key, separator, value in [item.partition('=')] if separator and key in keys}


def discover(saved):
    project = os.environ.get('B1_PROJECT') or saved.get('project') or 'stockexpert-backend'
    front_project = os.environ.get('B1_FRONTEND_PROJECT') or saved.get('frontend_project') or 'stockexpert-frontend'
    backend = container(project, 'backend')
    frontend = container(front_project, 'frontend')
    back_network = inspect_field(backend, 'NetworkSettings')
    front_network = inspect_field(frontend, 'NetworkSettings')
    common = set(back_network.get('Networks', {})) & set(front_network.get('Networks', {}))
    preferred = os.environ.get('STOCKEXPERT_NETWORK') or saved.get('network')
    if preferred:
        if preferred not in common:
            fail('Сохранённая/заданная сеть не подключена одновременно к backend и frontend.')
        network = preferred
    elif len(common) == 1:
        network = common.pop()
    else:
        fail('Не удалось однозначно выбрать существующую общую сеть backend/frontend.')
    infrastructure = {service: container(project, service) for service in INFRA}
    for service, ident in infrastructure.items():
        if network not in inspect_field(ident, 'NetworkSettings').get('Networks', {}):
            fail(f'Сервис {service} не подключён к существующей сети {network}.')
    worker = container(project, 'worker')
    runtime = selected_environment(backend, {'JOB_ROUTING_MODE', 'B1_RUNTIME_PROFILE'})
    config = {
        'project': project, 'frontend_project': front_project, 'network': network,
        'backend_port': binding(back_network, '8000/tcp'),
        'frontend_port': binding(front_network, '80/tcp'),
        'model_cache': mounted_volume(worker, '/models/huggingface'),
        'b1_artifacts': mounted_volume(backend, '/data/b1'),
        'backend_id': backend,
        'actual_backend_mode': runtime.get('JOB_ROUTING_MODE', 'shared'),
        'actual_backend_profile': runtime.get('B1_RUNTIME_PROFILE', 'standard'),
    }
    # Preserve MinIO's published port even though this launcher never recreates it.
    minio_network = inspect_field(infrastructure['minio'], 'NetworkSettings')
    config['minio_port'] = binding(minio_network, '9001/tcp')
    return config


def write_private(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    os.replace(temporary, path)


def compose(config, *, frontend=False):
    env = {**os.environ, 'STOCKEXPERT_ENV_FILE': str(ROOT / 'stockexpert-backend/.env'),
           'STOCKEXPERT_NETWORK': config['network'], 'BACKEND_PORT': config['backend_port'],
           'FRONTEND_PORT': config['frontend_port'], 'MINIO_CONSOLE_PORT': config['minio_port'],
           'B1_INTERACTIVE_CONCURRENCY': str(config['concurrency'])}
    if frontend:
        args = ['docker', 'compose', '--project-name', config['frontend_project'], '-f',
                str(ROOT / 'stockexpert-frontend/docker-compose.yml')]
    else:
        args = ['docker', 'compose', '--project-name', config['project'], '--env-file',
                env['STOCKEXPERT_ENV_FILE'], '-f', str(ROOT / 'stockexpert-backend/docker-compose.yml'),
                '-f', str(ROOT / f"compose.b1.{config['mode']}.yml")]
        if config['profile'] == 'parallel':
            args += ['-f', str(ROOT / 'compose.b1.parallel.yml')]
            if config['hardware'] == 'gpu':
                args += ['-f', str(ROOT / 'compose.b1.gpu.yml')]
        args += ['-f', str(OVERRIDE), '--profile', 'backend-experiments']
    return args, env


def invoke(config, *args, frontend=False):
    command, env = compose(config, frontend=frontend)
    return run(command + list(args), env=env)


def write_override(config):
    write_private(OVERRIDE, {
        'networks': {'default': {'name': config['network'], 'external': True}},
        'volumes': {key: {'name': config[key], 'external': True} for key in ('model_cache', 'b1_artifacts')},
        'services': {name: {'environment': {'B1_RUNTIME_PROFILE': config['profile']}}
                     for name in APP_SERVICES},
    })


def guard_idle(config):
    value = docker('exec', config['backend_id'], 'python', '-c', ACTIVE_SCRIPT)
    if value != '0':
        fail(f'Активные задания/эксперименты: {value}. Дождитесь завершения или отмены; '
             'контейнеры не перезапущены. Не начинайте новые задания во время обновления.')


def main():
    parser = argparse.ArgumentParser(description='Параллельный режим существующего StockExpert B1 без переноса данных.')
    subs = parser.add_subparsers(dest='action', required=True)
    start = subs.add_parser('start', help='Сборка и запуск параллельного профиля; 4 слота по умолчанию.')
    start.add_argument('hardware', nargs='?', choices=['cpu', 'gpu'])
    start.add_argument('concurrency', nargs='?', type=int, choices=range(1, 11), metavar='1..10')
    restore = subs.add_parser('restore', help='Вернуть стандартные два solo worker для сравнения B1.')
    restore.add_argument('mode', choices=['shared', 'split'])
    for name in ('status', 'logs', 'gpu-check'):
        subs.add_parser(name)
    args = parser.parse_args()
    if not (ROOT / 'stockexpert-backend/.env').is_file():
        fail('Нет stockexpert-backend/.env существующего стенда. Файл настроек не создаётся автоматически.')
    saved = json.loads(STATE.read_text()) if STATE.is_file() else {}
    config = {**saved, **discover(saved)}
    config.setdefault('profile', 'standard')
    config.setdefault('hardware', 'cpu')
    config.setdefault('concurrency', 4)
    config.setdefault('mode', 'shared')
    if args.action == 'start':
        default_slots = config['concurrency'] if config['profile'] == 'parallel' else 4
        config.update(profile='parallel', mode='split',
                      hardware=args.hardware or config['hardware'],
                      concurrency=args.concurrency or default_slots)
    elif args.action == 'restore':
        config.update(profile='standard', mode=args.mode, hardware='cpu', concurrency=1)
    write_override(config)
    if args.action in ('start', 'restore'):
        guard_idle(config)
        invoke(config, 'config', '--quiet')
        invoke(config, 'config', '--quiet', frontend=True)
        invoke(config, 'build', *APP_SERVICES)
        invoke(config, 'build', 'frontend', frontend=True)
        # Builds can be long: check again immediately before stopping any app worker.
        guard_idle(config)
        config['transition'] = 'app_restart_pending'
        write_private(STATE, config)
        invoke(config, 'up', '-d', '--no-deps', '--no-build', '--wait', *APP_SERVICES)
        invoke(config, 'up', '-d', '--no-deps', '--no-build', '--wait', 'frontend', frontend=True)
        config['transition'] = 'ready'
        write_private(STATE, config)
        print(f"Запущен профиль {config['profile']} / {config['hardware']}, очередь {config['mode']}. "
              f"Интерактивных слотов: {config['concurrency'] if config['profile'] == 'parallel' else 'стандартный бюджет B1'}. "
              f"Интерфейс: http://localhost:{config['frontend_port']}")
        print('База, Redis, Qdrant, MinIO, Ollama и их volumes не пересоздавались. '
              'Модели и сессии сохранены. Режим parallel является диагностическим.')
    elif args.action == 'status':
        print(json.dumps({k: config[k] for k in ('profile', 'hardware', 'mode', 'concurrency', 'network',
                                                'backend_port', 'frontend_port', 'actual_backend_mode',
                                                'actual_backend_profile')}, ensure_ascii=False))
        if (config['profile'], config['mode']) != (config['actual_backend_profile'], config['actual_backend_mode']):
            print('Сохранённый профиль отличается от работающего backend. Возможно, запуск выполнен другим скриптом; '
                  'status не меняет работающие контейнеры.')
        print('Активные задания/эксперименты: ' + docker('exec', config['backend_id'], 'python', '-c', ACTIVE_SCRIPT))
        invoke(config, 'ps')
        invoke(config, 'ps', frontend=True)
    elif args.action == 'logs':
        invoke(config, 'logs', '--tail=150', 'backend', 'worker', 'worker-batch', 'worker-control', 'worker-b1')
    elif args.action == 'gpu-check':
        print('Проверка доступности CUDA в worker; фактическое использование модели смотрите в RERANKER_READY и runtime.reranker_execution.')
        invoke(config, 'exec', '-T', 'worker', 'python', '-c', GPU_SCRIPT)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Запуск остановлен: {exc}', file=sys.stderr)
        print('Данные не удалялись. При сбое во время up часть приложений могла сменить профиль; '
              'проверьте status/logs и повторите ту же команду после устранения причины.', file=sys.stderr)
        raise SystemExit(1)
