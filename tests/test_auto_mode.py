"""在庫データ蓄積に応じたモード自動切替のテスト

前提: PostgreSQL 稼働 + apply_schema + seed_inventory 実行済み。
接続情報は環境変数で指定。
"""
from __future__ import annotations

from keio_inventory.infra.db.repository import InventoryRepository
from keio_inventory.infra.db.session import SessionLocal
from jobs import tasks


def test_accumulation_status_when_seeded():
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        status = repo.inventory_accumulation_status(target_days=14)
        assert status["covered_pairs"] > 0
        assert status["coverage"] >= 0.8
    finally:
        db.close()


def test_auto_mode_switches_to_full_when_data_fresh():
    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        status = repo.inventory_accumulation_status(target_days=14)
        has, on_hand = tasks._resolve_mode(repo)
        fresh_and_covered = (
            status["coverage"] >= tasks.ACCUM_PAIR_COVERAGE_THRESHOLD
            and (status["days_since_latest"] or 0) <= tasks.ACCUM_FRESHNESS_DAYS
        )
        if fresh_and_covered:
            assert has is True
            assert len(on_hand) > 0
        else:
            assert has is False
    finally:
        db.close()
