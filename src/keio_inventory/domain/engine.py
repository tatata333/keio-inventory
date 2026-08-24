from __future__ import annotations
import os

from dataclasses import asdict
from datetime import date, timedelta

from keio_inventory.demo_data import build_products_and_history, PLACES
from keio_inventory.domain.services.abc_xyz_service import classify_all
from keio_inventory.domain.services.forecast_service import ForecastService
from keio_inventory.domain.services.safety_stock_service import compute_safety_stock
from keio_inventory.domain.services.anomaly_service import AnomalyService
from keio_inventory.domain.services.order_service import OrderService

HORIZON = 14          # forecast horizon (days)
SERVICE_LEVEL = 0.95
HAS_INVENTORY = False
ABC_WINDOW_DAYS = 90  # ABC-XYZ の集計ウィンドウ（デモは全体、実データは直近90日）
PARALLEL_THRESHOLD = 200  # このSKU数以上なら自動で並列計算（それ以下は直列）


def _compute_product_worker(args):
    """商品1件分（×店舗）の計算を単独プロセスで実行するワーカー（並列化用）。"""
    from keio_inventory.domain.services.forecast_service import ForecastService
    from keio_inventory.domain.services.safety_stock_service import compute_safety_stock
    from keio_inventory.domain.services.anomaly_service import AnomalyService
    from keio_inventory.domain.services.order_service import OrderService

    pid = args['product_id']
    name = args['name']
    hist = args['history']
    seg_str = args.get('segment', 'BX')
    lt = float(args.get('lead_time', 7.0))
    places = args['places']
    has_inventory = args['has_inventory']
    on_hand_map = args.get('on_hand_map', {})
    service_level = float(args.get('service_level', 0.95))
    horizon = int(args.get('horizon', 14))

    forecaster = ForecastService()
    anomaly = AnomalyService()
    order = OrderService()
    forecasts, safety_stocks, recommendations, alerts = {}, {}, {}, []
    for pl in places:
        plid = pl['id']
        fc = forecaster.forecast(pid, plid, hist or [0.0], horizon=horizon, segment=seg_str)
        forecasts[(pid, plid)] = fc
        ss = compute_safety_stock(product_id=pid, place_id=plid, daily_forecasts=fc.p50,
                                  lead_time_days=lt, service_level=service_level,
                                  has_inventory=has_inventory, historical_demands=hist)
        safety_stocks[(pid, plid)] = ss
        on_hand = on_hand_map.get((pid, plid), 0.0) if has_inventory else 0.0
        rec = order.recommend(pid, plid,
                              forecast_demand=float(sum(fc.p50[:max(1, int(round(lt)))])),
                              safety_stock=ss.safety_stock, on_hand_qty=on_hand)
        recommendations[(pid, plid)] = rec
        for an in anomaly.run(pid, plid, hist,
                              on_hand=on_hand if has_inventory else None,
                              annual_turnover=None, days_on_hand=None, segment=seg_str):
            alerts.append({'product_id': pid, 'product_name': name, 'place_id': plid,
                           'anomaly_type': an.anomaly_type, 'severity': an.severity,
                           'detail': an.detail, 'recommended_action': an.recommended_action})
    return {'forecasts': forecasts, 'safety_stocks': safety_stocks,
            'recommendations': recommendations, 'alerts': alerts}

class InventoryEngine:
    """需要予測・安全在庫・推奨発注を「商品ごとに個別」に計算するオーケストレータ。

    - demo=True : デモ8商品・ダミー需要（検証用）
    - demo=False（repo 指定）: DBの全商品・POSから商品個別に計算（15万SKU対応）
    """

    def __init__(
        self,
        has_inventory: bool = HAS_INVENTORY,
        on_hand_map: dict[tuple[int, int], float] | None = None,
        lead_time_map: dict[int, float] | None = None,
        repo=None,
        demo: bool = True,
        end_date=None,
    ):
        self.forecaster = ForecastService()
        self.anomaly = AnomalyService()
        self.order = OrderService()
        self.service_level = SERVICE_LEVEL
        self.has_inventory = has_inventory
        self.on_hand_map = on_hand_map or {}
        self.lead_time_map = lead_time_map or {}

        if demo or repo is None:
            self.products, self.history, self._amounts = build_products_and_history()
            self.places = PLACES
        else:
            self.load_from_repo(repo, end_date=end_date)

        self.segments = {}
        self.forecasts = {}
        self.safety_stock = {}
        self.recommendations = {}
        self.alerts = []
        self._alert_seq = 0

    # ---------------------------------------------------------------
    # 実データ（DB全商品）読み込み
    # ---------------------------------------------------------------
    def load_from_repo(self, repo, end_date=None):
        """DBの全商品・店舗・POS需要を読み込み、商品個別計算の対象を作る。

        end_date を指定すると、その日以前のPOS・売上のみを対象とする
        （時系列バックフィル: 各 snap日時点の蓄積データで計算する）。
        """
        products_db = repo.list_products()
        places_db = repo.list_places()

        self.products = []
        for pr in products_db:
            self.products.append({
                "id": pr.id,
                "name": pr.name,
                "sku_code": pr.sku_code,
                "category": pr.category or "",
                "lead_time_days": float(pr.lead_time_days) if pr.lead_time_days is not None else 7.0,
            })
        self.places = [{"id": pl.id, "code": pl.place_code, "name": pl.place_name} for pl in places_db]

        # 日次需要系列（POS・店舗合計）: product_id -> [(date, qty)]
        sales_series = repo.daily_sales_series(days=ABC_WINDOW_DAYS, end_date=end_date)
        amount_by_product = repo.sales_amount_by_product(days=ABC_WINDOW_DAYS, end_date=end_date)

        # product_id -> 日次系列（日付を連続に整列）
        self.history = {}
        self._amounts = []
        for p in self.products:
            pid = p["id"]
            series_raw = sales_series.get(pid, [])
            if series_raw:
                # 日付順に日次数量の配列（欠損日は0埋めはせず、得られた実績のみ）
                sorted_series = sorted(series_raw, key=lambda x: x[0])
                self.history[pid] = [q for _, q in sorted_series]
            else:
                # 販売実績が無い商品も対象（需要=0系列として計算対象にし、除外しない）
                self.history[pid] = []
            self._amounts.append(float(amount_by_product.get(pid, 0.0)))

        if self.has_inventory and not self.on_hand_map:
            self.on_hand_map = repo.on_hand_map_all()
        if hasattr(repo, "measured_lead_time") and not self.lead_time_map:
            for pr in products_db:
                m = repo.measured_lead_time(pr.id)
                if m is not None:
                    self.lead_time_map[pr.id] = m

    # ---------------------------------------------------------------
    # 商品個別の計算（要の処理）
    # ---------------------------------------------------------------
    def run_pipeline_auto(self, max_workers: int = None):
        """商品数に応じて、直列/並列を自動選択して計算する（デフォルト推奨）。

        - SKU数 < PARALLEL_THRESHOLD（200）: 直列（オーバーヘッド回避）
        - SKU数 >= PARALLEL_THRESHOLD  : 並列（ProcessPoolExecutor）
        """
        if len(self.products) < PARALLEL_THRESHOLD:
            return self.run_pipeline()
        return self.run_pipeline_parallel(max_workers=max_workers)

    def run_pipeline(self):
        """商品ごとに個別に ABC-XYZ → 需要予測 → 安全在庫 → 推奨発注 → 異常検知。"""
        pids = [p["id"] for p in self.products]
        daily = [self.history[p["id"]] for p in self.products]

        # 1) ABC-XYZ（商品ごとに売上×需要安定性で分類）
        segs = classify_all(pids, self._amounts, daily)
        for s in segs:
            self.segments[s.product_id] = s

        # 2) 商品×店舗ごと（商品個別の需要予測・安全在庫・推奨発注）
        for prod in self.products:
            pid = prod["id"]
            hist = self.history.get(pid, [])
            seg = self.segments.get(pid)
            seg_str = seg.segment if seg else "BX"
            lt = self.lead_time_map.get(pid, prod["lead_time_days"])

            for pl in self.places:
                plid = pl["id"]
                fc = self.forecaster.forecast(pid, plid, hist or [0.0], horizon=HORIZON, segment=seg_str)
                self.forecasts[(pid, plid)] = fc

                ss = compute_safety_stock(
                    product_id=pid, place_id=plid,
                    daily_forecasts=fc.p50,
                    lead_time_days=lt,
                    service_level=self.service_level,
                    has_inventory=self.has_inventory,
                    historical_demands=hist,
                )
                self.safety_stock[(pid, plid)] = ss

                if self.has_inventory:
                    on_hand = self.on_hand_map.get((pid, plid), 0.0)
                else:
                    on_hand = 0.0

                rec = self.order.recommend(
                    pid, plid,
                    forecast_demand=float(sum(fc.p50[:max(1, int(round(lt)))])),
                    safety_stock=ss.safety_stock,
                    on_hand_qty=on_hand,
                )
                self.recommendations[(pid, plid)] = rec

                # 異常検知
                for an in self.anomaly.run(
                    pid, plid, hist,
                    on_hand=on_hand if self.has_inventory else None,
                    annual_turnover=None,
                    days_on_hand=None,
                    segment=seg_str,
                ):
                    self._alert_seq += 1
                    self.alerts.append({
                        "id": self._alert_seq,
                        "product_id": pid, "product_name": prod["name"],
                        "place_id": plid,
                        "anomaly_type": an.anomaly_type,
                        "severity": an.severity,
                        "status": "open",
                        "detected_at": str(date.today()),
                        "recommended_action": an.recommended_action,
                        "detail": an.detail,
                    })

    def run_pipeline_parallel(self, max_workers: int = None):
        """商品単位の計算を並列（ProcessPoolExecutor）で実行する。15万SKU対応。

        run_pipeline の商品ループを並列化した版。各商品の結果を集約する。
        Windows の注意: モジュールレベル関数 _compute_product_worker を使う。
        """
        from concurrent.futures import ProcessPoolExecutor
        pids = [p['id'] for p in self.products]
        daily = [self.history[p['id']] for p in self.products]

        # ABC-XYZ（これは1回で全商品を分類）
        segs = classify_all(pids, self._amounts, daily)
        for s in segs:
            self.segments[s.product_id] = s

        tasks = []
        for prod in self.products:
            pid = prod['id']
            seg = self.segments.get(pid)
            tasks.append({
                'product_id': pid, 'name': prod['name'],
                'history': self.history.get(pid, []),
                'segment': seg.segment if seg else 'BX',
                'lead_time': self.lead_time_map.get(pid, prod['lead_time_days']),
                'places': self.places,
                'has_inventory': self.has_inventory,
                'on_hand_map': self.on_hand_map,
                'service_level': self.service_level,
                'horizon': HORIZON,
            })

        n = max_workers or min(8, (os.cpu_count() or 2))
        results = []
        with ProcessPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(_compute_product_worker, tasks))

        # 集約
        exec_date = date.today()
        for res in results:
            for k, v in res['forecasts'].items():
                self.forecasts[k] = v
            for k, v in res['safety_stocks'].items():
                self.safety_stock[k] = v
            for k, v in res['recommendations'].items():
                self.recommendations[k] = v
            for a in res['alerts']:
                self._alert_seq += 1
                self.alerts.append({'id': self._alert_seq, 'status': 'open',
                                    'detected_at': str(exec_date), **a})

    def estimated_turnover(self) -> float:
        """在庫回転率(年近似)の概算。売上金額合計 / 平均安全在庫から見積もる。"""
        if not self.safety_stock:
            return 0.0
        sales_total = sum(self._amounts)
        safety_total = sum(float(v.safety_stock or 0) for v in self.safety_stock.values())
        if safety_total <= 0:
            return 0.0
        return round(sales_total / safety_total, 2)

    # ---------------------------------------------------------------
    # ダッシュボード用の集計
    # ---------------------------------------------------------------
    def result(self) -> dict:
        return {
            "segments": [asdict(s) for s in self.segments.values()],
            "forecasts": {
                f"{k[0]}:{k[1]}": {
                    "product_id": v.product_id, "place_id": v.place_id,
                    "model_name": v.model_name,
                    "p50": v.p50[:10], "p80": v.p80[:10], "p95": v.p95[:10],
                }
                for k, v in self.forecasts.items()
            },
            "safety_stock": {
                f"{k[0]}:{k[1]}": asdict(v) for k, v in self.safety_stock.items()
            },
            "recommendations": {
                f"{k[0]}:{k[1]}": asdict(v) for k, v in self.recommendations.items()
            },
            "alerts": self.alerts,
        }
