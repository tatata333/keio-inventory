"""販売不振商品(除外候補)のテスト。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from keio_inventory.api.main_db import app

client = TestClient(app)


def test_slow_mover_list_exists():
    r = client.get("/api/v1/exclusion/slow-movers")
    assert r.status_code == 200
    items = r.json()["items"]
    # 商品があればスコア順にソートされている
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)


def test_slow_mover_detail():
    r = client.get("/api/v1/exclusion/slow-movers")
    items = r.json()["items"]
    if items:
        pid = items[0]["product_id"]
        d = client.get(f"/api/v1/exclusion/{pid}")
        assert d.status_code == 200
        body = d.json()
        assert "score" in body and "recent_demand" in body


def test_slow_mover_404():
    assert client.get("/api/v1/exclusion/99999").status_code == 404
