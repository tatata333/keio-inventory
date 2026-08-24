
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from keio_inventory.domain.engine import InventoryEngine

app = FastAPI(
    title="Keio Atman - Inventory Optimization Platform (Sample)",
    version="0.1.0",
    description="2605-22 需要予測/ABC-XYZ/動的安全在庫/異常検知/推奨発注 API",
)

# Build demo engine + run "batch" pipeline once at startup.
_engine = InventoryEngine()
_engine.run_pipeline()

PLACES = {p["id"]: p["name"] for p in _engine.places}
PRODUCTS = {p["id"]: p["name"] for p in _engine.products}


def _product(pid: int) -> dict:
    for p in _engine.products:
        if p["id"] == pid:
            return p
    raise HTTPException(status_code=404, detail=f"product {pid} not found")


class RecommendUpdate(BaseModel):
    recommended_qty: float = Field(ge=0)
    note: str | None = None


# ---------------------------------------------------------------- auth
@app.get("/api/v1/health")
def health():
    return {"status": "ok", "engine": "ready"}


@app.post("/api/v1/auth/login")
def login(payload: dict):
    # Demo auth: any non-empty credentials returns a token.
    if not payload.get("username"):
        raise HTTPException(status_code=400, detail="username required")
    return {
        "access_token": "demo-token",
        "token_type": "bearer",
        "expires_in": 1800,
        "role": "buyer",
    }


# ---------------------------------------------------------------- forecast
@app.get("/api/v1/forecast/{product_id}/{place_id}")
def get_forecast(product_id: int, place_id: int):
    _product(product_id)
    if place_id not in PLACES:
        raise HTTPException(404, f"place {place_id} not found")
    key = (product_id, place_id)
    fc = _engine.forecasts.get(key)
    if not fc:
        raise HTTPException(404, "forecast not found")
    return {
        "product_id": product_id,
        "place_id": place_id,
        "product_name": PRODUCTS.get(product_id),
        "model_name": fc.model_name,
        "series": [
            {"target_date": f"2026-06-{i+1:02d}",
             "forecast_p50": fc.p50[i],
             "forecast_p80": fc.p80[i],
             "forecast_p95": fc.p95[i],
             "actual_qty": None}
            for i in range(min(10, len(fc.p50)))
        ],
    }


# ---------------------------------------------------------------- safety stock
@app.get("/api/v1/inventory/safety-stock")
def safety_stock_list(segment: str | None = Query(default=None), mode: str | None = None):
    items = []
    for (pid, plid), ss in _engine.safety_stock.items():
        seg = _engine.segments[pid]
        if segment and seg.segment != segment:
            continue
        if mode and ss.mode != mode:
            continue
        items.append({
            "product_id": pid, "product_name": PRODUCTS.get(pid), "place_id": plid,
            "abc": seg.abc_class, "xyz": seg.xyz_class, "mode": ss.mode,
            "avg_demand": ss.avg_demand, "demand_std": ss.demand_std,
            "lead_time_days": ss.lead_time_days, "safety_stock": ss.safety_stock,
            "reorder_point": ss.reorder_point,
            "target_inventory": ss.target_inventory, "order_qty": ss.order_qty,
            "service_level": ss.service_level,
        })
    return {"items": items}


@app.get("/api/v1/inventory/safety-stock/{product_id}")
def safety_stock_detail(product_id: int):
    _product(product_id)
    out = []
    for (pid, plid), ss in _engine.safety_stock.items():
        if pid != product_id:
            continue
        seg = _engine.segments[pid]
        out.append({
            "product_id": pid, "product_name": PRODUCTS.get(pid), "place_id": plid,
            "segment": seg.segment, "mode": ss.mode,
            "safety_stock": ss.safety_stock, "reorder_point": ss.reorder_point,
            "target_inventory": ss.target_inventory, "order_qty": ss.order_qty,
        })
    if not out:
        raise HTTPException(404, "no safety stock for product")
    return {"items": out}


# ---------------------------------------------------------------- recommendation
@app.get("/api/v1/order/recommendation")
def recommendations(status: str | None = None):
    items = []
    for (pid, plid), rec in _engine.recommendations.items():
        if status and rec.status != status:
            continue
        items.append({
            "id": pid * 100 + plid,
            "product_id": pid, "product_name": PRODUCTS.get(pid), "place_id": plid,
            "forecast_demand": rec.forecast_demand,
            "safety_stock": rec.safety_stock,
            "on_hand_qty": rec.on_hand_qty,
            "recommended_qty": rec.recommended_qty,
            "status": rec.status,
        })
    return {"items": items, "next_cursor": None}


@app.put("/api/v1/order/recommendation/{item_id}")
def adjust_recommendation(item_id: int, body: RecommendUpdate):
    target = _find_rec(item_id)
    updated = _engine.order.adjust(target.rec, body.recommended_qty)
    _store_rec(item_id, updated)
    return {"status": updated.status, "recommended_qty": updated.recommended_qty,
            "note": body.note}


@app.post("/api/v1/order/recommendation/{item_id}/approve")
def approve_recommendation(item_id: int):
    target = _find_rec(item_id)
    updated = _engine.order.approve(target.rec)
    _store_rec(item_id, updated)
    return {"status": updated.status, "recommended_qty": updated.recommended_qty}


@app.post("/api/v1/order/recommendation/{item_id}/reject")
def reject_recommendation(item_id: int):
    target = _find_rec(item_id)
    updated = _engine.order.reject(target.rec)
    _store_rec(item_id, updated)
    return {"status": updated.status}


def _find_rec(item_id: int):
    pid, plid = divmod(item_id, 100)
    key = (pid, plid)
    if key not in _engine.recommendations:
        # item_id built as pid*100+plid; last place may be 2-digit
        for (p, pl), rec in _engine.recommendations.items():
            if p * 100 + pl == item_id:
                return type("T", (), {"rec": rec, "pid": p, "plid": pl})()
        raise HTTPException(404, "recommendation not found")
    return type("T", (), {"rec": _engine.recommendations[key], "pid": pid, "plid": plid})()


def _store_rec(item_id: int, rec):
    for (p, pl) in list(_engine.recommendations.keys()):
        if p * 100 + pl == item_id:
            _engine.recommendations[(p, pl)] = rec
            return
    raise HTTPException(404, "recommendation not found")


# ---------------------------------------------------------------- anomaly
@app.get("/api/v1/anomaly/alerts")
def alerts(status: str | None = None, severity: str | None = None):
    items = []
    for a in _engine.alerts:
        if status and a["status"] != status:
            continue
        if severity and a["severity"] != severity:
            continue
        items.append(a)
    return {"items": items}


@app.post("/api/v1/anomaly/alerts/{alert_id}/ack")
def alert_ack(alert_id: int):
    return _set_alert_status(alert_id, "ack")


@app.post("/api/v1/anomaly/alerts/{alert_id}/resolve")
def alert_resolve(alert_id: int):
    return _set_alert_status(alert_id, "done")


def _set_alert_status(alert_id: int, new_status: str):
    for a in _engine.alerts:
        if a["id"] == alert_id:
            a["status"] = new_status
            return {"id": alert_id, "status": new_status}
    raise HTTPException(404, "alert not found")


# ---------------------------------------------------------------- segment
@app.get("/api/v1/segment/abc-xyz")
def segment_list(abc: str | None = None, xyz: str | None = None):
    items = []
    for pid, s in _engine.segments.items():
        if abc and s.abc_class != abc:
            continue
        if xyz and s.xyz_class != xyz:
            continue
        items.append({
            "product_id": pid, "product_name": PRODUCTS.get(pid),
            "abc": s.abc_class, "xyz": s.xyz_class, "segment": s.segment,
            "sales_amount": s.sales_amount, "cv": round(s.cv, 3),
        })
    return {"items": items}


# ---------------------------------------------------------------- dashboard / settings
@app.get("/api/v1/dashboard/kpi")
def kpi():
    segs = _engine.segments.values()
    turnover = _engine.estimated_turnover()
    open_alerts = sum(1 for a in _engine.alerts if a["status"] == "open")
    pending = sum(1 for r in _engine.recommendations.values() if r.status == "pending")
    return {
        "inventory_turnover": round(turnover, 2),
        "stockout_rate_est": round(1 - 0.95, 3),
        "open_anomaly_alerts": open_alerts,
        "pending_recommendations": pending,
        "by_segment": {s.segment: round(s.sales_amount, 0) for s in segs},
    }


@app.get("/api/v1/settings")
def settings():
    return {
        "service_level": _engine.service_level,
        "inventory_enabled": _engine.has_inventory,
        "inventory_mode": "pos_only" if not _engine.has_inventory else "full",
        "horizon_days": 14,
    }
