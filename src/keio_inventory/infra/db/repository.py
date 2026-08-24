from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from keio_inventory.infra.db.models import (
    AnomalyAlert, DemandForecastParam, InventoryDaily, Place, Product,
    PurchaseHistory, ResultAbcXyz, ResultForecast, ResultOrderRecommendation,
    ResultSafetyStock, SkuDailySales,
)


def _native(value):
    """Convert numpy / Decimal / bool to plain Python native types that
    psycopg2 can bind directly (avoids 'np' schema errors)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    import numbers
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


class InventoryRepository:
    """Reads master data from, and persists result_* data to, PostgreSQL."""

    def __init__(self, session: Session):
        self.session = session

    def list_products(self) -> list[Product]:
        return list(self.session.execute(select(Product).order_by(Product.id)).scalars())

    def list_places(self) -> list[Place]:
        return list(self.session.execute(select(Place).order_by(Place.id)).scalars())

    def get_param(self, key: str):
        row = self.session.execute(
            select(DemandForecastParam).where(DemandForecastParam.param_key == key)
        ).scalar_one_or_none()
        if row is None:
            return None
        val = row.value
        if isinstance(val, dict) and "value" in val:
            return val["value"]
        return val

    def inventory_enabled(self) -> bool:
        v = self.get_param("inventory_enabled")
        if v is None:
            return False
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    def set_param(self, key: str, value, description: str | None = None) -> None:
        """パラメータを upsert。value は JSONB 用の辞書(例 {'value': True} または {'value': 0.95})。"""
        row = self.session.execute(
            select(DemandForecastParam).where(DemandForecastParam.param_key == key)
        ).scalar_one_or_none()
        if row is None:
            self.session.add(DemandForecastParam(
                param_key=key,
                value=value if isinstance(value, dict) else {"value": value},
                description=description,
            ))
        else:
            row.value = value if isinstance(value, dict) else {"value": value}
            if description is not None:
                row.description = description
        self.session.flush()

    # ---------------------------------------------------------------
    # 在庫データ蓄積の自動判定 / 実在庫・実測リードタイム
    # ---------------------------------------------------------------
    def inventory_accumulation_status(self, target_days: int = 14) -> dict:
        """在庫データ(inventory_daily)の蓄積状況を自動判定する。

        対象(商品 x 店舗)のうち、直近 target_days 日分の在庫データが
        何割蓄積されているかを集計し、full モード切替の判定に使う。

        Returns
        -------
        {
            'total_pairs': int,        # 対象の (商品 x 店舗) 組数
            'covered_pairs': int,      # 直近 target_days 内に 1 日以上データがある組数
            'coverage': float,         # covered / total (0.0-1.0)
            'latest_date': date|None , # inventory_daily の最新日付
            'days_since_latest': int,  # 最新データからの経過日数
        }
        """
        products = self.list_products()
        places = self.list_places()
        total = len(products) * max(len(places), 1)

        latest_row = self.session.execute(
            select(InventoryDaily.inventory_date)
            .order_by(InventoryDaily.inventory_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        latest_date = latest_row
        days_since = None
        cutoff = date.today()
        if latest_row is not None:
            days_since = (date.today() - latest_row).days
            cutoff = latest_row

        start = cutoff - __import__('datetime').timedelta(days=target_days)
        # 対象組のうち、window 内に在庫データがある組数を数える
        covered = self.session.execute(
            select(InventoryDaily.product_id, InventoryDaily.place_id)
            .where(InventoryDaily.inventory_date >= start)
            .where(InventoryDaily.inventory_date <= cutoff)
            .distinct()
        ).fetchall()
        covered_pairs = len(covered)
        coverage = (covered_pairs / total) if total > 0 else 0.0
        return {
            "total_pairs": total,
            "covered_pairs": covered_pairs,
            "coverage": round(coverage, 4),
            "latest_date": latest_row,
            "days_since_latest": days_since,
        }

    def latest_on_hand(self, product_id: int, place_id: int) -> float | None:
        """直近の実在庫(inventory_daily) を返す。データが無ければ None。"""
        row = self.session.execute(
            select(InventoryDaily.on_hand_qty)
            .where(InventoryDaily.product_id == product_id,
                   InventoryDaily.place_id == place_id)
            .order_by(InventoryDaily.inventory_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return float(row) if row is not None else None

    def latest_on_hand_map(self) -> dict[tuple[int, int], float]:
        """全 (product_id, place_id) の最新実在庫を辞書で返す。"""
        rows = self.session.execute(
            select(InventoryDaily.product_id, InventoryDaily.place_id, InventoryDaily.inventory_date, InventoryDaily.on_hand_qty)
            .order_by(InventoryDaily.inventory_date)
        ).fetchall()
        out: dict[tuple[int, int], float] = {}
        seen: dict[tuple[int, int], date] = {}
        for pr, pl, dt, qty in rows:
            key = (pr, pl)
            if key not in seen or dt >= seen[key]:
                seen[key] = dt
                out[key] = float(qty)
        return out

    def measured_lead_time(self, product_id: int) -> float | None:
        """入荷履歴(purchase_history) から実測リードタイム(発注→入荷の日数)を推定。

        最終入荷日 - 発注日の平均を取る。データが無ければ None。
        """
        rows = self.session.execute(
            select(PurchaseHistory.po_date, PurchaseHistory.expected_date, PurchaseHistory.received_qty)
            .where(PurchaseHistory.product_id == product_id,
                   PurchaseHistory.received_qty.isnot(None))
        ).fetchall()
        deltas = []
        for po, exp, rec in rows:
            base = rec if po is None else po  # 代替
            # 実測リードタイムは expected_date - po_date を基本とする
            if po is not None and exp is not None:
                deltas.append((exp - po).days)
        if not deltas:
            return None
        return round(sum(deltas) / len(deltas), 2)


    def save_forecast(self, forecast_date: date, items: list[dict]) -> None:
        for it in items:
            model = ResultForecast(
                product_id=it["product_id"],
                place_id=it["place_id"],
                forecast_date=forecast_date,
                target_date=it["target_date"],
                forecast_p50=_native(it.get("p50")),
                forecast_p80=_native(it.get("p80")),
                forecast_p95=_native(it.get("p95")),
                model_name=it.get("model_name"),
            )
            self.session.merge(model)
        self.session.flush()

    def save_safety_stock(self, calc_date: date, items: list[dict]) -> None:
        for it in items:
            model = ResultSafetyStock(
                product_id=it["product_id"], place_id=it["place_id"],
                calc_date=calc_date,
                safety_stock=_native(it.get("safety_stock")),
                reorder_point=_native(it.get("reorder_point")),
                target_inventory=_native(it.get("target_inventory")),
                order_qty=_native(it.get("order_qty")),
                avg_demand=_native(it.get("avg_demand")),
                demand_std=_native(it.get("demand_std")),
                lead_time_days=_native(it.get("lead_time_days")),
                service_level=_native(it.get("service_level")),
                mode=it.get("mode", "pos_only"),
            )
            self.session.merge(model)
        self.session.flush()

    def save_abc_xyz(self, calc_date: date, items: list[dict]) -> None:
        for it in items:
            model = ResultAbcXyz(
                product_id=it["product_id"], abc_class=it["abc"],
                xyz_class=it["xyz"], calc_date=calc_date,
                sales_amount=_native(it.get("sales_amount")), cv=_native(it.get("cv")),
            )
            self.session.merge(model)
        self.session.flush()

    def save_recommendations(self, calc_date: date, items: list[dict]) -> None:
        for it in items:
            model = ResultOrderRecommendation(
                product_id=it["product_id"], place_id=it["place_id"],
                calc_date=calc_date,
                forecast_demand=_native(it.get("forecast_demand")),
                safety_stock=_native(it.get("safety_stock")),
                on_hand_qty=_native(it.get("on_hand_qty")),
                recommended_qty=_native(it.get("recommended_qty")),
                status=it.get("status", "pending"),
            )
            self.session.merge(model)
        self.session.flush()

    def daily_sales_series(self, days: int = 180, end_date=None) -> dict:
        """POS(sku_daily_sales)から、product_id ごとの日次売上数量系列(全店舗合計)を返す。

        end_date を指定すると、その日以前のPOSのみを対象にする（時系列バックフィル用）。
        """
        from sqlalchemy import func as _f
        from keio_inventory.infra.db.models import SkuDailySales
        from datetime import date, timedelta
        end = end_date or date.today()
        start = end - timedelta(days=days)
        rows = self.session.query(
            SkuDailySales.product_id, SkuDailySales.sales_date, _f.sum(SkuDailySales.qty_sold)
        ).filter(SkuDailySales.sales_date >= start, SkuDailySales.sales_date <= end).group_by(
            SkuDailySales.product_id, SkuDailySales.sales_date).order_by(SkuDailySales.sales_date).all()
        out = {}
        for pid, d, q in rows:
            out.setdefault(pid, []).append((d, float(q or 0)))
        return out

    def sales_amount_by_product(self, days: int = 180, end_date=None) -> dict:
        """POSから、product_id ごとの期間売上金額合計(ABC-XYZ用)を返す。

        end_date を指定すると、その日以前のPOSのみを対象にする（時系列バックフィル用）。
        """
        from sqlalchemy import func as _f
        from keio_inventory.infra.db.models import SkuDailySales
        from datetime import date, timedelta
        end = end_date or date.today()
        start = end - timedelta(days=days)
        rows = self.session.query(
            SkuDailySales.product_id, _f.sum(SkuDailySales.amount)
        ).filter(SkuDailySales.sales_date >= start, SkuDailySales.sales_date <= end).group_by(
            SkuDailySales.product_id).all()
        return {pid: float(amt or 0) for pid, amt in rows}

    def sales_count_by_product(self, days: int = 180) -> dict:
        """POSから、product_id ごとの期間売上数量合計(ABC-XYZ用)を返す。"""
        from sqlalchemy import func as _f
        from keio_inventory.infra.db.models import SkuDailySales
        from datetime import date, timedelta
        start = date.today() - timedelta(days=days)
        rows = self.session.query(
            SkuDailySales.product_id, _f.sum(SkuDailySales.qty_sold)
        ).filter(SkuDailySales.sales_date >= start).group_by(
            SkuDailySales.product_id).all()
        return {pid: float(q or 0) for pid, q in rows}

    def on_hand_map_all(self) -> dict:
        """各 (product, place) の最新実在庫を返す。"""
        from keio_inventory.infra.db.models import InventoryDaily
        rows = self.session.query(
            InventoryDaily.product_id, InventoryDaily.place_id, InventoryDaily.on_hand_qty,
            InventoryDaily.inventory_date
        ).order_by(InventoryDaily.inventory_date).all()
        seen = {}
        out = {}
        for pid, plid, q, dt in rows:
            key = (pid, plid)
            if key not in seen or dt >= seen[key]:
                seen[key] = dt
                out[key] = float(q or 0)
        return out

    def exclusion_product_data(self, days: int = 180) -> list[dict]:
        """販売不振判定・一覧表示用の商品データを 商品×店舗単位 でまとめて返す。

        Returns per (product, place): id, place_id, place_name, name, sku, category,
        sales_count, sales_amount, on_hand. 販売不振スコアは domain 層で算出する。
        店舗フィルタ（店舗別）に対応するため、店舗ごとの売上・在庫で算出する。
        """
        from sqlalchemy import func as _f
        from keio_inventory.infra.db.models import SkuDailySales, InventoryDaily
        from datetime import date, timedelta

        today = date.today()
        start = today - timedelta(days=days)
        products = self.list_products()
        places = {p.id: p.place_name for p in self.list_places()}

        # 売上集計（数量・金額）を 商品×店舗 で
        rows = self.session.query(
            SkuDailySales.product_id, SkuDailySales.place_id,
            _f.sum(SkuDailySales.qty_sold), _f.sum(SkuDailySales.amount)
        ).filter(SkuDailySales.sales_date >= start).group_by(
            SkuDailySales.product_id, SkuDailySales.place_id).all()
        sales = {}
        for pid, plid, q, a in rows:
            sales[(pid, plid)] = (float(q or 0), float(a or 0))

        # 最新在庫（商品×店舗）
        onhand_map = {}
        inv_rows = self.session.query(
            InventoryDaily.product_id, InventoryDaily.place_id,
            InventoryDaily.on_hand_qty, InventoryDaily.inventory_date
        ).order_by(InventoryDaily.inventory_date).all()
        seen = {}
        for pid, plid, q, dt in inv_rows:
            key = (pid, plid)
            if key not in seen or dt >= seen[key]:
                seen[key] = dt
                onhand_map[(pid, plid)] = float(q or 0)

        by_pid = {}
        for pr in products:
            by_pid[pr.id] = pr

        result = []
        # 全 (商品×店舗) 組み合わせを列挙
        for pid, pr in by_pid.items():
            for plid in places:
                sc, sa = sales.get((pid, plid), (0.0, 0.0))
                oh = onhand_map.get((pid, plid), 0.0)
                result.append({
                    'id': pid,
                    'place_id': plid,
                    'place_name': places.get(plid, ''),
                    'name': pr.name,
                    'sku_code': pr.sku_code or '',
                    'category': pr.category or '',
                    'sales_count': sc,
                    'sales_amount': sa,
                    'on_hand': oh,
                    'lead_time': float(pr.lead_time_days) if pr.lead_time_days is not None else 7.0,
                })
        # 売上・在庫が全て0の行は除外（本当にレコードが存在する組合せのみ）
        result = [r for r in result if r['sales_count'] > 0 or r['on_hand'] > 0 or r['sales_amount'] > 0]
        return result


    def clear_alerts(self) -> None:
        self.session.query(AnomalyAlert).delete()
        self.session.flush()


    def upsert_transaction(self, data_type: str, record: dict) -> bool:
        """CSV取込用: POS/仕入/在庫の1レコードを UPSERT（冪等）する。"""
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from keio_inventory.infra.db.models import SkuDailySales, PurchaseHistory, InventoryDaily

        # 型変換（record はすべて文字列）
        def num(v):
            if v is None or (isinstance(v, str) and v.strip() == ""):
                return None
            return float(v)

        def dt(v):
            if v is None or (isinstance(v, str) and v.strip() == ""):
                return None
            from datetime import datetime as _dt
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
                try:
                    return _dt.strptime(v.strip(), fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"日付が解釈できません: {v!r}")

        if data_type == "pos":
            model = SkuDailySales
            values = {
                "sales_date": dt(record.get("sales_date")),
                "product_id": int(record.get("product_id")),
                "place_id": int(record.get("place_id")),
                "qty_sold": num(record.get("qty_sold")),
                "amount": num(record.get("amount")),
            }
            pk = ["sales_date", "product_id", "place_id"]
        elif data_type == "purchase":
            model = PurchaseHistory
            values = {
                "po_date": dt(record.get("po_date")),
                "product_id": int(record.get("product_id")),
                "place_id": int(record.get("place_id")),
                "order_qty": num(record.get("order_qty")),
                "received_qty": num(record.get("received_qty")),
                "expected_date": dt(record.get("expected_date")),
            }
            pk = ["po_date", "product_id", "place_id"]
        elif data_type == "inventory":
            model = InventoryDaily
            values = {
                "inventory_date": dt(record.get("inventory_date")),
                "product_id": int(record.get("product_id")),
                "place_id": int(record.get("place_id")),
                "on_hand_qty": num(record.get("on_hand_qty")),
                "allocated_qty": num(record.get("allocated_qty")),
                "available_qty": num(record.get("available_qty")),
            }
            pk = ["inventory_date", "product_id", "place_id"]
        else:
            raise ValueError(f"不正なタイプ: {data_type}")

        stmt = pg_insert(model).values(**values)
        upd = {k: getattr(stmt.excluded, k) for k in values if k not in pk}
        stmt = stmt.on_conflict_do_update(index_elements=pk, set_=upd)
        self.session.execute(stmt)
        return True


    def clear_for_calc_date(self, calc_date: date) -> None:
        """Idempotency (design 6.4): remove that calc_date's result_* rows
        before re-writing, so re-running the daily batch never double-inserts."""
        self.session.query(ResultForecast).filter(
            ResultForecast.forecast_date == calc_date).delete(synchronize_session=False)
        self.session.query(ResultSafetyStock).filter(
            ResultSafetyStock.calc_date == calc_date).delete(synchronize_session=False)
        self.session.query(ResultAbcXyz).filter(
            ResultAbcXyz.calc_date == calc_date).delete(synchronize_session=False)
        self.session.query(ResultOrderRecommendation).filter(
            ResultOrderRecommendation.calc_date == calc_date).delete(synchronize_session=False)
        self.session.flush()

    def save_alert(self, item: dict) -> None:
        # detected_at は文字列で来る場合があるため、date に正規化（SQLite対応）
        _d = item.get("detected_at", date.today())
        if isinstance(_d, str):
            try:
                _d = date.fromisoformat(_d[:10])
            except ValueError:
                _d = date.today()
        self.session.add(AnomalyAlert(
            product_id=item["product_id"], place_id=item["place_id"],
            anomaly_type=item["anomaly_type"], severity=item["severity"],
            detail=item.get("detail"),
            recommended_action=item.get("recommended_action"),
            source=item.get("source"),
            detected_at=_d,
        ))
        self.session.flush()

    def latest_forecast(self, product_id, place_id, limit=10):
        return list(self.session.execute(
            select(ResultForecast)
            .where(ResultForecast.product_id == product_id,
                   ResultForecast.place_id == place_id)
            .order_by(ResultForecast.target_date)
            .limit(limit)
        ).scalars())

    def forecast_with_actual(self, product_id: int, place_id: int, limit: int = 30) -> list[dict]:
        """予測(forecast_p50/80/95)と実績売上(sku_daily_sales)を日付で突き合わせて返す。

        result_forecast は target_date ごとの予測、actual は sku_daily_sales の実績売上。
        同じ日付の実績を付与し、実績が無い日は None を返す（予測vs実績比較チャート用）。
        """
        frows = list(self.session.execute(
            select(ResultForecast).where(
                ResultForecast.product_id == product_id,
                ResultForecast.place_id == place_id)
            .order_by(ResultForecast.target_date)
            .limit(limit)
        ).scalars())
        if not frows:
            return []
        # 予測対象日付範囲の実績売上を一括取得
        dates = [r.target_date for r in frows]
        actuals = {}
        arows = self.session.execute(
            select(SkuDailySales).where(
                SkuDailySales.product_id == product_id,
                SkuDailySales.place_id == place_id,
                SkuDailySales.sales_date.in_(dates))
        ).scalars()
        for a in arows:
            actuals[str(a.sales_date)] = float(a.qty_sold or 0)
        return [{
            "target_date": str(r.target_date),
            "forecast_p50": float(r.forecast_p50 or 0),
            "forecast_p80": float(r.forecast_p80 or 0),
            "forecast_p95": float(r.forecast_p95 or 0),
            "actual_qty": actuals.get(str(r.target_date)),
        } for r in frows]

    def latest_safety_stock(self, calc_date: date | None = None) -> list[ResultSafetyStock]:
        cd = calc_date or date.today()
        return list(self.session.execute(
            select(ResultSafetyStock).where(ResultSafetyStock.calc_date == cd)
        ).scalars())

    def latest_segments(self, calc_date: date | None = None) -> list[ResultAbcXyz]:
        cd = calc_date or date.today()
        return list(self.session.execute(
            select(ResultAbcXyz).where(ResultAbcXyz.calc_date == cd)
        ).scalars())

    def latest_recommendations(self, calc_date: date | None = None, status: str | None = None) -> list[ResultOrderRecommendation]:
        cd = calc_date or date.today()
        q = select(ResultOrderRecommendation).where(ResultOrderRecommendation.calc_date == cd)
        if status:
            q = q.where(ResultOrderRecommendation.status == status)
        return list(self.session.execute(q).scalars())

    def list_alerts(self, status: str | None = None, severity: str | None = None,
                    anomaly_type: str | None = None) -> list[AnomalyAlert]:
        """アラートの一覧（フィルタ付き・新しい順）。"""
        q = select(AnomalyAlert).order_by(AnomalyAlert.detected_at.desc(), AnomalyAlert.id.desc())
        if status:
            q = q.where(AnomalyAlert.status == status)
        if severity:
            q = q.where(AnomalyAlert.severity == severity)
        if anomaly_type:
            q = q.where(AnomalyAlert.anomaly_type == anomaly_type)
        return list(self.session.execute(q).scalars())

    def get_alert(self, alert_id: int) -> AnomalyAlert | None:
        """1件のアラート詳細を取得。"""
        return self.session.get(AnomalyAlert, alert_id)

    def update_alert_status(self, alert_id: int, status: str) -> bool:
        """アラートのステータスを更新（open→ack→done）。"""
        a = self.session.get(AnomalyAlert, alert_id)
        if a is None:
            return False
        a.status = status
        if status == 'done':
            from datetime import datetime
            a.resolved_at = datetime.now()
        self.session.commit()
        return True

    def open_alerts(self) -> list[AnomalyAlert]:
        return list(self.session.execute(
            select(AnomalyAlert).order_by(AnomalyAlert.detected_at)
        ).scalars())

    def update_rec_status(self, product_id: int, place_id: int, calc_date: date, status: str, qty: float | None = None) -> bool:
        rec = self.session.execute(
            select(ResultOrderRecommendation).where(
                ResultOrderRecommendation.product_id == product_id,
                ResultOrderRecommendation.place_id == place_id,
                ResultOrderRecommendation.calc_date == calc_date,
            )
        ).scalar_one_or_none()
        if rec is None:
            return False
        if qty is not None:
            rec.recommended_qty = qty
        rec.status = status
        self.session.commit()
        return True

    def dashboard_summary(self, calc_date: date | None = None) -> dict:
        """ダッシュボード用の KPI 集計（result_* から算出）。

        設計書 5 章の KPI（在庫回転率・発注サマリ・異常アラート・ABC-XYZ構成）を
        空データでも耐える形で返す。
        """
        cd = calc_date or date.today()

        ss_list = self.latest_safety_stock(cd)
        rec_list = self.latest_recommendations(cd)
        rec_by_key = {(r.product_id, r.place_id): r for r in rec_list}

        total_avg_demand = 0.0
        total_safety = 0.0
        total_target_inventory = 0.0  # 適正在庫量合計
        total_rec_qty = 0.0
        order_count = 0
        for ss in ss_list:
            total_avg_demand += float(ss.avg_demand or 0)
            total_safety += float(ss.safety_stock or 0)
            total_target_inventory += float(getattr(ss, "target_inventory", None) or float(ss.reorder_point or 0))
            rec = rec_by_key.get((ss.product_id, ss.place_id))
            if rec and rec.recommended_qty is not None and float(rec.recommended_qty) > 0:
                total_rec_qty += float(rec.recommended_qty)
                order_count += 1

        turnover = None
        if total_safety > 0:
            turnover = (total_avg_demand * 365.0) / total_safety

        alerts = self.open_alerts()
        open_alerts = [a for a in alerts if a.status == "open"]
        by_type: dict[str, int] = {}
        for a in alerts:
            by_type[a.anomaly_type] = by_type.get(a.anomaly_type, 0) + 1

        seg_list = self.latest_segments(cd)
        seg_count: dict[str, int] = {}
        for s in seg_list:
            key = (s.abc_class or "") + (s.xyz_class or "")
            seg_count[key] = seg_count.get(key, 0) + 1

        pending = sum(1 for r in rec_list if r.status == "pending")
        approved = sum(1 for r in rec_list if r.status == "approved")

        return {
            "calc_date": str(cd),
            "inventory": {
                "mode": "full" if self.inventory_enabled() else "pos_only",
                "total_items_safety_stock": len(ss_list),
                "avg_demand_total": round(total_avg_demand, 2),
                "safety_stock_total": round(total_safety, 2),
                "target_inventory_total": round(total_target_inventory, 2),  # 適正在庫量合計
                "inventory_turnover_annual": round(turnover, 2) if turnover else None,
                "service_level": self.get_param("service_level"),
            },
            "order": {
                "recommendation_items": len(rec_list),
                "pending": pending,
                "approved": approved,
                "order_quantity_total": round(total_rec_qty, 2),
                "order_count": order_count,
            },
            "anomaly": {
                "open_alerts": len(open_alerts),
                "total_alerts": len(alerts),
                "by_type": by_type,
            },
            "segment": {
                "items": len(seg_list),
                "by_segment": seg_count,
            },
        }

    def history_summary(self, period: str = "day", lookback_days: int = 90) -> dict:
        """月別/日別/年別 での適正在庫量・安全在庫・在庫回転率の時系列集計。

        result_safety_stock（calc_date ごとの日次スナップショット）を、
        period に応じて日別(day) / 月別(month) / 年別(year)にバケットして集計する。
        基の日次テーブルからの SQL 導出（02_data_model §2.7 の方針に準拠）。
        """
        from datetime import timedelta, date as _date
        from sqlalchemy import func as _f

        target_attr = getattr(ResultSafetyStock, "target_inventory", ResultSafetyStock.reorder_point)
        end = _date.today()
        start = end - timedelta(days=lookback_days)
        rows = self.session.query(
            ResultSafetyStock.calc_date,
            _f.sum(ResultSafetyStock.safety_stock),
            _f.max(ResultSafetyStock.safety_stock),
            _f.sum(_f.coalesce(target_attr, ResultSafetyStock.reorder_point)),
            _f.sum(ResultSafetyStock.avg_demand),
        ).filter(ResultSafetyStock.calc_date >= start,
                 ResultSafetyStock.calc_date <= end) \
         .group_by(ResultSafetyStock.calc_date) \
         .order_by(ResultSafetyStock.calc_date).all()

        # 日別ごとの集計（バケット化の元データ）
        daily = {}
        for cd, ss_total, ss_max, ti_total, avg_total in rows:
            daily[cd] = {
                "safety_total": float(ss_total or 0),
                "safety_max": float(ss_max or 0),
                "target_total": float(ti_total or 0),
                "avg_demand_total": float(avg_total or 0),
            }

        def _bucket_key(cd, period):
            if period == "year":
                return cd.strftime("%Y"), cd.strftime("%Y年")
            if period == "month":
                return cd.strftime("%Y-%m"), cd.strftime("%Y年%m月")
            return cd.isoformat(), cd.strftime("%Y-%m-%d")

        buckets = {}
        for cd, agg in daily.items():
            key, label = _bucket_key(cd, period)
            b = buckets.setdefault(key, {
                "period": key, "label": label, "days": 0,
                "safety_total": 0.0, "safety_max": 0.0,
                "target_total": 0.0, "avg_demand_total": 0.0,
            })
            b["days"] += 1
            b["safety_total"] += agg["safety_total"]
            b["safety_max"] = max(b["safety_max"], agg["safety_max"])
            b["target_total"] += agg["target_total"]
            b["avg_demand_total"] += agg["avg_demand_total"]

        n_places = max(len(self.list_places()), 1)
        out = []
        for key in sorted(buckets):
            b = buckets[key]
            n = max(b["days"], 1)
            safety_avg = b["safety_total"] / n
            target_avg = b["target_total"] / n
            turnover = None
            avg_demand_daily = (b["avg_demand_total"] / n) / n_places if b["avg_demand_total"] > 0 else 0.0
            if safety_avg > 0 and avg_demand_daily > 0:
                turnover = (avg_demand_daily * 365.0) / safety_avg
            out.append({
                "period": b["period"], "label": b["label"],
                "days": b["days"],
                "safety_avg": round(safety_avg, 2),
                "safety_max": round(b["safety_max"], 2),
                "target_inventory_avg": round(target_avg, 2),
                "inventory_turnover_annual": round(turnover, 2) if turnover else None,
            })
        return {"period": period, "lookback_days": lookback_days, "items": out}

    def drilldown_summary(self, segment: str | None = None,
                          category: str | None = None,
                          place_id: int | None = None,
                          calc_date: date | None = None) -> dict:
        """セグメント(ABC-XYZ)・カテゴリ・店舗のドリルダウン集計。

        商品×店舗単位の在庫指標（適正在庫量/安全在庫/推奨発注量/回転率）
        を、指定した次元で絞り込んで返す。FR-7-2（セグメント別・カテゴリ別・店舗別）。
        """
        from datetime import date as _date
        cd = calc_date or _date.today()
        ss_list = self.latest_safety_stock(cd)
        rec_list = self.latest_recommendations(cd, status=None)
        rec_by_key = {(r.product_id, r.place_id): r for r in rec_list}
        seg_list = self.latest_segments(cd)
        seg_by_pid = {s.product_id: (s.abc_class or "") + (s.xyz_class or "") for s in seg_list}

        products = {p.id: p for p in self.list_products()}
        places = {p.id: p.place_name for p in self.list_places()}
        cats = sorted({p.category for p in products.values() if p.category})
        segs = sorted({v for v in seg_by_pid.values() if v})

        rows = []
        for ss in ss_list:
            rec = rec_by_key.get((ss.product_id, ss.place_id))
            seg = seg_by_pid.get(ss.product_id, "-")
            prod = products.get(ss.product_id)
            cat = prod.category if prod else None
            if segment and seg != segment:
                continue
            if category and cat != category:
                continue
            if place_id is not None and ss.place_id != place_id:
                continue
            safety = float(ss.safety_stock or 0)
            target = float(getattr(ss, "target_inventory", None) or float(ss.reorder_point or 0))
            avg_demand = float(ss.avg_demand or 0)
            rec_qty = float(rec.recommended_qty or 0) if rec and rec.recommended_qty is not None else 0.0
            rows.append({
                "product_id": ss.product_id,
                "product_name": prod.name if prod else str(ss.product_id),
                "category": cat or "-",
                "place_id": ss.place_id,
                "place_name": places.get(ss.place_id, str(ss.place_id)),
                "segment": seg,
                "avg_demand": round(avg_demand, 2),
                "safety_stock": round(safety, 2),
                "target_inventory": round(target, 2),
                "recommended_qty": round(rec_qty, 2),
            })

        # 集計サマリ
        n = len(rows)
        t_target = sum(r["target_inventory"] for r in rows)
        t_safety = sum(r["safety_stock"] for r in rows)
        t_rec = sum(r["recommended_qty"] for r in rows)
        t_demand = sum(r["avg_demand"] for r in rows)
        turnover = None
        if t_safety > 0 and n > 0:
            place_n = len({r["place_id"] for r in rows}) or 1
            daily_avg = (t_demand / max(n, 1)) / place_n
            turnover = (daily_avg * 365.0) / (t_safety / max(n, 1)) if (t_safety / max(n, 1)) > 0 else None

        return {
            "filters": {
                "segments": segs or [],
                "categories": cats,
                "places": [{"id": pid, "name": name} for pid, name in sorted(places.items())],
            },
            "summary": {
                "item_count": n,
                "target_inventory_total": round(t_target, 2),
                "safety_stock_total": round(t_safety, 2),
                "recommended_qty_total": round(t_rec, 2),
                "avg_demand_total": round(t_demand, 2),
                "place_count": len({r["place_id"] for r in rows}),
                "inventory_turnover_annual": round(turnover, 2) if turnover else None,
            },
            "rows": rows,
        }


