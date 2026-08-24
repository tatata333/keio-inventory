"""weekly_segment DAG

週次: ABC-XYZ 分析を再計算し、安全在庫の発注点・サービスレベルへ反映する基盤。
設計書 06_batch の weekly_segment 相当。
"""
from __future__ import annotations

import os
import sys

# DAG プロセッサは PYTHONPATH を引き継がないため、src/jobs を明示的に sys.path へ追加
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_SRC, _ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pendulum

from airflow import DAG
from airflow.operators.python import PythonOperator

from jobs import tasks


WITH_ = dict(retries=2, retry_delay=pendulum.duration(minutes=15), owner="data-eng")


with DAG(
    dag_id="weekly_segment",
    schedule="0 1 * * 0",
    start_date=pendulum.datetime(2026, 8, 1, tz="Asia/Tokyo"),
    catchup=False,
    default_args=WITH_,
    description="週次: ABC-XYZ 再計算",
    tags=["weekly", "segment"],
) as dag:
    segment = PythonOperator(
        task_id="weekly_segment_abc_xyz", python_callable=tasks.run_segment,
        provide_context=True, dag=dag,
    )
    restock = PythonOperator(
        task_id="weekly_safety_stock", python_callable=tasks.run_safety_stock,
        provide_context=True, dag=dag,
    )
    segment >> restock
