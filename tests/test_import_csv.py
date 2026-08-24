"""CSV取込のテスト。

前提: DBスキーマ適用済み。接続情報は環境変数で指定。
"""
from __future__ import annotations

from jobs.import_data import import_csv_text


def test_import_pos_upsert_idempotent():
    csv_text = "sales_date,product_id,place_id,qty_sold,amount\n2026-08-01,1,1,5,4500\n"
    r1 = import_csv_text("pos", csv_text)
    assert r1["inserted"] == 1
    # 再入(冪等)でも件数は増えない
    r2 = import_csv_text("pos", csv_text)
    assert r2["inserted"] == 1
    assert r2["skipped"] == 0


def test_import_inventory():
    csv_text = "inventory_date,product_id,place_id,on_hand_qty,allocated_qty,available_qty\n2026-08-24,1,1,30,0,30\n"
    r = import_csv_text("inventory", csv_text)
    assert r["inserted"] == 1
    assert r["errors"] == []


def test_import_purchase():
    csv_text = "po_date,product_id,place_id,order_qty,received_qty,expected_date\n2026-08-14,1,1,50,50,2026-08-21\n"
    r = import_csv_text("purchase", csv_text)
    assert r["inserted"] == 1


def test_import_bad_type():
    import pytest
    with pytest.raises(ValueError):
        import_csv_text("bogus", "a,b\n1,2\n")
