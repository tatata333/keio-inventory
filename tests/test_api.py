from fastapi.testclient import TestClient

from keio_inventory.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_segments():
    r = client.get("/api/v1/segment/abc-xyz")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    assert all(i["segment"] for i in items)


def test_forecast():
    r = client.get("/api/v1/forecast/1/1")
    assert r.status_code == 200
    series = r.json()["series"]
    assert len(series) == 10
    assert all(s["forecast_p50"] <= s["forecast_p95"] for s in series)


def test_safety_stock_mode_pos_only():
    r = client.get("/api/v1/inventory/safety-stock")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(i["mode"] == "pos_only" for i in items)


def test_recommendation_workflow():
    r = client.get("/api/v1/order/recommendation")
    first = r.json()["items"][0]
    fid = first["id"]
    # adjust
    r = client.put(f"/api/v1/order/recommendation/{fid}", json={"recommended_qty": 30})
    assert r.status_code == 200 and r.json()["status"] == "adjusted"
    # approve
    r = client.post(f"/api/v1/order/recommendation/{fid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_anomaly_ack_and_resolve():
    r = client.get("/api/v1/anomaly/alerts")
    items = r.json()["items"]
    if items:
        aid = items[0]["id"]
        assert client.post(f"/api/v1/anomaly/alerts/{aid}/ack").json()["status"] == "ack"
        assert client.post(f"/api/v1/anomaly/alerts/{aid}/resolve").json()["status"] == "done"


def test_kpi_and_settings():
    assert client.get("/api/v1/dashboard/kpi").status_code == 200
    s = client.get("/api/v1/settings").json()
    assert s["inventory_mode"] == "pos_only"


def test_404_unknown_product():
    assert client.get("/api/v1/forecast/9999/1").status_code == 404
