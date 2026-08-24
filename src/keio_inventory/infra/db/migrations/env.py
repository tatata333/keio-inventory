"""Alembic migration environment.

DB 接続URL は環境変数 (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD) から構築する。
モデルは keio_inventory.infra.db.models の Base を autogenerate に使用。
"""

import configparser
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

import sys

# 必要なら src を sys.path へ追加（alembic 実行時の cwd が sample/ でない場合に備える）
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from keio_inventory.infra.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except (KeyError, configparser.ParsingError):
        # alembic.ini に logging セクションが無い場合でも起動可能にする
        pass

DEFAULTS = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "keio_inventory",
    "DB_USER": "postgres",
    "DB_PASSWORD": "postgres",
}


def _url() -> str:
    def cfg(k: str) -> str:
        return os.environ.get(k, DEFAULTS[k])

    return (
        f"postgresql+psycopg2://{cfg('DB_USER')}:{cfg('DB_PASSWORD')}"
        f"@{cfg('DB_HOST')}:{cfg('DB_PORT')}/{cfg('DB_NAME')}"
    )


config.set_main_option("sqlalchemy.url", _url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL only, no DB needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
