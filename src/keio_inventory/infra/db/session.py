from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DEFAULTS = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "keio_inventory",
    "DB_USER": "postgres",
    "DB_PASSWORD": "postgres",
}


def _cfg(key: str) -> str:
    return os.environ.get(key, DEFAULTS[key])


def build_engine():
    # 環境変数 DATABASE_URL があればそれを使用（SQLite または PostgreSQL）
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # SQLite の相対パスを絶対化しやすいようそのまま使う
        if db_url.startswith("sqlite"):
            return create_engine(
                db_url, connect_args={"check_same_thread": False}, future=True
            )
        return create_engine(db_url, pool_pre_ping=True, future=True)
    # 従来の PostgreSQL 環境変数
    url = (
        f"postgresql+psycopg2://{_cfg('DB_USER')}:{_cfg('DB_PASSWORD')}"
        f"@{_cfg('DB_HOST')}:{_cfg('DB_PORT')}/{_cfg('DB_NAME')}"
    )
    return create_engine(url, pool_pre_ping=True, future=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
