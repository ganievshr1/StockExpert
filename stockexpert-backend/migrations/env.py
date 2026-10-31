"""Run schema upgrades on one managed connection, serialized in PostgreSQL."""
from alembic import context
from sqlalchemy import text
from app.db import Base, engine


def migrate(connection):
    with connection.begin():
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(731903900)'))
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError('Use online migrations: the initial revision inspects existing tables.')
elif context.config.attributes.get('connection') is not None:
    migrate(context.config.attributes['connection'])
else:
    with engine.connect() as connection:
        migrate(connection)

