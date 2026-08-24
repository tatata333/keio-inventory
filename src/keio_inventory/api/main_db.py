from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from keio_inventory.infra.db.models import Product, Place
from keio_inventory.infra.db.repository import InventoryRepository
from keio_inventory.infra.db.session import get_session
from keio_inventory.infra.db.models import AnomalyAlert

app = FastAPI(
    title="Keio Atman - Inventory Optimization (PostgreSQL-backed)",
    version="0.2.0",
    description="2605-22 需要予測/ABC-XYZ/動的安全在庫/異常検知/推奨発注 API (PostgreSQL永続化版)",
)

DATE = date.today()


def _repo(db: Session) -> InventoryRepository:
    return InventoryRepository(db)


@app.get("/api/v1/health")
def health(db: Session = Depends(get_session)):
    from keio_inventory.infra.db.session import engine as _eng
    return {"status": "ok", "storage": _eng.name, "calc_date": str(DATE)}


@app.get("/api/v1/segment/abc-xyz")
def segments(abc: str | None = None, xyz: str | None = None, db: Session = Depends(get_session)):
    repo = _repo(db)
    prods = {p.id: p.name for p in repo.list_products()}
    items = []
    for s in repo.latest_segments(DATE):
        if abc and s.abc_class != abc:
            continue
        if xyz and s.xyz_class != xyz:
            continue
        items.append({
            "product_id": s.product_id, "product_name": prods.get(s.product_id),
            "abc": s.abc_class, "xyz": s.xyz_class, "segment": (s.abc_class or "") + (s.xyz_class or ""),
            "sales_amount": float(s.sales_amount or 0), "cv": float(s.cv or 0),
        })
    return {"items": items}


@app.get("/api/v1/forecast/{product_id}/{place_id}")
def forecast(product_id: int, place_id: int, db: Session = Depends(get_session)):
    repo = _repo(db)
    prods = {p.id: p.name for p in repo.list_products()}
    rows = repo.forecast_with_actual(product_id, place_id, limit=30)
    if not rows:
        raise HTTPException(404, "forecast not found")
    return {
        "product_id": product_id, "place_id": place_id,
        "product_name": prods.get(product_id),
        "series": rows,
    }


@app.get("/api/v1/inventory/safety-stock")
def safety_stock(segment: str | None = None, mode: str | None = None, db: Session = Depends(get_session)):
    repo = _repo(db)
    prods = {p.id: p.name for p in repo.list_products()}
    seg_map = {s.product_id: ((s.abc_class or "") + (s.xyz_class or "")) for s in repo.latest_segments(DATE)}
    items = []
    for ss in repo.latest_safety_stock(DATE):
        seg = seg_map.get(ss.product_id, "")
        if segment and seg != segment:
            continue
        if mode and ss.mode != mode:
            continue
        items.append({
            "product_id": ss.product_id, "product_name": prods.get(ss.product_id),
            "place_id": ss.place_id, "segment": seg, "mode": ss.mode,
            "avg_demand": float(ss.avg_demand or 0), "demand_std": float(ss.demand_std or 0),
            "lead_time_days": float(ss.lead_time_days or 0), "safety_stock": float(ss.safety_stock or 0),
            "reorder_point": float(ss.reorder_point or 0),
            "target_inventory": float(getattr(ss, "target_inventory", None) or float(ss.reorder_point or 0)),
            "order_qty": float(ss.order_qty or 0),
            "service_level": float(ss.service_level or 0),
        })
    return {"items": items}


@app.get("/api/v1/order/recommendation")
def recommendations(status: str | None = None, sort: str | None = Query(None, pattern="^(qty_desc|qty_asc|demand_desc)$"),
                     db: Session = Depends(get_session)):
    repo = _repo(db)
    prods = {p.id: p.name for p in repo.list_products()}
    places = {p.id: p.place_name for p in repo.list_places()}
    seg_map = {s.product_id: ((s.abc_class or "") + (s.xyz_class or "")) for s in repo.latest_segments(DATE)}
    items = []
    for r in repo.latest_recommendations(DATE, status):
        items.append({
            "id": r.product_id * 100 + r.place_id,
            "product_id": r.product_id, "product_name": prods.get(r.product_id),
            "place_id": r.place_id, "place_name": places.get(r.place_id),
            "segment": seg_map.get(r.product_id, ""),
            "calc_date": str(r.calc_date),
            "forecast_demand": float(r.forecast_demand or 0),
            "safety_stock": float(r.safety_stock or 0),
            "on_hand_qty": float(r.on_hand_qty or 0),
            "recommended_qty": float(r.recommended_qty or 0),
            "status": r.status,
        })
    if sort == "qty_desc":
        items.sort(key=lambda x: x["recommended_qty"], reverse=True)
    elif sort == "qty_asc":
        items.sort(key=lambda x: x["recommended_qty"])
    elif sort == "demand_desc":
        items.sort(key=lambda x: x["forecast_demand"], reverse=True)
    return {"items": items, "next_cursor": None}


@app.post("/api/v1/order/recommendation/{item_id}/approve")
def approve(item_id: int, db: Session = Depends(get_session)):
    pid, plid = divmod(item_id, 100)
    repo = _repo(db)
    ok = repo.update_rec_status(pid, plid, DATE, "approved")
    if not ok:
        raise HTTPException(404, "recommendation not found")
    return {"status": "approved", "recommended_qty": None}


@app.post("/api/v1/order/recommendation/{item_id}/reject")
def reject(item_id: int, db: Session = Depends(get_session)):
    pid, plid = divmod(item_id, 100)
    repo = _repo(db)
    ok = repo.update_rec_status(pid, plid, DATE, "rejected")
    if not ok:
        raise HTTPException(404, "recommendation not found")
    return {"status": "rejected"}


@app.get("/api/v1/anomaly/alerts")
def alerts(status: str | None = None, severity: str | None = None,
           anomaly_type: str | None = None, db: Session = Depends(get_session)):
    """アラート一覧（フィルタ: 状態/重警度/種別）。"""
    repo = _repo(db)
    prods = {p.id: p.name for p in repo.list_products()}
    places = {p.id: p.place_name for p in repo.list_places()}
    items = []
    for a in repo.list_alerts(status=status, severity=severity, anomaly_type=anomaly_type):
        items.append({
            "id": a.id, "product_id": a.product_id, "product_name": prods.get(a.product_id),
            "place_id": a.place_id, "place_name": places.get(a.place_id),
            "anomaly_type": a.anomaly_type, "severity": a.severity,
            "detected_at": str(a.detected_at), "status": a.status,
            "recommended_action": a.recommended_action, "detail": a.detail,
        })
    return {"items": items}


@app.get("/api/v1/anomaly/alerts/{alert_id}")
def alert_detail(alert_id: int, db: Session = Depends(get_session)):
    """アラート詳細（対象商品・指標・直近需要）。"""
    repo = _repo(db)
    a = repo.get_alert(alert_id)
    if not a:
        raise HTTPException(404, "alert not found")
    prods = {p.id: p for p in repo.list_products()}
    places = {p.id: p.place_name for p in repo.list_places()}
    series = repo.daily_sales_series(days=30).get(a.product_id, [])
    p = prods.get(a.product_id)
    return {
        "id": a.id, "product_id": a.product_id, "product_name": p.name if p else None,
        "sku_code": p.sku_code if p else None,
        "place_id": a.place_id, "place_name": places.get(a.place_id),
        "anomaly_type": a.anomaly_type, "severity": a.severity,
        "detected_at": str(a.detected_at), "status": a.status,
        "recommended_action": a.recommended_action, "detail": a.detail,
        "recent_demand": [{"date": str(d), "qty": q} for d, q in series[-14:]],
    }


@app.post("/api/v1/anomaly/alerts/{alert_id}/ack")
def alert_ack(alert_id: int, db: Session = Depends(get_session)):
    if not _repo(db).update_alert_status(alert_id, "ack"):
        raise HTTPException(404, "alert not found")
    return {"status": "ack"}


@app.post("/api/v1/anomaly/alerts/{alert_id}/resolve")
def alert_resolve(alert_id: int, db: Session = Depends(get_session)):
    if not _repo(db).update_alert_status(alert_id, "done"):
        raise HTTPException(404, "alert not found")
    return {"status": "done"}



@app.get("/api/v1/settings")
def settings(db: Session = Depends(get_session)):
    repo = _repo(db)
    return {
        "service_level": repo.get_param("service_level"),
        "inventory_enabled": repo.inventory_enabled(),
        "inventory_mode": "full" if repo.inventory_enabled() else "pos_only",
        "calc_date": str(DATE),
    }


@app.get("/api/v1/dashboard/kpi")
def kpi(calc_date: str | None = None, db: Session = Depends(get_session)):
    """ダッシュボードKPI集計。Airflow が在庫DB(result_*)に書いた結果を集計して返す。"""
    repo = _repo(db)
    cd = DATE
    if calc_date:
        from datetime import date as _date
        try:
            cd = _date.fromisoformat(calc_date[:10])
        except ValueError:
            raise HTTPException(400, "invalid calc_date format")
    return repo.dashboard_summary(cd)


@app.get("/api/v1/dashboard/history")
def dashboard_history(period: str = Query("day", pattern="^(day|month|year)$"),
                      lookback: int = Query(90, ge=1, le=3660),
                      db: Session = Depends(get_session)):
    """月別/日別/年別 の在庫指標の時系列集計（適正在庫量・安全在庫・回転率）。

    period: day(日別) / month(月別) / year(年別)
    lookback: 対象期間（日）。デフォルト90日。
    """
    repo = _repo(db)
    return repo.history_summary(period=period, lookback_days=lookback)


@app.get("/api/v1/dashboard/drilldown")
def drilldown(segment: str | None = None, category: str | None = None,
              place_id: int | None = None, db: Session = Depends(get_session)):
    """セグメント・カテゴリ・店舗のドリルダウン集計（FR-7-2）。

    segment=AX / category=ホーム / place_id=1 等で絞り込み、商品×店舗単位の
    在庫指標と集計サマリを返す。
    """
    repo = _repo(db)
    return repo.drilldown_summary(
        segment=segment or None,
        category=category or None,
        place_id=place_id,
    )


# -*- coding: utf-8 -*-

# --------------------------------------------------------------------------
# 販売不振商品（排除候補）API
# --------------------------------------------------------------------------

def _exclusion_rows(repo, db):
    from keio_inventory.domain.services.exclusion_service import compute_slow_mover_score
    rows = repo.exclusion_product_data()
    seg_map = {}
    for s in repo.latest_segments(DATE):
        seg_map[s.product_id] = (s.abc_class or '') + (s.xyz_class or '')
    out = []
    for r in rows:
        pid = r['id']
        turn = 0.0
        if r['on_hand'] > 0:
            turn = (r['sales_count'] * (365.0 / 180.0)) / r['on_hand']
        rr = compute_slow_mover_score({
            'product_id': pid,
            'place_id': r.get('place_id'),
            'place_name': r.get('place_name', ''),
            'name': r['name'],
            'abcx': seg_map.get(pid, ''),
            'sales_count': r['sales_count'],
            'sales_amount': r['sales_amount'],
            'on_hand': r['on_hand'],
            'turnover': turn,
        })
        out.append(rr)
    out.sort(key=lambda x: x.score, reverse=True)
    return out


@app.get('/api/v1/exclusion/slow-movers')
def slow_movers(db: Session = Depends(get_session)):
    """販売不振商品のリスト（スコア順・自動生成）。"""
    repo = _repo(db)
    rows = _exclusion_rows(repo, db)
    from dataclasses import asdict
    return {'max_score': 100, 'items': [asdict(r) for r in rows]}

@app.get('/api/v1/exclusion/{product_id}')
def slow_mover_detail(product_id: int, db: Session = Depends(get_session)):
    """販売不振商品の個別詳細（DBデータ参照）。"""
    repo = _repo(db)
    rows = _exclusion_rows(repo, db)
    found = next((r for r in rows if r.product_id == product_id), None)
    if not found:
        raise HTTPException(404, 'product not found')
    # 需要系列(直近30日)をDBから取得
    series = repo.daily_sales_series(days=30).get(product_id, [])
    from dataclasses import asdict
    return {**asdict(found), 'max_score': 100,
            'recent_demand': [{'date': str(d), 'qty': q} for d, q in series]}

# --------------------------------------------------------------------------
# CSV データ取込 API
# --------------------------------------------------------------------------
from fastapi import UploadFile, File as FileParam  # noqa: E402


@app.post("/api/v1/import/{data_type}")
async def import_csv(
    data_type: str,
    file: UploadFile = FileParam(...),
):
    """CSV取込。data_type: pos / purchase / inventory"""
    if data_type not in ("pos", "purchase", "inventory"):
        raise HTTPException(400, "data_type は pos / purchase / inventory です")
    raw = (await file.read()).decode("utf-8-sig")
    from jobs.import_data import import_csv_text
    try:
        result = import_csv_text(data_type, raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"data_type": data_type, "filename": file.filename, **result}



# --------------------------------------------------------------------------
# シンプル HTML ダッシュボード（Chart.js + localStorage 不要・自己完結）
# --------------------------------------------------------------------------
from fastapi.responses import HTMLResponse  # noqa: E402


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_html():
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>在庫最適化 ダッシュボード</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js">
async function doImport(){
  const t = document.getElementById('imp-type').value;
  const f = document.getElementById('imp-file').files[0];
  const out = document.getElementById('imp-result');
  if(!f){ out.textContent = 'ファイルを選択してください'; return; }
  const form = new FormData();
  form.append('file', f);
  out.textContent = '取り込み中...';
  try{
    const r = await fetch('/api/v1/import/' + t, { method:'POST', body: form });
    const j = await r.json();
    if(r.ok){
      out.textContent = '取込完了: ' + j.rows + ' 行 / 挿入 '+ (j.inserted ?? j.skipped) + ' / スキップ ' + (j.skipped ?? 0) + ' / エラー ' + (j.errors ?? []).length;
      load(); // ダッシュボード更新
    } else {
      out.textContent = 'エラー: ' + (j.detail || JSON.stringify(j));
    }
  }catch(e){ out.textContent = '通信エラー: ' + e; }
}
</script>
<style>
:root{--bg:#0a1018;--card:#141c2e;--line:#2a3a5e;--txt:#eef2f8;--muted:#9fb0cc;--navy:#1b2a4a;--pink:#ec008c;--pink-light:#ff6cff;--pink-soft:#ff9ecb}
*{box-sizing:border-box;margin:0;padding:0}
/* 縦スクロールバー領域を常時確保する。タブでページ高さが短いと縦スクロールバーが消えて
   コンテンツ幅が変わり（=縦スクロールバーの幅分）、タブ切替が横にずれて見える問題を防ぐ。
   overflow-x は使わない（レイアウトを崩すため）。 */
html{scrollbar-gutter:stable}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);padding:20px 24px;
  /* 固定レイアウト: 画面1枚に収め、ワークスペース内容(ws-body)だけをスクロールさせる。
     これによりタブ切替でページ全体が無理やりスクロール移動しない */
  height:100vh;box-sizing:border-box;display:flex;flex-direction:column;overflow:hidden}
/* ワークスペース内容をスクロール領域にする（ヘッダー/KPI/タブは固定） */
.ws-body{flex:1;min-height:140px;overflow-y:auto;margin-right:-6px;padding-right:6px}
h1{font-size:22px;font-weight:600;margin-bottom:4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.dash-header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:6px}
.dash-clock-wrap{text-align:right}
.dash-clock{font-size:24px;font-weight:700;color:#ec008c;line-height:1.1}
.dash-clock-day{font-size:12px;color:var(--muted);text-align:right}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.card .value{font-size:26px;font-weight:700;margin-top:6px}
.card .detail{font-size:12px;color:var(--muted);margin-top:4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.panel h2{font-size:15px;font-weight:600;margin-bottom:12px}
.panel .chartWrap{position:relative;height:260px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px}
.pill.open{background:#dc2626;color:#fff}.pill.pending{background:#d97706;color:#fff}.pill.ok{background:#16a34a;color:#fff}
.status-summary{display:inline-flex;gap:6px;flex-wrap:wrap;margin-left:auto}
#tab-rec tbody tr{height:44px}
#tab-rec tbody td{height:44px;vertical-align:middle}
.kpi-mini{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.kpi-mini .card{padding:10px 12px}
.kpi-mini .card .value{font-size:20px}
.kbadge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10px;font-weight:700;margin-left:3px;vertical-align:middle}
.kbadge.good{background:#16a34a;color:#fff}
.kbadge.warn{background:#eab308;color:#1a1a1a}
.kbadge.bad{background:#dc2626;color:#fff}
/* ---- KPIバッジ カスタムツールチップ ---- */
.kbadge{cursor:help;position:relative}
/* ツールチップはバッジの右側に表示（ページ上端で見切れないよう横方向にする） */
.kbadge .tip{display:none;position:absolute;left:calc(100% + 8px);top:50%;transform:translateY(-50%);
  min-width:230px;max-width:280px;padding:9px 11px;background:#0d1420;border:1px solid #2a3a5e;border-radius:8px;
  font-size:11px;font-weight:400;line-height:1.5;color:#dce6f5;text-align:left;z-index:99;box-shadow:0 4px 16px rgba(0,0,0,.5);white-space:normal}
/* バッジの右側に続く空白が小さく右にはみ出しそうな時は、左側に回り込ませる */
.kbadge .tip::before{content:'';position:absolute;right:100%;top:50%;transform:translateY(-50%);
  border:6px solid transparent;border-right-color:#2a3a5e}
.kbadge .tip b{display:inline;color:#ff9ecb;font-weight:700}
.kbadge:hover .tip{display:block}
/* 右端のカードで右にはみ出す場合は、バッジの下（horizontal）ではなく左側に開く画面端グリッド用の安全針 */
@media (max-width:900px){ .kbadge .tip{left:auto;right:calc(100% + 8px);transform:translateY(-50%)} .kbadge .tip::before{right:auto;left:100%;border-right-color:transparent;border-left-color:#2a3a5e} }
.detail-compare{color:var(--muted) !important;font-size:11px;margin-top:2px;line-height:1.4}
.detail-compare .cmp-ok{color:#16a34a;font-weight:600}
.detail-compare .cmp-warn{color:#eab308;font-weight:600}
.detail-compare .cmp-bad{color:#dc2626;font-weight:600}
/* ---- アラート詳細 モーダル ---- */
#alert-overlay{display:none;position:fixed;inset:0;background:rgba(5,9,16,.62);z-index:998;backdrop-filter:blur(2px)}
#alert-detail{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  width:min(560px,92vw);max-height:85vh;overflow-y:auto;
  background:#0d1521;border:1px solid #2a3a5e;border-radius:12px;
  padding:16px 18px;box-shadow:0 12px 40px rgba(0,0,0,.6);z-index:999}
#alert-detail .modal-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #22304a}
#alert-detail .modal-close{flex-shrink:0;width:28px;height:28px;border-radius:50%;
  background:#1b2a4a;border:1px solid #2a3a5e;color:#9fb0cc;font-size:15px;line-height:1;
  cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s}
#alert-detail .modal-close:hover{background:#2a3a5e;color:#fff}
#alert-detail table{margin-top:4px}
.tab-btn{background:transparent;border:none;color:var(--muted);padding:8px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent}
.tab-btn.active{color:var(--pink);border-bottom:2px solid var(--pink);font-weight:600}
button.act-btn{background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:3px 10px;border-radius:6px;font-size:12px;cursor:pointer;margin-right:4px}
button.act-btn.approve{background:#0f5132;border-color:#198754;color:#fff}
button.act-btn.reject{background:#842029;border-color:#dc3545;color:#fff}
@media(max-width:820px){.row{grid-template-columns:1fr}}
</style></head>
<body>
<div class="dash-header">
  <div>
    <h1>在庫最適化プラットフォーム ダッシュボード</h1>
    <div class="sub">データ: 在庫DB(result_*) ・ Airflowバッチ算出結果 ・ <span id="calc-date"></span> ・ 稼働指標（異常確認用）</div>
  </div>
  <div class="dash-clock-wrap">
    <div class="dash-clock" id="dash-clock">--</div>
    <div class="dash-clock-day" id="dash-clock-day"></div>
  </div>
</div>



<!-- 1. KPI ステータスバー（小型） -->
<div class="grid kpi-mini">
<div class="card"><div class="label">適正在庫量 <span class="kbadge" id="k-target-badge"></span></div><div class="value" id="k-target">-</div><div class="detail" id="k-target-meta"></div></div>
<div class="card"><div class="label">在庫回転率 <span class="kbadge" id="k-turnover-badge"></span></div><div class="value" id="k-turnover">-</div><div class="detail" id="k-turnover-meta"></div></div>
<div class="card"><div class="label">安全在庫 <span class="kbadge" id="k-safety-badge"></span></div><div class="value" id="k-safety">-</div><div class="detail" id="k-mode"></div></div>
<div class="card"><div class="label">推奨発注量 <span class="kbadge" id="k-order-badge"></span></div><div class="value" id="k-order">-</div><div class="detail" id="k-order-count"></div></div>
<div class="card"><div class="label">未対応アラート <span class="kbadge" id="k-alert-badge"></span></div><div class="value" id="k-alert">-</div><div class="detail" id="k-alert-total"></div></div>
<div class="card"><div class="label">対象商品数 <span class="kbadge" id="k-item-badge"></span></div><div class="value" id="k-item">-</div><div class="detail">商品×店舗: <span id="k-item-pairs"></span> ・ ABC-XYZ: <span id="k-seg-items"></span></div></div>
</div>




<!-- アラート対応優先度（折りたたみ可能） -->
<div class="alert-standalone" style="margin-bottom:10px">
<div class="panel" style="margin-bottom:0;padding:10px 16px">
  <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer" onclick="toggleAlertPriority()">
    <h2 style="margin:0">⚠ 異常アラート 対応優先度 <span id="alert-pri-count" style="font-size:11px;color:var(--muted);font-weight:400"></span></h2>
    <span id="alert-pri-toggle" style="color:var(--muted);font-size:13px">▼</span>
  </div>
  <div id="alert-pri-body" style="display:none;margin-top:10px">
    <div style="font-size:11px;color:var(--muted);margin-bottom:8px">未対応(open)アラートを重大度順に表示。クリックで詳細・対応</div>
    <div style="max-height:180px;overflow-y:auto"><table style="width:100%;font-size:12px"><thead><tr>
      <th>重大度</th><th>種別</th><th>対象商品</th><th>店舗</th><th>推奨アクション</th><th></th>
    </tr></thead><tbody id="anom-priority-body"></tbody></table></div>
    <div id="anom-summary" style="font-size:11px;color:var(--muted);margin-top:6px"></div>
  </div>
</div>



</div>

<div class="ws-tabs" style="display:flex;gap:4px;border-bottom:2px solid var(--line);margin-bottom:14px;flex-wrap:wrap">
  <button class="tab-btn active" id="wstab-list" onclick="switchWorkspace('list',this)">📋 商品別リスト</button>
  <button class="tab-btn" id="wstab-analysis" onclick="switchWorkspace('analysis',this)">🔍 分析</button>
  <button class="tab-btn" id="wstab-graph" onclick="switchWorkspace('graph',this)">📈 グラフ</button>
  <button class="tab-btn" id="wstab-import" onclick="switchWorkspace('import',this)">📥 取込</button>
</div>
<div class="ws-body">
  <div id="ws-list" class="ws-pane">
<!-- 3. 商品別リスト（店舗切替対応） -->
<div class="panel" style="margin-bottom:14px">
  <h2>商品別リスト</h2>
  <div style="display:flex;gap:6px;border-bottom:1px solid var(--line);flex-wrap:wrap;margin-bottom:8px">
    <button class="tab-btn active" id="tab-btn-rec" onclick="switchList('rec')">推奨発注</button>
    <button class="tab-btn" id="tab-btn-alert" onclick="switchList('alert')">異常アラート</button>
    <button class="tab-btn" id="tab-btn-exclusion" onclick="switchList('exclusion')">販売不振</button>
  </div>
  <!-- 店舗切り替え（全タブ共通フィルタ） -->
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
    <label style="color:var(--muted);font-size:12px">店舗:</label>
    <select id="list-place" onchange="applyListPlace()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px;font-size:12px"><option value="">全店舗</option></select>
  </div>

  <!-- 推奨発注タブ -->
  <div id="tab-rec" class="tab-body">
    <div style="display:flex;gap:6px;border-bottom:1px solid var(--line);flex-wrap:wrap;margin-bottom:8px">
      <button class="tab-btn active" id="rec-tab-pending" onclick="setRecStatus('pending',this)">未処理</button>
      <button class="tab-btn" id="rec-tab-approved" onclick="setRecStatus('approved',this)">承認済</button>
      <button class="tab-btn" id="rec-tab-adjusted" onclick="setRecStatus('adjusted',this)">調整済</button>
      <button class="tab-btn" id="rec-tab-rejected" onclick="setRecStatus('rejected',this)">却下</button>
      <button class="tab-btn" id="rec-tab-all" onclick="setRecStatus('',this)">全件</button>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;align-items:center">
      <label style="color:var(--muted);font-size:13px">並び:</label>
      <select id="rec-sort" onchange="loadRecs()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px"><option value="qty_desc">発注量 多い順</option><option value="qty_asc">発注量 少ない順</option><option value="demand_desc">需要 多い順</option></select>
      <span style="color:var(--muted);font-size:12px" id="rec-count"></span>
      <span id="rec-status-summary" class="status-summary"></span>
    </div>
    <div style="max-height:320px;overflow-y:auto"><table style="width:100%"><thead id="rec-thead"><tr>
      <th>商品</th><th>店舗</th><th>ABC/XYZ</th><th>在庫</th><th>需要予測</th><th>安全在庫</th><th>推奨発注量</th><th>状態</th><th>操作</th>
    </tr></thead><tbody id="rec-tbody"></tbody></table></div>
  </div>

  <!-- 異常アラートタブ -->
  <div id="tab-alert" class="tab-body" style="display:none">
    <div style="display:flex;gap:4px;margin-bottom:8px;border-bottom:1px solid var(--line);flex-wrap:wrap">
      <button class="tab-btn active" id="al-tab-open" onclick="setAlertStatus('open',this)">未対応</button>
      <button class="tab-btn" id="al-tab-ack" onclick="setAlertStatus('ack',this)">対応中</button>
      <button class="tab-btn" id="al-tab-done" onclick="setAlertStatus('done',this)">解決済み</button>
      <button class="tab-btn" id="al-tab-all" onclick="setAlertStatus('',this)">全件</button>
      <span style="margin-left:auto;display:flex;gap:8px;align-items:center">
        <label style="color:var(--muted);font-size:12px">重警度:</label>
        <select id="al-severity" onchange="loadAlerts()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px;font-size:12px"><option value="">全部</option><option value="critical">緊急</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select>
      </span>
    </div>
    <div style="max-height:320px;overflow-y:auto"><table style="width:100%"><thead><tr><th>商品</th><th>店舗</th><th>種別</th><th>重警度</th><th>検知日</th><th>状態</th><th>操作</th></tr></thead><tbody id="alert-tbody"></tbody></table></div>
    <div id="al-summary" style="font-size:12px;color:var(--muted);margin-top:6px"></div>
    <!-- アラート詳細 モーダル（商品リストの上に被せる。背景グレーアウト付き） -->
<div id="alert-overlay" onclick="alClose()"></div>
<div id="alert-detail" role="dialog" aria-modal="true"></div>
  </div>

  <!-- 販売不振タブ -->
  <div id="tab-exclusion" class="tab-body" style="display:none">
    <div style="max-height:320px;overflow-y:auto"><table style="width:100%"><thead><tr><th>商品</th><th>店舗</th><th>ABC/XYZ</th><th>販売数</th><th>在庫</th><th>スコア(/100)</th><th>リスク</th></tr></thead><tbody id="exclusion-tbody"></tbody></table></div>
    <div id="exclusion-detail" style="margin-top:10px;display:none;background:#0a1018;padding:10px;border-radius:8px"></div>
  </div>
</div>



  </div>
  <div id="ws-analysis" class="ws-pane" style="display:none">
<div class="panel" style="margin-bottom:14px">
  <h2>🔍 セグメント分析</h2>
  <div style="display:flex;gap:6px;border-bottom:1px solid var(--line);margin-bottom:14px;flex-wrap:wrap">
    <button class="tab-btn active" id="abtn-matrix" onclick="switchAnalysis('matrix',this)">🗺 マトリクス</button>
    <button class="tab-btn" id="abtn-drill" onclick="switchAnalysis('drill',this)">📋 商品ドリル</button>
  </div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:12px">ABC-XYZ マトリクスで商品を俯瞰 → 商品ドリルでセグメント/カテゴリ/店舗を絞り込んで詳細分析。</div>
</div>

<div id="a-tab-matrix">
<div class="panel" style="margin-bottom:14px"><h2>ABC-XYZ 管理方針マトリクス</h2>
  <div style="font-size:11px;color:var(--muted);margin-bottom:8px">ABC=売上貢献度 / XYZ=需要安定性(CV)・各セル=商品数・売上比・管理方針。セルをクリックで商品ドリルへ</div>
  <div style="overflow-x:auto"><table style="width:100%;table-layout:fixed;border-collapse:separate;border-spacing:6px;font-size:12px">
    <thead><tr>
      <th style="color:var(--muted);font-weight:500;text-align:left;white-space:nowrap;font-size:10px">縦=ABC(売上)<br>横=XYZ</th>
      <th style="color:var(--muted);font-weight:600;font-size:12px;width:24%">X<br><span style="font-weight:400;font-size:10px;color:var(--muted)">安定</span></th>
      <th style="color:var(--muted);font-weight:600;font-size:12px;width:24%">Y<br><span style="font-weight:400;font-size:10px;color:var(--muted)">中変動</span></th>
      <th style="color:var(--muted);font-weight:600;font-size:12px;width:24%">Z<br><span style="font-weight:400;font-size:10px;color:var(--muted)">不安定</span></th>
    </tr></thead>
    <tbody id="seg-matrix-body"></tbody>
  </table></div>
  <div style="font-size:11px;color:var(--muted);margin-top:8px" id="seg-matrix-note"></div>
</div>
</div>

<div id="a-tab-drill" style="display:none">
<div class="panel" style="margin-bottom:14px">
  <h2>📋 商品ドリル（セグメント/カテゴリ/店舗で絞り込み）</h2>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center">
    <label style="color:var(--muted);font-size:13px">カテゴリ:</label>
    <select id="dd-cat" onchange="loadDrilldown()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px"><option value="">すべて</option></select>
    <label style="color:var(--muted);font-size:13px">店舗:</label>
    <select id="dd-place" onchange="loadDrilldown()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px"><option value="">すべて</option></select>
    <label style="color:var(--muted);font-size:13px">セグメント:</label>
    <select id="dd-seg" onchange="loadDrilldown()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px"><option value="">すべて</option></select>
  </div>
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
    <div class="card"><div class="label">対象(商品×店舗)</div><div class="value" id="dd-count" style="font-size:22px">-</div><div class="detail" id="dd-placecount"></div></div>
    <div class="card"><div class="label">適正在庫量</div><div class="value" id="dd-target" style="font-size:22px">-</div><div class="detail">商品×店舗 合計 / ROP</div></div>
    <div class="card"><div class="label">安全在庫</div><div class="value" id="dd-safety" style="font-size:22px">-</div><div class="detail">合計</div></div>
    <div class="card"><div class="label">推奨発注量</div><div class="value" id="dd-rec" style="font-size:22px">-</div><div class="detail">合計</div></div>
    <div class="card"><div class="label">回転率(年近似)</div><div class="value" id="dd-turn" style="font-size:22px">-</div><div class="detail">需要×365/安全在庫</div></div>
  </div>
  <div style="max-height:300px;overflow-y:auto;margin-top:10px"><table style="width:100%"><thead><tr>
    <th>商品</th><th>カテゴリ</th><th>店舗</th><th>ABC/XYZ</th><th>平均需要</th><th>安全在庫</th><th>適正在庫量</th><th>推奨発注量</th>
  </tr></thead><tbody id="dd-tbody"></tbody></table></div>
</div>
</div>

  </div>
  <div id="ws-graph" class="ws-pane" style="display:none">
  <div class="panel">
    <h2>グラフ</h2>
    <div style="display:flex;gap:6px;border-bottom:1px solid var(--line);margin-bottom:10px;flex-wrap:wrap">
      <button class="tab-btn active" id="tab-btn-hist" onclick="switchGraph('hist')">在庫指標の時系列</button>
      <button class="tab-btn" id="tab-btn-fc" onclick="switchGraph('fc')">需要予測 vs 実績</button>
    </div>
    <div id="g-tab-hist">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <label style="color:var(--muted);font-size:13px">集計粒度:</label>
        <select id="hist-period" onchange="loadHistory()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px">
          <option value="day" selected>日別</option>
          <option value="month">月別</option>
          <option value="year">年別</option>
        </select>
        <span style="color:var(--muted);font-size:12px">適正在庫量(平均) / 安全在庫(平均) / 回転率(年近似)</span>
      </div>
      <div class="chartWrap" style="height:400px"><canvas id="chart-history"></canvas></div>
      <div style="max-height:180px;overflow-y:auto;margin-top:6px"><table style="width:100%"><thead><tr>
        <th>期間</th><th>日数</th><th>適正在庫量(平均)</th><th>安全在庫(平均)</th><th>回転率(年近似)</th>
      </tr></thead><tbody id="history-table"></tbody></table></div>
    </div>
    <div id="g-tab-fc" style="display:none">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;align-items:center">
        <label style="color:var(--muted);font-size:13px">対象 (商品×店舗):</label>
        <select id="fc-product" onchange="loadForecastCompare()" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:4px;border-radius:6px;min-width:220px"></select>
        <span style="color:var(--muted);font-size:12px">実線=実績売上, 点線=予測P50, 帯=P80/P95</span>
      </div>
      <div class="chartWrap" style="height:400px"><canvas id="chart-forecast"></canvas></div>
      <div style="font-size:11px;color:var(--muted);margin-top:6px" id="fc-note"></div>
    </div>
  </div>
  </div>
  <div id="ws-import" class="ws-pane" style="display:none">
<!-- 4. データ取込（CSV） -->
<div class="panel" style="margin-bottom:14px">
  <h2>データ取込（CSV）</h2>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
    <select id="imp-type" style="background:#141c2e;color:#eef2f8;border:1px solid #2a3a5e;padding:6px;border-radius:6px;font-size:13px">
      <option value="pos">POS（販売実績）</option>
      <option value="purchase">仕入実績</option>
      <option value="inventory">在庫実績</option>
    </select>
    <input type="file" id="imp-file" accept=".csv" style="color:#eef2f8;font-size:13px">
    <button onclick="doImport()" style="background:#ec008c;color:#fff;border:none;padding:6px 16px;border-radius:6px;font-size:13px;cursor:pointer">取込</button>
  </div>
  <div id="imp-result" style="margin-top:10px;font-size:12px;color:var(--muted);white-space:pre-wrap"></div>
</div>

  </div>
</div>
<script>
// ==== 右上 時計（リアルタイム更新） ====
function updateClock(){
  var now = new Date();
  var y = now.getFullYear();
  var mo = String(now.getMonth()+1).padStart(2,'0');
  var da = String(now.getDate()).padStart(2,'0');
  var w = ['日','月','火','水','木','金','土'][now.getDay()];
  var h = String(now.getHours()).padStart(2,'0');
  var mi = String(now.getMinutes()).padStart(2,'0');
  var se = String(now.getSeconds()).padStart(2,'0');
  var clk = document.getElementById('dash-clock');
  var cd = document.getElementById('dash-clock-day');
  if(clk){ clk.textContent = h + ':' + mi + ':' + se; }
  if(cd){ cd.textContent = y + '年' + mo + '月' + da + '日 (' + w + ')'; }
}
updateClock();
setInterval(updateClock, 1000);
// ワークスペースタブ切替
// 仕様: タブを切り替えるたびにページ先頭に戻る（シンプルで一貫性のある挙動。高さがタブで異なるので位置維持はしない）
function switchWorkspace(name, btn){
  ['list','analysis','graph','import'].forEach(function(k){
    var p = document.getElementById('ws-' + k);
    var b = document.getElementById('wstab-' + k);
    if(p){ p.style.display = (k===name) ? '' : 'none'; }
    if(b){ b.className = 'tab-btn' + ((k===name)?' active':''); }
  });
  // タブ表示時に該当データを再描画（初期ロード済みなら即応）
  if(name==='analysis'){ renderSegMatrix(); loadDrilldown(); }
  if(name==='graph'){ loadHistory(); }
  //   if(name==='alert'){ renderAnomPriority(); }
  // タブ切替: ページ全体は動かさず、ワークスペース領域(ws-body)のスクロール位置だけ先頭に戻す
  var _wb = document.querySelector('.ws-body');
  if(_wb){ _wb.scrollTop = 0; }
}
// ==== ABC-XYZ 管理方針マトリクス（判定支援） ====
var SEG_POLICY = {
  AX:{policy:'重点管理・予測メイン',lvl:'high'}, AY:{policy:'高影響・要監視',lvl:'high'}, AZ:{policy:'予測+柔軟対応・発注点高め',lvl:'high'},
  BX:{policy:'標準管理',lvl:'med'}, BY:{policy:'標準+フォロー',lvl:'med'}, BZ:{policy:'在庫過剰注意',lvl:'med'},
  CX:{policy:'自動発注(KANBAN風)',lvl:'low'}, CY:{policy:'撤退検討対象',lvl:'low'}, CZ:{policy:'撤退・限定販売',lvl:'low'}
};
function segColor(lvl){ return lvl==='high' ? '#dc2626' : (lvl==='med' ? '#d97706' : '#166534'); }
function setSegNote(n){ var el=document.getElementById('seg-matrix-note'); if(el) el.textContent=n; }
async function renderSegMatrix(){
  var body = document.getElementById('seg-matrix-body');
  if(!body){ return; }
  try{
    var r = await fetch('/api/v1/segment/abc-xyz');
    var j = await r.json();
    var ag = {}, total = 0;
    (j.items||[]).forEach(function(it){ var sG=it.segment||'-', a=Number(it.sales_amount||0); ag[sG]=ag[sG]||{count:0,amount:0}; ag[sG].count+=1; ag[sG].amount+=a; total+=a; });
    var html = '';
    var abc=['A','B','C'], xyz=['X','Y','Z'];
    var abcLabel={A:'売上大（上位80%）', B:'中（次15%）', C:'小（下位5%）'};
    abc.forEach(function(a){
      html += '<tr><td style="font-weight:700;text-align:center;vertical-align:middle;height:148px">'+a+'<span style="display:block;font-weight:400;font-size:11px;color:var(--muted)">'+(abcLabel[a]||'')+'</span></td>';
      xyz.forEach(function(x){
        var seg=a+x, g2=ag[seg], policy=SEG_POLICY[seg]||{policy:'-',lvl:'med'};
        if(g2){
          var share=(total>0?(g2.amount/total*100):0); var edge=segColor(policy.lvl);
          html += '<td class="seg-cell" data-seg="'+seg+'" onclick="drillSegCell(this)" style="background:#1b2a4a;border:2px solid '+edge+';border-radius:8px;cursor:pointer;padding:8px;vertical-align:middle;height:148px">'
            + '<div style="display:flex;justify-content:space-between;align-items:baseline"><b style="font-size:15px">'+seg+'</b><b style="font-size:16px">'+g2.count+'<span style="font-size:11px;font-weight:400;color:var(--muted)"> 品</span></b></div>'
            + '<div style="font-size:12px;margin-top:8px">売上 <b>'+share.toFixed(1)+'%</b></div>'
            + '<div style="font-size:11px;line-height:1.4;color:'+edge+';margin-top:8px">◆ '+policy.policy+'</div>'
            + '</td>';
        } else {
          html += '<td style="background:#0a1018;border:1px dashed #2a3a5e;border-radius:8px;padding:7px;color:var(--muted);text-align:center">-</td>';
        }
      });
      html += '</tr>';
    });
    body.innerHTML = html;
    setSegNote('合計 '+total.toLocaleString()+' 円／'+j.items.length+'商品　·　赤=高優先・橙=中・緑=自動化/撤退。セルをクリックで商品一覧へ');
  }catch(e){ body.innerHTML=''; setSegNote('ABC-XYZ取得エラー: '+e); }
}
function drillSegCell(cell){ var seg=cell.getAttribute('data-seg'); if(seg) drillSegment(seg); }
function drillSegment(seg){ var s=document.getElementById('dd-seg'); if(s) s.value=seg; loadDrilldownSet(seg); switchAnalysis('drill'); }
async function loadDrilldownSet(seg){
  var q = new URLSearchParams(); if(seg) q.append('segment', seg);
  var cat = document.getElementById('dd-cat'); if(cat && cat.value) q.append('category', cat.value);
  var plc = document.getElementById('dd-place'); if(plc && plc.value) q.append('place_id', plc.value);
  var tb = document.getElementById('dd-tbody');
  try{ var r = await fetch('/api/v1/dashboard/drilldown?'+q.toString()); var j = await r.json(); ddData=j; renderDrilldownFilters(j);
    if(seg) document.getElementById('dd-seg').value = seg;
    var sm = j.summary||{};
    document.getElementById('dd-count').textContent = sm.item_count!=null?sm.item_count:'-';
    document.getElementById('dd-placecount').textContent = (sm.place_count||0)+' 店舗 / '+(j.rows||[]).length+' 件';
    document.getElementById('dd-target').textContent = sm.target_inventory_total!=null?Math.round(Number(sm.target_inventory_total)).toLocaleString():'-';
    document.getElementById('dd-safety').textContent = sm.safety_stock_total!=null?Math.round(Number(sm.safety_stock_total)).toLocaleString():'-';
    document.getElementById('dd-rec').textContent = sm.recommended_qty_total!=null?Math.round(Number(sm.recommended_qty_total)).toLocaleString():'-';
    document.getElementById('dd-turn').textContent = sm.inventory_turnover_annual!=null?Number(sm.inventory_turnover_annual).toLocaleString():'-';
    tb.innerHTML = (j.rows||[]).map(function(x){ return '<tr><td>'+x.product_name+'</td><td>'+x.category+'</td><td>'+x.place_name+'</td><td>'+x.segment+'</td><td>'+x.avg_demand+'</td><td>'+Math.round(x.safety_stock)+'</td><td><b>'+Math.round(x.target_inventory)+'</b></td><td>'+Math.round(x.recommended_qty)+'</td></tr>'; }).join('');
  }catch(e){ tb.innerHTML='<tr><td colspan=8>エラー</td></tr>'; }
}
function set(id, v){ var el=document.getElementById(id); if(el) el.textContent=v; }
// ==== ダッシュボードKPI（load） ====
async function load(){
  try{
    var r = await fetch('/api/v1/dashboard/kpi');
    var d = await r.json();
    document.getElementById('calc-date').textContent = '算定日: ' + d.calc_date;
    set('k-target', (d.inventory.target_inventory_total!=null? Math.round(Number(d.inventory.target_inventory_total)).toLocaleString() : '-'));
    set('k-turnover', d.inventory.inventory_turnover_annual != null ? Number(d.inventory.inventory_turnover_annual).toLocaleString() : '-');
    set('k-safety', Math.round(Number(d.inventory.safety_stock_total)).toLocaleString());
    set('k-mode', 'mode: ' + d.inventory.mode + (d.inventory.service_level!=null?' / SL='+d.inventory.service_level:''));
    set('k-order', Math.round(Number(d.order.order_quantity_total)).toLocaleString());
    set('k-order-count', d.order.order_count + ' 件 / ' + d.order.recommendation_items + ' 品目' + (d.order.pending?' (未処理 '+d.order.pending+')':''));
    set('k-alert', d.anomaly.open_alerts);
    set('k-alert-total', '合計 ' + d.anomaly.total_alerts);
    set('k-item', d.segment.items);
    set('k-item-pairs', d.inventory.total_items_safety_stock);
    set('k-seg-items', d.segment.items);
  }catch(e){ console.error('kpi load err', e); }
  // ==== KPI 対目標 良否判定バッジ ====
  try{
    var inv = d.inventory||{}, ord = d.order||{}, anm = d.anomaly||{};
    var turnover = Number(inv.inventory_turnover_annual||0);
    var inj = inv.target_inventory_total!=null?Number(inv.target_inventory_total):0;
    var recq = Number(ord.order_quantity_total||0);
    var stock = Math.max(0, inj - recq);  // 在庫合計 ≒ 適正 - 推奨
    var ss = Number(inv.safety_stock_total||0);
    var dem = Number(inv.avg_demand_total||0);
    // ---- 適正在庫量：在庫充填率（在庫/適正） ----
    var fill = inj>0 ? (stock/inj*100) : 100;
    var fillLv = fill>=60 ? 'good' : (fill>=30 ? 'warn' : 'bad');
    setKpiBadge('k-target-badge', fillLv,
      (fillLv==='good'?'在庫の水準は適切':'在庫が不足気味') ,
      (fillLv==='good'
        ? '在庫充填率 '+fill.toFixed(0)+'%<br>在庫 '+Math.round(stock).toLocaleString()+' / 適正 '+Math.round(inj).toLocaleString()+'<br><b>このまま通常運用</b>でOKです。'
        : '在庫充填率 '+fill.toFixed(0)+'% と適正(100%)を下回っています。<br><b>欠品リスク</b>の可能性があります。<br><b>推奨発注量を確認し、補充を優先</b>してください。'));
    setKpiCompare('k-target-meta',
      ['在庫充填率', fill.toFixed(0)+'%', '100%', fill>=90?'ok':(fill>=60?'warn':'bad')],
      '在庫(推計) ' + Math.round(stock).toLocaleString() + ' / 適正 ' + Math.round(inj).toLocaleString());
    // ---- 在庫回転率：高すぎ(欠品危険)・低すぎ(滞留) ----
    var turnLv = (turnover>=10 && turnover<=120) ? 'good' : (turnover>=5 ? 'warn' : 'bad');
    setKpiBadge('k-turnover-badge', turnLv,
      (turnLv==='good'?'回転率は適正域':'回転率が適正域から逸脱'),
      (turnLv==='good'
        ? '年あたり回転率 '+turnover.toFixed(1)+' 回は適正域(10〜120)内です。<br><b>流動性OK</b>。'
        : (turnover<5 ? '回転率 '+turnover.toFixed(1)+' 回と低く、<b>滞留・過剰在庫</b>の可能性。<br><b>販売施策・値引き・返品</b>を検討。'
                     : (turnover>120 ? '回転率が著しく高く<b>欠品リスク</b>があります。<br><b>安全在庫の引き上げ</b>をご検討。'
                                     : '回転率が適正域の境界付近です。<br><b>推移を注視</b>してください。'))));
    setKpiCompare('k-turnover-meta',
      ['回転率(年)', turnover.toFixed(1)+'回', '10〜120回', turnLv],
      '在庫の回転速度（年間何回入れ替わるか）');
    // ---- 安全在庫：需要に対するバッファ日数 ----
    var bufDays = dem>0 ? (ss/dem) : 7;
    var bufLv = bufDays>=5 ? 'good' : (bufDays>=3 ? 'warn' : 'bad');
    setKpiBadge('k-safety-badge', bufLv,
      (bufLv==='good'?'安全在庫は十分':'安全在庫が少なめ'),
      (bufLv==='good'
        ? 'バッファ '+bufDays.toFixed(1)+' 日分（目安 5日以上）を確保。<br><b>欠品に強い在庫構成</b>。'
        : 'バッファ '+bufDays.toFixed(1)+' 日分と少なめ。<br><b>リードタイム中の欠品</b>リスク。<br><b>安全在庫・発注点を引き上げ</b>をご検討。'));
    setKpiCompare('k-mode',
      ['安全在庫', ss.toLocaleString()+' 単', 'バッファ '+bufDays.toFixed(1)+'日分（目安≥5日）', bufLv],
      '日次需要 '+dem.toFixed(1)+' に対するバッファ日数');
    // ---- 推奨発注量：補充必要額 ----
    var recLv = recq<=0 ? 'good' : (recq <= inj*0.5 ? 'warn' : 'bad');
    setKpiBadge('k-order-badge', recLv,
      (recLv==='good'?'補充は十分':'発注が必要'),
      (recLv==='good'
        ? '推奨発注量 0。現状庫で適正に近い水準です。<br><b>通常運用を継続</b>。'
        : '推奨発注量 '+Math.round(recq).toLocaleString()+'、補充不足の可能性があります。<br><b>発注承認</b>を進めてください。'));
    setKpiCompare('k-order-count',
      ['推奨発注量', recq.toLocaleString(), '在庫→適正の "+補充分"', recLv],
      '今すぐ補充すべき量（在庫から適正まで）');
    // ---- 未対応アラート ----
    var alLv = anm.open_alerts===0 ? 'good' : (anm.open_alerts<=3?'warn':'bad');
    setKpiBadge('k-alert-badge', alLv,
      (alLv==='good'?'未対応アラートなし':'未対応アラートあり'),
      (alLv==='good'
        ? '対応待ちの異常アラートは 0 件です。<br><b>良好な状態</b>。'
        : '未対応アラート '+anm.open_alerts+' 件あります。<br>重大度の高い順に<b>対応・解決</b>してください。'));
  }catch(e){ /* バッジ失敗は無視 */ }
  renderSegMatrix();
  renderAnomPriority();
}
function setKpiBadge(id, level, tipTitle, tipBody){
  var el = document.getElementById(id);
  if(!el) return;
  var map = {good:['◎良好'], warn:['○要注意'], bad:['⚠要注意']};
  // warn/bad は分かりやすく（warn=○注意, bad=⚠注意）
  var m = map[level]||['-'];
  el.textContent = m[0];
  el.className = 'kbadge ' + level;
  // カスタムツールチップ（マウスホバーで状態・対処を表示）
  if(tipBody){
    var tip = document.createElement('span'); tip.className='tip';
    // 見出しと本文を改行で分ける（本文内の改行は各説明文で <br> 指定）
    tip.innerHTML = (tipTitle?'<b>'+tipTitle+'</b><br>':'') + tipBody;
    // 既存tipを除去して再生成
    var old = el.querySelector('.tip'); if(old) old.remove();
    el.appendChild(tip);
  }
  if(tipTitle){ el.setAttribute('title', tipTitle); } else { el.removeAttribute('title'); }
}
function setKpiCompare(detailId, trio, extra){
  // 詳細カード下部に「現在値 vs 基準」の比較を表示する（既存テキストは保持）
  var el = document.getElementById(detailId);
  if(!el) return;
  var name = trio[0], cur = trio[1], base = trio[2], lv = trio[3]||'';

  // 既存の比較div（この関数が以前に作った物）を除去して多重表示を防ぐ
  var exist = el.querySelector('.detail-compare');
  if(!exist){
    // 既存テキスト（set()で設定済みの textContent）を span で囲んで保持
    var kept = document.createElement('span');
    kept.className = 'detail-kept';
    kept.textContent = el.textContent;   // set()による元情報（mode/件数など）
    if(el.childNodes.length===0 || (el.childNodes.length===1 && el.childNodes[0].nodeType===3)){
      el.textContent=''; 
      if(kept.textContent) el.appendChild(kept);
    }
  }
  var cls = lv==='ok'?'cmp-ok':(lv==='warn'?'cmp-warn':'cmp-bad');
  var label = lv==='ok'?'● 適': (lv==='warn'?'▲ 注意':'× 乖離');
  var d = document.createElement('div');
  d.className = 'detail-compare';
  d.innerHTML = '<span class="'+cls+'">'+label+'</span> 現在: <b>'+cur+'</b>' +
      (base ? ' ／ 基準: '+base : '') +
      (extra ? '<br><span style="color:#8fa3c0">'+extra+'</span>' : '');
  el.appendChild(d);
}

// ==== 異常アラート 対応優先度（判定支援） ====
var SEV_ORDER = {critical:0, high:1, medium:2, low:3};
var SEV_LABEL = {critical:'緊急 即対応', high:'高 高優先', medium:'中 中優先', low:'低 確認'};
var SEV_JP = {critical:'緊急', high:'高', medium:'中', low:'低'};
var SEV_MEANING = {critical:'追加発注・在庫確保（欠品リスク）', high:'撤退検討・販売見直し', medium:'在庫削減・需要調査', low:'棚卸・運用点検'};
var ANOM_JP = {slow_mover:'長期滞留', demand_spike:'需要急上昇', demand_drop:'需要急落', abnormal_turnover:'異常回転率'};
// 異常アラート対応優先度の折りたたみ制御
function toggleAlertPriority(){
  var body = document.getElementById('alert-pri-body');
  var tri = document.getElementById('alert-pri-toggle');
  if(!body) return;
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  if(tri){ tri.textContent = open ? '▼' : '▲'; }
}
async function renderAnomPriority(){
  var body = document.getElementById('anom-priority-body');
  var summ = document.getElementById('anom-summary');
  var sevColor = {critical:'#ef4444', high:'#f97316', medium:'#eab308', low:'#9fb0cc'};
  try{
    var r = await fetch('/api/v1/anomaly/alerts');
    var j = await r.json();
    var all = j.items||[];
    var open = all.filter(function(a){ return a.status === 'open'; });
    var items = open.slice().sort(function(a,b){ return (SEV_ORDER[a.severity]||9) - (SEV_ORDER[b.severity]||9); });
    if(!items.length){ body.innerHTML = '<tr><td colspan=6 style="color:#34d399;text-align:center">未対応アラートは 0件</td></tr>'; summ.textContent = '未対応アラート: 0件'; return; }
    body.innerHTML = items.map(function(a){
      var sc = sevColor[a.severity]||'#9fb0cc';
      var tip = SEV_MEANING[a.severity]||'';
      var label = (SEV_LABEL[a.severity]||a.severity);
      var jp = (ANOM_JP[a.anomaly_type]||a.anomaly_type);
      return '<tr style="border-left:4px solid '+sc+'"><td><b style="color:'+sc+'">'+label+'</b><br><span style="color:var(--muted);font-size:11px">'+tip+'</span></td>'+
        '<td>'+jp+'</td>'+
        '<td>'+a.product_name+'</td><td>'+(a.place_name||('店舗'+a.place_id))+'</td>'+
        '<td>'+a.recommended_action+'</td>'+
        "<td><button class='act-btn approve' onclick='openAlertDetail("+a.id+")'>詳細</button></td></tr>";
    }).join('');
    var bysev = {}; items.forEach(function(a){ bysev[a.severity]=(bysev[a.severity]||0)+1; });
    var pc = document.getElementById('alert-pri-count');
    if(pc){ pc.textContent = '未対応 ' + items.length + ' 件'; }
    var parts = ['未対応 '+items.length+' 件'];
    ['critical','high','medium','low'].forEach(function(k){ if(bysev[k]){ parts.push('<span style="color:'+sevColor[k]+'">'+(SEV_JP[k]||k)+' '+bysev[k]+'</span>'); } });
    summ.innerHTML = parts.join(' ・ ');
  }catch(e){ body.innerHTML=''; summ.textContent='アラート取得エラー: '+e; }
}
load();


function switchList(name){
  ['rec','alert','exclusion'].forEach(function(k){
    var tb = document.getElementById('tab-' + k);
    var btn = document.getElementById('tab-btn-' + k);
    if(tb){ tb.style.display = (k===name) ? '' : 'none'; }
    if(btn){ btn.className = 'tab-btn' + ((k===name)?' active':''); }
  });
  if(name==='rec') loadRecs();
//   if(name==='alert') loadAlerts();
  if(name==='exclusion') loadExclusion();
  applyListPlace();
}

function switchGraph(name){
  ['hist','fc'].forEach(function(k){
    var tb = document.getElementById('g-tab-' + k);
    var btn = document.getElementById('tab-btn-' + k);
    if(tb){ tb.style.display = (k===name) ? '' : 'none'; }
    if(btn){ btn.className = 'tab-btn' + ((k===name)?' active':''); }
  });
  if(name==='hist') loadHistory();
  if(name==='fc') loadForecastOptions();
}
// 分析タブのサブタブ切替（マトリクス / 商品ドリル）
function switchAnalysis(name, btn){
  ['matrix','drill'].forEach(function(k){
    var tb = document.getElementById('a-tab-' + k);
    var b = document.getElementById('abtn-' + k);
    if(tb){ tb.style.display = (k===name) ? '' : 'none'; }
    if(b){ b.className = 'tab-btn' + ((k===name)?' active':''); }
  });
  if(name==='matrix') renderSegMatrix();
  if(name==='drill') loadDrilldown();
}
// 商品別リストの店舗フィルタ
function applyListPlace(){
  var place = document.getElementById('list-place').value;
  ['rec','alert','exclusion'].forEach(function(t){
    var rows = document.querySelectorAll('#tab-' + t + ' tbody tr');
    for(var i=0;i<rows.length;i++){
      var r = rows[i];
      var pn = r.getAttribute('data-place');
      var show = true;
      if(place && pn){ show = (pn.indexOf(place) >= 0); }
      else if(place && (!pn||pn==='')){ show = true; } // 店舗情報なし行は表示
      r.style.display = show ? '' : 'none';
    }
  });
}
// 店舗フィルタの選択肢を初期化（推奨発注APIから店舗名取得）
async function initListPlace(){
  var sel = document.getElementById('list-place');
  if(!sel){ return; }
  try{
    var r = await fetch('/api/v1/order/recommendation');
    var j = await r.json();
    var places = {};
    (j.items||[]).forEach(function(it){ if(it.place_name) places[it.place_name] = 1; });
    var opts = ['<option value="">全店舗</option>'];
    Object.keys(places).sort().forEach(function(p){ opts.push('<option value="'+p+'">'+p+'</option>'); });
    sel.innerHTML = opts.join('');
  }catch(e){ /* ignore */ }
}


var REC_ITEMS = [];
var recStatus = 'pending'; // デフォルトは未処理
function setRecStatus(st, btn){
  recStatus = st;
  // タブ切り替え時はスクロール位置を保持しない（空タブで上に飛ぶのを防ぐ）
  window._recScroll = -1;
  ['pending','approved','adjusted','rejected','all'].forEach(function(k){
    var b = document.getElementById('rec-tab-' + k);
    if(b){ b.className = 'tab-btn' + (k===st?' active':''); }
  });
  loadRecs();
}
async function loadRecs(){
  const st = recStatus;
  const so = document.getElementById('rec-sort').value;
  const tb = document.getElementById('rec-tbody');
  const q = new URLSearchParams(); if(st) q.append('status',st); if(so) q.append('sort',so);
  // スクロール位置を記録（承認/却下後もその場を維持するため）
  var recWrap = tb.closest('[style*="max-height"]');
  // 承認/却下ボタン操作時のみスクロール位置を保存（タブ切替時は -1 で保持しない）
  if(window._recScroll === undefined && recWrap){ window._recScroll = recWrap.scrollTop; }
  else if(window._recScroll !== undefined && window._recScroll === -1){ /* タブ切替: 位置保持なし */ }
  try{
    const r = await fetch('/api/v1/order/recommendation?' + q.toString());
    const j = await r.json();
    const items = j.items||[];
    REC_ITEMS = items;
    // 商品×店舗 の件数と店舗数を表示（重複でなく場所別であることを明示）
    var nPlaces = new Set(items.map(function(x){return x.place_id})).size;
    var nProds = new Set(items.map(function(x){return x.product_id})).size;
    document.getElementById('rec-count').textContent =
      nProds + ' 商品 × ' + nPlaces + ' 店舗 = ' + items.length + ' 件（場所ごとの発注量）';
    // 一覧表示: タブ選択の状態(st)でAPIが絞り込んだ items をそのまま表示（未処理タブでは承認で自動的に消える）
    var arr = items.slice();
    var stCount = {};
    arr.forEach(function(it){ var s = it.status||'pending'; stCount[s] = (stCount[s]||0)+1; });
    var stMap = {pending:'未処理', approved:'承認済', adjusted:'調整済', rejected:'却下'};
    var stClass = {pending:'pending', approved:'ok', adjusted:'pending', rejected:'open'};
    var ss = document.getElementById('rec-status-summary');
    ss.innerHTML = Object.keys(stMap).map(function(k){
      var n = stCount[k]||0;
      var cls = stClass[k]||'pending';
      return '<span class="pill ' + cls + '" style="margin-right:6px">' + stMap[k] + ' <b>' + n + '</b></span>';
    }).join('');
    // 商品×店舗を1行ずつ表示（在庫・推奨は別列）
    // 並び順: 発注量多い順/少ない順/需要多い順
    arr.sort(function(a,b){
      if(so==='demand_desc') return (Number(b.forecast_demand||0)) - (Number(a.forecast_demand||0));
      var da = Number(a.recommended_qty||0), db = Number(b.recommended_qty||0);
      return (so==='qty_asc') ? (da-db) : (db-da);
    });
    function stClass2(s){ return s==='approved'?'ok':(s==='rejected'?'open':'pending'); }
    var html = '';
    var lastPid = -1;
    arr.forEach(function(it){
      var seg = it.segment || '-';
      var place = it.place_name || ('店舗'+it.place_id);
      var qty = Math.round(Number(it.recommended_qty||0));
      var oh = Math.round(Number(it.on_hand_qty||0));
      // pushNew: 商品が変わった先頭行だけ帯色
      var grpBg = (it.product_id !== lastPid) ? ' style="background:#1b2a4a"' : '';
      lastPid = it.product_id;
      // 在庫・推奨を別列
      var invTxt = oh.toLocaleString();
      var recTxt = (qty>0 ? '<b style="color:#ec008c">'+qty.toLocaleString()+'</b>' : '<span style="color:var(--muted)">0</span>');
      // 状態バッジ（日本語表示: 未処理/承認済/調整済/却下）
      var ST_REC_JP = {pending:'未処理', approved:'承認済', adjusted:'調整済', rejected:'却下'};
      var stPill = '<span class="pill '+stClass2(it.status)+'">'+(ST_REC_JP[it.status]||it.status)+'</span>';
      // 操作（未処理のみ）
      var btnAct = (it.status==='pending'||it.status==='adjusted')
        ? '<button class="act-btn approve" onclick="approveRec('+it.id+')">承認</button>'
          + '<button class="act-btn reject" onclick="rejectRec('+it.id+')">却下</button>'
        : '';
      html += '<tr data-place="'+place+'" style="min-height:44px;'+grpBg.slice(grpBg?grpBg.indexOf('background'):-1)+'">' +
        '<td><b>' + it.product_name + '</b></td>' +
        '<td>' + place + '</td>' +
        '<td>' + seg + '</td>' +
        '<td>' + invTxt + '</td>' +
        '<td>' + Math.round(Number(it.forecast_demand)).toLocaleString() + '</td>' +
        '<td>' + Math.round(Number(it.safety_stock)).toLocaleString() + '</td>' +
        '<td>' + recTxt + '</td>' +
        '<td>' + stPill + '</td>' +
        '<td>' + btnAct + '</td></tr>';
    });
    if(!html){ html = '<tr><td colspan=9 style="text-align:center;color:var(--muted)">該当なし</td></tr>'; }
    tb.innerHTML = html;
    applyListPlace();
    // スクロール位置を復元（承認/却下ボタン操作時のみ。タブ切替(-1)は維持しない）
    if(window._recScroll !== undefined && window._recScroll !== -1){
      var rw = tb.closest('[style*="max-height"]');
      if(rw){ rw.scrollTop = window._recScroll; }
    }
    window._recScroll = undefined;
  }catch(e){ tb.innerHTML = '<tr><td colspan=9>読込エラー</td></tr>'; }
}

async function approveRec(id){
  await fetch('/api/v1/order/recommendation/' + id + '/approve', {method:'POST'}); loadRecs();
}
async function rejectRec(id){
  await fetch('/api/v1/order/recommendation/' + id + '/reject', {method:'POST'}); loadRecs();
}
async function approveProduct(pid){
  var ids = REC_ITEMS.filter(function(x){return x.product_id===pid;}).map(function(x){return x.id;});
  for(var i=0;i<ids.length;i++){ await fetch('/api/v1/order/recommendation/' + ids[i] + '/approve', {method:'POST'}); }
  loadRecs();
}
async function rejectProduct(pid){
  var ids = REC_ITEMS.filter(function(x){return x.product_id===pid;}).map(function(x){return x.id;});
  for(var i=0;i<ids.length;i++){ await fetch('/api/v1/order/recommendation/' + ids[i] + '/reject', {method:'POST'}); }
  loadRecs();
}
async function loadExclusion(){
  const tb = document.getElementById('exclusion-tbody');
  try{
    const r = await fetch('/api/v1/exclusion/slow-movers');
    const j = await r.json();
    tb.innerHTML = (j.items||[]).map(function(it){
      var pn = it.place_name || ('店舗'+it.place_id);
      var riskColor = it.risk==='撤退候補' ? '#ef4444' : (it.risk==='要注意' ? '#f97316' : '#16a34a');
      return '<tr data-place="'+pn+'" style="cursor:pointer" onclick="loadExclusionDetail(' + it.product_id + ')" title="'+pn+'">' +
        '<td>' + it.name + '</td><td>' + pn + '</td><td>' + it.abcx + '</td><td>' + Math.round(it.sales_count) + '</td>' +
        '<td>' + Math.round(it.on_hand) + '</td><td><b style="color:'+riskColor+'">' + it.score + '/100</b></td><td>' + it.risk + '</td></tr>';
    }).join('');
    applyListPlace();
  }catch(e){ tb.innerHTML = '<tr><td colspan=6>読込エラー</td></tr>'; }
}

async function loadExclusionDetail(pid){
  const d = document.getElementById('exclusion-detail');
  try{
    const r = await fetch('/api/v1/exclusion/' + pid);
    const it = await r.json();
    const demand = it.recent_demand ? it.recent_demand.slice(-14).map(function(x){return x.qty}).join(', ') : '-';
    d.style.display='block';
    d.innerHTML = '<b>' + it.name + '</b>（' + it.abcx + '）スコア ' + it.score + '/100 [' + it.risk + ']<br>' +
      '販売数: ' + Math.round(it.sales_count) + ' / 売上: ' + Math.round(it.sales_amount) + ' / 在庫: ' + Math.round(it.on_hand) + '<br>' +
      '不振理由: ' + (it.reasons||[]).join('、') + '<br>' +
      '直近需要: <span style="color:#ec008c">' + demand + '</span>';
  }catch(e){ d.style.display='block'; d.innerHTML='詳細取得エラー'; }
}



var alStatus = 'open'; // デフォルトは未対応
var SEV_COLOR = {critical:'#ef4444', high:'#f97316', medium:'#eab308', low:'#9fb0cc'};
var ANOM_JP_T = {slow_mover:'長期滞留', demand_spike:'需要急上昇', demand_drop:'需要急落', abnormal_turnover:'異常回転率'};
function setAlertStatus(st, btn){
  alStatus = st;
  ['open','ack','done','all'].forEach(function(k){
    var b = document.getElementById('al-tab-' + k);
    if(b){ b.className = 'tab-btn' + (k===st?' active':''); }
  });
  loadAlerts();
}
async function loadAlerts(){
  const se = document.getElementById('al-severity').value;
  const q = new URLSearchParams(); if(alStatus) q.append('status',alStatus); if(se) q.append('severity',se);
  const tb = document.getElementById('alert-tbody');
  try{
    const r = await fetch('/api/v1/anomaly/alerts?' + q.toString());
    const j = await r.json();
    const items = j.items||[];
    // 集計サマリ（未対応件数）
    var openCount = items.filter(function(x){return x.status==='open';}).length;
    var sevSum = {};
    items.forEach(function(x){ sevSum[x.severity]=(sevSum[x.severity]||0)+1; });
    var summEl = document.getElementById('al-summary');
    if(summEl){
      var parts = [];
      ['critical','high','medium','low'].forEach(function(k){ if(sevSum[k]){ parts.push('<span style="color:'+(SEV_COLOR[k]||'#9fb0cc')+'">'+(SEV_JP[k]||k)+' '+sevSum[k]+'</span>'); } });
      summEl.innerHTML = (alStatus==='open' ? '未対応 ' : '') + items.length + ' 件' + (parts.length?' ・ '+parts.join(' ・ '):'');
    }
    if(!items.length){
      tb.innerHTML = '<tr><td colspan=7 style="text-align:center;color:var(--muted)">該当なし</td></tr>';
      applyListPlace();
      return;
    }
    tb.innerHTML = items.map(function(a){
      const btn = a.status==='open' ? '<button class="act-btn" onclick="ackAlert('+a.id+')">対応中</button> ' : '';
      const done = a.status!=='done' ? '<button class="act-btn reject" onclick="resolveAlert('+a.id+')">解決</button>' : '';
      var apn = a.place_name || ('店舗'+a.place_id);
      var sc = SEV_COLOR[a.severity] || '#9fb0cc';
      var jp = ANOM_JP_T[a.anomaly_type] || a.anomaly_type;
      // 状態を日本語表示（open=未対応 / ack=対応中 / done=解決済み）
      var ST_JP = {open:'未対応', ack:'対応中', done:'解決済み'};
      var stTxt = ST_JP[a.status] || a.status;
      var stBadge = a.status==='done' ? '<span class="pill ok">解決済み</span>' : '<span class="pill '+(a.status==='ack'?'ok':'open')+'">'+stTxt+'</span>';
      return '<tr data-place="'+apn+'" style="cursor:pointer;border-left:3px solid '+sc+'" onclick="showAlertDetail('+a.id+')">' +
        '<td>'+a.product_name+'</td><td>'+apn+'</td><td>'+jp+'</td>' +
        '<td style="color:'+sc+';font-weight:600">'+(SEV_JP[a.severity]||a.severity)+'</td>' +
        '<td>'+a.detected_at+'</td><td>'+stBadge+'</td>' +
        '<td style="text-align:right" onclick="event.stopPropagation()">'+btn+done+'</td></tr>';
    }).join('');
    applyListPlace();
  }catch(e){ tb.innerHTML = '<tr><td colspan=7>読込エラー</td></tr>'; }
}

var ANOM_JP2 = {slow_mover:"長期滞留", demand_spike:"需要急上昇", demand_drop:"需要急落", abnormal_turnover:"異常回転率"};
async function showAlertDetail(id){
  const d = document.getElementById('alert-detail');
  try{
    const r = await fetch('/api/v1/anomaly/alerts/' + id);
    const a = await r.json();
    // 重大度・種別の日本語表記
    var jp = ANOM_JP2[a.anomaly_type] || a.anomaly_type;
    var sev = a.severity || 'low';
    var sevLabel = SEV_LABEL[sev] || sev;
    var sevColor = {critical:'#ef4444', high:'#f97316', medium:'#eab308', low:'#9fb0cc'}[sev] || '#9fb0cc';
    // 検知理由の解釈（指標 vs 閾値）
    var reason = '';
    var dt = a.detail || {};
    if(a.anomaly_type === 'abnormal_turnover' && (dt.lower!=null || dt.upper!=null)){
      var oh = dt.on_hand!=null ? Number(dt.on_hand) : null;
      if(oh!=null && dt.lower!=null && oh < dt.lower){
        reason = '在庫 ' + oh + ' が正常下限 ' + Number(dt.lower).toFixed(0) + ' を下回る（過少在庫・欠品リスク）';
      } else if(oh!=null && dt.upper!=null && oh > dt.upper){
        reason = '在庫 ' + oh + ' が正常上限 ' + Number(dt.upper).toFixed(0) + ' を超過する（過剰在庫・滞留リスク）';
      } else {
        reason = '回転率が正常範囲 [' + (dt.lower!=null?Number(dt.lower).toFixed(1):'-') + '〜' + (dt.upper!=null?Number(dt.upper).toFixed(1):'-') + '] から逸脱';
      }
    } else {
      reason = jp + 'として検知（' + a.detected_at + '）';
    }
    // 直近需要サマリ
    var demand = a.recent_demand || [];
    var dq = demand.map(function(x){return Number(x.qty).toFixed(0)}).join(' → ');
    var demandHint = demand.length>0 ? demand.slice(-7).map(function(x){return Number(x.qty).toFixed(0)}).join(', ') : '-';
    var btn = '';
    if(a.status === 'open'){ btn = '<button class="act-btn approve" onclick="ackAlert('+a.id+')">対応中にする</button> <button class="act-btn reject" onclick="resolveAlert('+a.id+')">解決にする</button>'; }
    else if(a.status === 'ack'){ btn = '<button class="act-btn approve" onclick="resolveAlert('+a.id+')">解決にする</button>'; }
    // 店舗・SKUによる差別化（同じ商品名でもどの店舗のアラートか区別できるよう見出しに表示）
    var pn = a.place_name || ('店舗' + a.place_id);
    var skuTxt = a.sku_code ? '<span style="color:var(--muted);font-size:12px;font-weight:400">[' + a.sku_code + ']</span> ' : '';
    // モーダル（商品リストの上に被せる）を表示
    document.getElementById('alert-overlay').style.display='block';
    d.style.display='block';
    d.innerHTML =
      '<div class="modal-head">' +
        '<div style="min-width:0">' +
          '<div style="display:flex;align-items:baseline;flex-wrap:wrap;gap:6px"><b style="font-size:16px">' + a.product_name + '</b> ' + skuTxt +
            '<span style="color:#ec008c;font-size:13px;font-weight:600">' + pn + '</span></div>' +
          '<div style="margin-top:3px"><span style="color:var(--muted);font-size:12px">' + jp + '</span>　' +
            '<span style="color:' + sevColor + ';font-weight:700;font-size:12px">' + sevLabel + '</span></div>' +
        '</div>' +
        '<button class="modal-close" onclick="alClose()" title="閉じる">✕</button>' +
      '</div>' +
      '<table style="width:100%;font-size:13px;border-collapse:collapse"><tbody>' +
        '<tr><td style="padding:5px 0;color:var(--muted);width:110px">店舗</td><td>' + pn + '</td></tr>' +
        '<tr><td style="padding:5px 0;color:var(--muted)">商品コード</td><td>' + (a.sku_code || '-') + '</td></tr>' +
        '<tr><td style="padding:5px 0;color:var(--muted)">検知日</td><td>' + a.detected_at + '</td></tr>' +
        '<tr><td style="padding:5px 0;color:var(--muted)">検知理由</td><td style="color:#ec008c">' + reason + '</td></tr>' +
        '<tr><td style="padding:5px 0;color:var(--muted)">推奨アクション</td><td><b style="color:#ff9ecb">' + a.recommended_action + '</b></td></tr>' +
        '<tr><td style="padding:5px 0;color:var(--muted)">直近7日需要</td><td><span style="color:#ec008c">' + demandHint + '</span></td></tr>' +
        '<tr><td style="padding:8px 0 2px;color:var(--muted)">操作</td><td>' + btn + '</td></tr>' +
      '</tbody></table>';
  }catch(e){ document.getElementById('alert-overlay').style.display='block'; d.style.display='block'; d.innerHTML='<div class="modal-head"><div>詳細取得エラー</div><button class="modal-close" onclick="alClose()">✕</button></div>'; }
}
function alClose(){
  var ov = document.getElementById('alert-overlay'); if(ov) ov.style.display='none';
  var d2 = document.getElementById('alert-detail'); if(d2) d2.style.display='none';
}
function openAlertDetail(id){ switchList('alert'); showAlertDetail(id); }

async function ackAlert(id){
  await fetch('/api/v1/anomaly/alerts/' + id + '/ack', {method:'POST'}); loadAlerts();
  alClose(); // モーダル内の操作後は閉じる（一覧から押した場合は既に閉じているので無害）
}
async function resolveAlert(id){
  await fetch('/api/v1/anomaly/alerts/' + id + '/resolve', {method:'POST'}); loadAlerts();
  alClose();
}

loadRecs();   // 初期表示は「推奨発注」タブ
loadAlerts();
initListPlace();

async function loadHistory(){
  const period = document.getElementById('hist-period').value;
  const canvas = document.getElementById('chart-history');
  const tb = document.getElementById('history-table');
  try{
    const r = await fetch('/api/v1/dashboard/history?period=' + period + '&lookback=180');
    const j = await r.json();
    const items = j.items||[];
    tb.innerHTML = items.map(function(x){
      const turn = (x.inventory_turnover_annual!=null? Number(x.inventory_turnover_annual).toLocaleString() : '-');
      return '<tr><td>' + x.label + '</td><td>' + x.days + '</td>' +
        '<td>' + Number(x.target_inventory_avg).toLocaleString() + '</td>' +
        '<td>' + Number(x.safety_avg).toLocaleString() + '</td><td>' + turn + '</td></tr>';
    }).join('');
    // 折れ線グラフ: 適正在庫量(左軸) + 安全在庫(左軸) + 回転率(右軸)
    if(window._histChart){ window._histChart.destroy(); }
    window._histChart = new Chart(canvas, { type:'line', data:{
      labels: items.map(function(x){return x.label}),
      datasets: [
        { label:'適正在庫量(平均)', data: items.map(function(x){return x.target_inventory_avg}), borderColor:'#ec008c', backgroundColor:'#ec008c', yAxisID:'y', tension:0.3 },
        { label:'安全在庫(平均)', data: items.map(function(x){return x.safety_avg}), borderColor:'#ff9ecb', backgroundColor:'#ff9ecb', yAxisID:'y', tension:0.3 },
        { label:'回転率(年近似)', data: items.map(function(x){return x.inventory_turnover_annual}), borderColor:'#5b78b8', backgroundColor:'#5b78b8', yAxisID:'y1', tension:0.3, dashed:true }
      ]
    }, options:{
      responsive:true,
      plugins:{ legend:{ labels:{ color:'#eef2f8' } } },
      scales:{
        x:{ ticks:{ color:'#9fb0cc' }, grid:{ color:'#2a3a5e' } },
        y:{ position:'left', ticks:{ color:'#9fb0cc' }, grid:{ color:'#2a3a5e' } },
        y1:{ position:'right', ticks:{ color:'#9fb0cc' }, grid:{ drawOnChartArea:false } }
      }
    }});
  }catch(e){ tb.innerHTML = '<tr><td colspan=5>読込エラー</td></tr>'; }
}

// ==== 需要予測 vs 実績（FR-7-4） ====
var fcOptions = [];
async function loadForecastOptions(){
  try{
    const r = await fetch("/api/v1/order/recommendation");
    const j = await r.json();
    const sel = document.getElementById("fc-product");
    fcOptions = (j.items||[]).map(function(it){ return { product_id:it.product_id, place_id:it.place_id, label:(it.product_name||("商品"+it.product_id))+" × "+(it.place_name||("店舗"+it.place_id)) }; });
    // 商品×店舗一意化
    var seen = {};
    fcOptions = fcOptions.filter(function(o){ var k=o.product_id+"_"+o.place_id; if(seen[k]){return false;} seen[k]=1; return true; });
    sel.innerHTML = fcOptions.map(function(o,i){ return '<option value="'+i+'" '+(i===0?'selected':'')+'>'+o.label+'</option>'; }).join('');
    if(fcOptions.length){ loadForecastCompare(); }
  }catch(e){ console.error("forecast options err", e); }
}
async function loadForecastCompare(){
  const sel = document.getElementById("fc-product");
  const opts = fcOptions[Number(sel.value)];
  if(!opts){ return; }
  const canvas = document.getElementById("chart-forecast");
  const note = document.getElementById("fc-note");
  try{
    const r = await fetch("/api/v1/forecast/"+opts.product_id+"/"+opts.place_id);
    const j = await r.json();
    const s = (j.series||[]);
    const dates = s.map(function(x){return x.target_date});
    const actuals = s.map(function(x){return x.actual_qty != null ? x.actual_qty : null});
    var hasActual = actuals.some(function(v){return v!=null;});
    note.textContent = hasActual ? "実線=実績売上 / 青点=予測P50 / 帯=P80~P95予測区間（"+opts.label+"）" : "実績(actual)データが未蓄積のため、現状は予測(帯)のみ表示。実績が蓄積されると対比されます。";
    if(window._fcChart){ window._fcChart.destroy(); }
    window._fcChart = new Chart(canvas, { type:"line", data:{
      labels: dates,
      datasets: [
        { label:"実績売上", data: actuals, borderColor:"#1b2a4a", backgroundColor:"#1b2a4a", spanGaps:true, tension:0.3, pointRadius:3 },
        { label:"予測P50", data: s.map(function(x){return x.forecast_p50}), borderColor:"#ec008c", backgroundColor:"transparent", borderDash:[6,4], tension:0.3, pointRadius:2 },
        { label:"予測P80", data: s.map(function(x){return x.forecast_p80}), borderColor:"rgba(236,0,140,0.35)", backgroundColor:"rgba(236,0,140,0.15)", borderDash:[2,2], pointRadius:0, fill:"+1" },
        { label:"予測P95", data: s.map(function(x){return x.forecast_p95}), borderColor:"rgba(236,0,140,0.25)", backgroundColor:"rgba(236,0,140,0.08)", pointRadius:0 }
      ]
    }, options:{
      responsive:true,
      plugins:{ legend:{ labels:{ color:"#eef2f8" } } },
      scales:{ x:{ ticks:{ color:"#9fb0cc" }, grid:{ color:"#2a3a5e" } }, y:{ ticks:{ color:"#9fb0cc" }, grid:{ color:"#2a3a5e" } } }
    }});
  }catch(e){ note.textContent = "予測チャート取得エラー: " + e; }
}

// ==== ドリルダウン分析（FR-7-2） ====
var ddData = null;
async function loadDrilldown(){
  const q = new URLSearchParams();
  const cat = document.getElementById("dd-cat").value;
  const plc = document.getElementById("dd-place").value;
  const seg = document.getElementById("dd-seg").value;
  if(cat) q.append("category", cat);
  if(plc) q.append("place_id", plc);
  if(seg) q.append("segment", seg);
  const tb = document.getElementById("dd-tbody");
  try{
    const r = await fetch("/api/v1/dashboard/drilldown?" + q.toString());
    const j = await r.json();
    ddData = j;
    renderDrilldownFilters(j);
    const s = j.summary||{};
    document.getElementById("dd-count").textContent = s.item_count!=null ? s.item_count : "-";
    document.getElementById("dd-placecount").textContent = (s.place_count||0) + " 店舗 / " + (j.rows||[]).length + " 件";
    document.getElementById("dd-target").textContent = s.target_inventory_total!=null ? Math.round(Number(s.target_inventory_total)).toLocaleString() : "-";
    document.getElementById("dd-safety").textContent = s.safety_stock_total!=null ? Math.round(Number(s.safety_stock_total)).toLocaleString() : "-";
    document.getElementById("dd-rec").textContent = s.recommended_qty_total!=null ? Math.round(Number(s.recommended_qty_total)).toLocaleString() : "-";
    document.getElementById("dd-turn").textContent = s.inventory_turnover_annual!=null ? Number(s.inventory_turnover_annual).toLocaleString() : "-";
    tb.innerHTML = (j.rows||[]).map(function(x){
      return "<tr><td>"+x.product_name+"</td><td>"+x.category+"</td><td>"+x.place_name+"</td><td>"+x.segment+"</td>"+
      "<td>"+x.avg_demand+"</td><td>"+Math.round(x.safety_stock)+"</td><td><b>"+Math.round(x.target_inventory)+"</b></td><td>"+Math.round(x.recommended_qty)+"</td></tr>";
    }).join("");
  }catch(e){ tb.innerHTML = "<tr><td colspan=8>読込エラー</td></tr>"; }
}
function renderDrilldownFilters(j){
  var fill = function(id, vals, label){
    var el = document.getElementById(id);
    if(!el) return;
    var cur = el.value;
    var opts = ['<option value="">'+label+'</option>'];
    (vals||[]).forEach(function(v){
      var val, lbl;
      if(v && typeof v === "object"){ val = String(v.id); lbl = v.name; }
      else { val = String(v); lbl = String(v); }
      var c = (cur===val);
      opts.push('<option value="'+val+'"'+(c?' selected':'')+'>'+lbl+'</option>');
    });
    el.innerHTML = opts.join("");
  };
  fill("dd-cat", j.filters.categories, "カテゴリ(すべて)");
  fill("dd-place", (j.filters.places||[]).map(function(p){return {id:p.id,name:p.name}}), "店舗(すべて)");
  fill("dd-seg", j.filters.segments, "セグメント(すべて)");
}
loadDrilldown();

loadForecastOptions();
loadHistory();
</script></body></html>
"""


