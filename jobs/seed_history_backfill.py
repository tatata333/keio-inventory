"""在庫指標の時系列グラフ用 履歴バックフィル（デモ用・トレンド対応）。

result_safety_stock は本来「日次バッチ(jobs/run_pipeline_db)が毎日実行される」ことで
過去分が蓄積される。デモ環境では過去分が無いため、時系列グラフが1点しか表示されない。

本スクリプトは過去 days 日分の calc_date に対して run_pipeline_db.main を実行し、
各 snap日(cd)時点までのPOS需要でエンジンを再計算して、日次スナップショットを生成・蓄積する。
これにより、需要の季節変動・トレンドに応じた安全在庫・適正在庫の時系列トレンドが得られる。

使い方:
    python -m jobs.seed_history_backfill            # 過去90日（デフォルト）
    python -m jobs.seed_history_backfill --days 30  # 過去30日
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from jobs.run_pipeline_db import main as run_pipeline


def backfill(days: int = 90, end_date: date | None = None) -> list[date]:
    """過去 days 日分の日次スナップショットを result_* に蓄積する。

    各 calc_date について end_date=calc_date でエンジンを回し、その日時点までの
    POS需要から安全在庫・適正在庫を算出する（時系列トレンドを出す）。
    """
    end = end_date or date.today()
    start = end - timedelta(days=days - 1)
    written = []
    for i in range(days):
        cd = start + timedelta(days=i)
        run_pipeline(cd, end_date=cd)  # calc_date 時点までのPOSで計算 → 該当日を書直し
        written.append(cd)
        if (i + 1) % 10 == 0 or i == days - 1:
            print(f"[backfill] {i + 1}/{days} done (up to {cd})")
    return written


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="在庫指標の時系列グラフ用に過去日分のスナップショットを生成（トレンド対応）")
    p.add_argument("--days", type=int, default=90, help="過去日数（デフォルト90）")
    args = p.parse_args()
    written = backfill(days=args.days)
    print(f"[+] backfilled {len(written)} calc_date snapshots (day range): "
          f"{written[0]} .. {written[-1]}")
