"""daily_inventory_pipeline DAG

日次バッチ: 需要予測 -> 安全在庫 -> 推奨発注 -> 異常検知
設計書 06_batch の daily_forecast / daily_order / daily_anomaly / daily_stock 相当。
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


WITH_ = dict(
    retries=3,
    retry_delay=pendulum.duration(minutes=10),
    owner="data-eng",
)


def _op(dag, id_, callable):
    return PythonOperator(task_id=id_, python_callable=callable, provide_context=True, dag=dag)


with DAG(
    dag_id="daily_inventory_pipeline",
    schedule="30 3 * * *",
    start_date=pendulum.datetime(2026, 8, 1, tz="Asia/Tokyo"),
    catchup=False,
    default_args=WITH_,
    description="日次: 需要予測・安全在庫・推奨発注・異常検知",
    tags=["daily", "inventory"],
) as dag:
    forecast = _op(dag, "daily_forecast", tasks.run_forecast)
    safety = _op(dag, "daily_safety_stock", tasks.run_safety_stock)
    order = _op(dag, "daily_order", tasks.run_order_recommendation)
    anomaly = _op(dag, "daily_anomaly", tasks.run_anomaly)

    forecast >> safety >> order
    forecast >> anomaly
