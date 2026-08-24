"""異常アラートの一覧・詳細・ステータス操作のテスト。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from keio_inventory.api.main_db import app

client = TestClient(app)


def test_alerts_list():
    r = client.get("/api/v1/anomaly/alerts")
    assert r.status_code == 200
    assert "items" in r.json()


def test_alerts_filter():
    r = client.get("/api/v1/anomaly/alerts?status=open&severity=high")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["status"] == "open"


def test_alert_detail():
    r = client.get("/api/v1/anomaly/alerts")
    items = r.json().get("items", [])
    if items:
        aid = items[0]["id"]
        d = client.get(f"/api/v1/anomaly/alerts/{aid}")
        assert d.status_code == 200
        assert "recent_demand" in d.json()


def test_alert_status_workflow():
    r = client.get("/api/v1/anomaly/alerts?status=open")
    items = r.json().get("items", [])
    if items:
        aid = items[0]["id"]
        assert client.post(f"/api/v1/anomaly/alerts/{aid}/ack").json()["status"] == "ack"
        assert client.post(f"/api/v1/anomaly/alerts/{aid}/resolve").json()["status"] == "done"


def test_alert_404():
    assert client.get("/api/v1/anomaly/alerts/999999").status_code == 404
