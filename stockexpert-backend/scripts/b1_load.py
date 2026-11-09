#!/usr/bin/env python3
"""CLI for the same real B1 experiment API used by the service UI (Python 3.11+)."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

import httpx


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8002/api/v1', help='StockExpert API prefix')
    p.add_argument('--username', default=os.getenv('STOCKEXPERT_USERNAME'))
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('config'); sub.add_parser('list')
    create = sub.add_parser('create')
    create.add_argument('--scenario', choices=['concurrent_10', 'sequential_10', 'single_user_parallel', 'confirmation'], required=True)
    create.add_argument('--diagnostic-timeout-seconds', type=int, default=120,
                        help='Diagnostic request deadline, 30–3600 s; confirmation stays at 120 s')
    create.add_argument('--base-session', required=True)
    create.add_argument('--requirements-session', required=True)
    create.add_argument('--background-session')
    create.add_argument('--title')
    for name in ['status', 'continue', 'cancel', 'wait', 'export']:
        command = sub.add_parser(name)
        command.add_argument('experiment_id', type=lambda value: str(UUID(value)))
        if name == 'export':
            command.add_argument('--format', choices=['json', 'csv', 'zip'], default='zip')
            command.add_argument('--output', type=Path)
    return p


def checked(response):
    if response.is_error:
        try:
            detail = response.json().get('detail', response.status_code)
        except ValueError:
            detail = response.status_code
        raise RuntimeError(f'HTTP {response.status_code}: {detail}')
    return response


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with httpx.Client(base_url=args.url.rstrip('/') + '/', timeout=30, trust_env=False) as client:
            token = os.getenv('STOCKEXPERT_TOKEN')
            if not token:
                username = args.username or input('Имя администратора: ')
                password = os.getenv('STOCKEXPERT_PASSWORD') or getpass.getpass('Пароль: ')
                token = checked(client.post('auth/token', data={'username': username, 'password': password})).json()['access_token']
            client.headers['Authorization'] = 'Bearer ' + token
            base = 'developer/b1'
            action = args.action
            if action in ('config', 'list'):
                result = checked(client.get(base + ('/config' if action == 'config' else '/experiments'))).json()
            elif action == 'create':
                payload = {'scenario': args.scenario, 'base_session_id': args.base_session,
                           'requirement_session_id': args.requirements_session,
                           'background_session_id': args.background_session, 'campaign_title': args.title,
                           'diagnostic_timeout_seconds': args.diagnostic_timeout_seconds}
                result = checked(client.post(base + '/experiments', json=payload)).json()
            else:
                path = base + '/experiments/' + args.experiment_id
                if action == 'export':
                    output = args.output or Path(f'b1-{args.experiment_id}.{args.format}')
                    response = checked(client.get(path + '/export', params={'format': args.format}))
                    # A second export cannot silently erase previous evidence.
                    with output.open('xb') as stream:
                        stream.write(response.content)
                    print(str(output.resolve()))
                    return 0
                if action in ('continue', 'cancel'):
                    result = checked(client.post(path + '/' + action)).json()
                elif action == 'wait':
                    while True:
                        result = checked(client.get(path)).json()
                        print(json.dumps({'status': result['status'], 'progress': result.get('progress'),
                                          'current_run': result.get('current_run')}, ensure_ascii=False), flush=True)
                        if result['status'] not in ('QUEUED', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'):
                            break
                        time.sleep(2)
                else:
                    result = checked(client.get(path)).json()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if isinstance(result, dict) and result.get('status') == 'AWAITING_PROFILE':
                return 3
            return 1 if isinstance(result, dict) and result.get('status') == 'FAILED' else 0
    except KeyboardInterrupt:
        print('Ожидание остановлено; серверный эксперимент продолжает работу. Для отмены используйте cancel.', file=sys.stderr)
        return 130
    except (httpx.HTTPError, RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f'B1: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
