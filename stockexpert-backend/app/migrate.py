"""Checked upgrade without dropping data or modifying existing credentials."""
import sys
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError


def main():
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    try:
        command.upgrade(config, 'head')
    except OperationalError as exc:
        code = getattr(getattr(exc, 'orig', None), 'sqlstate', None)
        if code == '28P01' or 'password authentication failed' in str(exc):
            print('PostgreSQL отклонил пароль. Существующий том сохраняет старый пароль. '
                  'Верните исходный .env; изменение POSTGRES_PASSWORD не меняет пароль роли в базе. '
                  'Не удаляйте тома. Инструкция: UPDATE_3.9.0.md.', file=sys.stderr)
        else:
            print('База недоступна. Проверьте контейнер postgres и параметры DB_HOST/DB_PORT. '
                  'Миграция остановлена, данные не удалялись.', file=sys.stderr)
        return 1
    print('Схема обновлена до 0009. Существующие расчёты и пароли сохранены; добавлен метод H1 в основной движок сессий.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
