"""PostgreSQL バックエンド API の統合テスト。

前提: PostgreSQL 17 稼働 + スキーマ適用 + 結果永続化済み
      (db/apply_schema.py と jobs/run_pipeline_db.py を実行済み)。
接続情報は環境変数で指定します。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from keio_inventory.api.main_db import app
from keio_inventory.demo_catalog import CATALOG, PLACES

client = TestClient(app)

# 商品×店舗の期待レコード数（カタログが単一 source of truth）
EXPECT_ITEM_COUNT = len(CATALOG) * len(PLACES)


def test_health_db():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["storage"] == "postgresql"


def test_segments_from_db():
    r = client.get("/api/v1/segment/abc-xyz")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 8  # シード/SKU 8商品
    assert all("segment" in i for i in items)


def test_forecast_from_db():
    r = client.get("/api/v1/forecast/1/1")
    assert r.status_code == 200
    body = r.json()
    # 予測vs実績比較用: series は予測日付順で、各要素に forecast と actual_qty を持つ
    assert "product_name" in body
    assert 1 <= len(body["series"]) <= 30
    for s in body["series"]:
        assert "target_date" in s
        assert "forecast_p50" in s
        assert "actual_qty" in s  # 実績売上(sku_daily_sales)と突き合わせる


def test_safety_stock_from_db():
    r = client.get("/api/v1/inventory/safety-stock")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == EXPECT_ITEM_COUNT  # カタログ商品数 x 店舗数
    assert all(i["mode"] in ("pos_only", "full") for i in items)
    # 適正在庫量（target_inventory）が返却され、発注点(ROP)と同値であること
    assert all("target_inventory" in i for i in items)
    assert all(i["target_inventory"] == i["reorder_point"] for i in items)


def test_recommendations_from_db():
    r = client.get("/api/v1/order/recommendation")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == EXPECT_ITEM_COUNT  # カタログ商品数 x 店舗数（重複ではなく場所別）
    # 各レコードに place_name と segment がある
    assert all("place_name" in i and i["place_name"] for i in items)
    assert all("segment" in i for i in items)
    # 商品ごとに2店舗分あるが、同一 (product_id, place_id) の重複はない
    keys = [(i["product_id"], i["place_id"]) for i in items]
    assert len(keys) == len(set(keys))

def test_recommendation_sort_from_db():
    r = client.get("/api/v1/order/recommendation?sort=qty_desc")
    items = r.json()["items"]
    qs = [i["recommended_qty"] for i in items]
    assert qs == sorted(qs, reverse=True)


def test_recommendation_approve_from_db():
    r = client.get("/api/v1/order/recommendation")
    items = r.json()["items"]
    if items:
        item = items[0]
        rid = item["id"]
        resp = client.post(f"/api/v1/order/recommendation/{rid}/approve")
        # 既に approved の場合も or 他の状態でも 200 を返す（冪等）
        assert resp.status_code == 200


def test_anomaly_alerts_from_db():
    r = client.get("/api/v1/anomaly/alerts")
    assert r.status_code == 200
    assert len(r.json()["items"]) >= 1


def test_settings_db_mode():
    r = client.get("/api/v1/settings")
    body = r.json()
    assert body["inventory_mode"] in ("pos_only", "full")


def test_dashboard_kpi_from_db():
    """KPI集計エンドポイントがバッチ結果(result_*)を集計して返す。"""
    r = client.get('/api/v1/dashboard/kpi')
    assert r.status_code == 200
    d = r.json()
    # 必須キー
    assert 'inventory' in d and 'order' in d and 'anomaly' in d and 'segment' in d
    assert d['inventory']['mode'] in ('pos_only', 'full')
    # バッチで永続化済みなら安全在庫件数が 1 以上
    assert d['inventory']['total_items_safety_stock'] >= 1
    # 異常・セグメントは空でも耐える
    assert isinstance(d['anomaly']['by_type'], dict)
    assert isinstance(d['segment']['by_segment'], dict)


def test_dashboard_html_served():
    """ダッシュボードHTML(Chart.js)が 200 で配信される。"""
    r = client.get('/')
    assert r.status_code == 200
    assert 'chart.umd' in r.text
    assert '在庫最適化' in r.text
    # /dashboard エイリアス
    assert client.get('/dashboard').status_code == 200



def test_dashboard_history_from_db():
    """月別/日別/年別 の在庫指標時系列集計エンドポイントが動作する。"""
    for p in ("day", "month", "year"):
        r = client.get(f'/api/v1/dashboard/history?period={p}&lookback=90')
        assert r.status_code == 200, f"period={p} status={r.status_code}"
        body = r.json()
        assert body["period"] == p
        assert isinstance(body["items"], list)
        if body["items"]:
            first = body["items"][0]
            for key in ("label", "days", "safety_avg",
                        "target_inventory_avg", "inventory_turnover_annual"):
                assert key in first, f"missing {key}"
    # 不正な period は 422
    r = client.get('/api/v1/dashboard/history?period=week')
    assert r.status_code == 422


def test_dashboard_history_target_inventory_fallback():
    """過去日（target_inventory 列追加前）の適正在庫量欠落(=0)を reorder_point で補正する。

    適正在庫量(=ROP)は常に非負であるはず。target_inventory が NULL の過去日でも、
    COALESCE により reorder_point を代用して 0 表示を防ぐ。
    """
    r = client.get('/api/v1/dashboard/history?period=day&lookback=180')
    assert r.status_code == 200
    items = r.json()["items"]
    # いずれの日も適正在庫量平均が 0 未満でない＆ 0 より大きい行がある
    non_zero = [it for it in items if it["target_inventory_avg"] and it["target_inventory_avg"] > 0]
    assert non_zero, "適正在庫量が全期間 0 になってはいけない（欠落補正失敗）"
    # 補正が効いている場合、全行が 0 超（reorder_point 由来）であること
    assert all(it["target_inventory_avg"] > 0 for it in items), "適正在庫量に 0 の行が残っている"


def test_dashboard_drilldown_from_db():
    """セグメント・カテゴリ・店舗のドリルダウン（FR-7-2）。"""
    r = client.get('/api/v1/dashboard/drilldown')
    assert r.status_code == 200
    body = r.json()
    assert body["filters"]["segments"]  # セグメント一覧（AX/BY/CY...）
    assert body["filters"]["categories"]  # カテゴリ一覧
    assert body["filters"]["places"]  # 店舗一覧（id/name）
    assert len(body["rows"]) == EXPECT_ITEM_COUNT  # カタログ商品数 x 店舗数
    assert body["summary"]["item_count"] == EXPECT_ITEM_COUNT
    for row in body["rows"]:
        assert "segment" in row and "category" in row and "place_name" in row
    # セグメント絞り込み
    ax = client.get('/api/v1/dashboard/drilldown?segment=AX')
    assert ax.status_code == 200
    ax_rows = ax.json()["rows"]
    assert len(ax_rows) > 0
    assert all(x["segment"] == "AX" for x in ax_rows)
    # 店舗絞り込み
    p1 = client.get('/api/v1/dashboard/drilldown?place_id=1')
    assert p1.status_code == 200
    p1_rows = p1.json()["rows"]
    assert p1_rows and all(x["place_id"] == 1 for x in p1_rows)