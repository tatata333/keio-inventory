"""汎用 CSV データ取込

POS(販売実績) / 仕入 / 在庫 の3種のCSVを、データベースへ取り込む。

使い方（CLI）:
  python -m jobs.import_data --type pos   --file path.csv
  python -m jobs.import_data --type purchase --file path.csv
  python -m jobs.import_data --type inventory --file path.csv

CSVの列は、各タイプの「標準ヘッダ」を認識します（大小・空白・BOM を無視）。
実データの形式に合わせ、ヘッダ名の別名（別名マップ）を簡単に拡張できます。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import os
import sys

from keio_inventory.infra.db.repository import InventoryRepository
from keio_inventory.infra.db.session import SessionLocal


# 各タイプの「標準ヘッダ」と、DBカラムへの対応
TYPE_COLUMNS = {
    # 販売実績 (SkuDailySales)
    "pos": {
        "pk": ["sales_date", "product_id", "place_id"],
        "cols": {
            "sales_date": "sales_date",
            "product_id": "product_id",
            "place_id": "place_id",
            "qty_sold": "qty_sold",
            "amount": "amount",
        },
        "table": "sku_daily_sales",
    },
    # 仕入実績 (PurchaseHistory)
    "purchase": {
        "pk": ["po_date", "product_id", "place_id"],
        "cols": {
            "po_date": "po_date",
            "product_id": "product_id",
            "place_id": "place_id",
            "order_qty": "order_qty",
            "received_qty": "received_qty",
            "expected_date": "expected_date",
        },
        "table": "purchase_history",
    },
    # 在庫実績 (InventoryDaily)
    "inventory": {
        "pk": ["inventory_date", "product_id", "place_id"],
        "cols": {
            "inventory_date": "inventory_date",
            "product_id": "product_id",
            "place_id": "place_id",
            "on_hand_qty": "on_hand_qty",
            "allocated_qty": "allocated_qty",
            "available_qty": "available_qty",
        },
        "table": "inventory_daily",
    },
}

# 別名マップ（実データのヘッダ名違いに柔軟対応。必要に応じて追加）
ALIASES = {
    "sales_date": ["sales_date", "salesdate", "日付", "販売日", "sale_day"],
    "product_id": ["product_id", "productid", "sku", "sku_code", "商品id", "商品コード"],
    "place_id": ["place_id", "placeid", "store", "store_code", "店舗id", "店舗コード"],
    "qty_sold": ["qty_sold", "qty", "quantity", "数量", "売上数", "販売数量"],
    "amount": ["amount", "売上金額", "金額", "sales_amount"],
    "po_date": ["po_date", "podate", "発注日", "注文日", "order_date"],
    "order_qty": ["order_qty", "orderqty", "発注数量", "注文数量"],
    "received_qty": ["received_qty", "received", "入荷数量", "受入数量"],
    "expected_date": ["expected_date", "入荷予定日", "予定日"],
    "inventory_date": ["inventory_date", "inv_date", "在庫日", "在庫日付"],
    "on_hand_qty": ["on_hand_qty", "onhand", "手持ち在庫", "在庫数量"],
    "allocated_qty": ["allocated_qty", "割当数量", "引当数量"],
    "available_qty": ["available_qty", "available", "有効在庫", "利用可能在庫"],
}


def _normalize(s: str) -> str:
    """ヘッダ名の正規化（BOM・空白・大文字小文字を除去）。"""
    if s is None:
        return ""
    return s.lstrip("\ufeff").strip().lower().replace(" ", "")


def _resolve_header(header: list[str]) -> tuple[dict | None, str]:
    """CSVヘッダ行から、DBカラム名へのマッピングを決定。"""
    for _type, spec in TYPE_COLUMNS.items():
        mapping = {}
        ok = True
        for std_col in spec["cols"]:
            found = None
            for h in header:
                nh = _normalize(h)
                if nh in (_normalize(a) for a in ALIASES.get(std_col, [std_col])):
                    found = h
                    break
            if found is not None:
                mapping[std_col] = found
            else:
                # 必須の主キー列が無ければ、このタイプとしては不成立
                if std_col in spec["pk"]:
                    ok = False
                    break
        if ok:
            return mapping, _type
    return None, ""


def _to_type(value: str, kind: str):
    value = (value or "").strip()
    if value == "":
        return None
    if kind == "int":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind == "date":
        # YYYY-MM-DD / YYYY/MM/DD / MM/DD/YYYY 等
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
            try:
                return datetime.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"date parse失敗: {value!r}")
    return value


def import_csv_text(data_type: str, text: str) -> dict:
    """CSV文字列をDBへ取り込む。UPSERT（冪等）。"""
    if data_type not in TYPE_COLUMNS:
        raise ValueError(f"不正なタイプ: {data_type}（pos / purchase / inventory）")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"rows": 0, "skipped": 0, "errors": []}

    header = rows[0]
    mapping, resolved = _resolve_header(header)
    if mapping is None:
        raise ValueError(f"ヘッダを認識できませんでした。先頭行: {header}")

    spec = TYPE_COLUMNS[data_type]
    inserted = 0
    skipped = 0
    errors = []

    db = SessionLocal()
    try:
        repo = InventoryRepository(db)
        for i, row in enumerate(rows[1:]):
            if not row or all((c or "").strip() == "" for c in row):
                skipped += 1
                continue
            record = {}
            try:
                for std_col, hdr in mapping.items():
                    idx = header.index(hdr)
                    raw = row[idx] if idx < len(row) else ""
                    record[std_col] = raw
                ok = repo.upsert_transaction(data_type, record)
                if ok:
                    inserted += 1
            except Exception as e:
                errors.append({"line": i + 2, "error": str(e)})
                skipped += 1
        db.commit()
    finally:
        db.close()
    return {"rows": len(rows) - 1, "inserted": inserted, "skipped": skipped, "errors": errors}


def import_csv_file(data_type: str, path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return import_csv_text(data_type, f.read())


def main():
    ap = argparse.ArgumentParser(description="CSV取込")
    ap.add_argument("--type", required=True, choices=["pos", "purchase", "inventory"])
    ap.add_argument("--file", required=True, help="CSVファイルパス")
    args = ap.parse_args()
    result = import_csv_file(args.type, args.file)
    print(result)


if __name__ == "__main__":
    main()
