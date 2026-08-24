# 在庫最適化 サンプル実装（2605-22）

動くFastAPIサンプル。設計書（design/）の主要ロジック（需要予測・ABC-XYZ・動的安全在庫・異常検知・推奨発注）を実装し、ダミーデータで即時動作確認できます。

## 起動方法

> 依存: requirements.txt のパッケージ（numpy のみで動作する軽量バックエンド EwmaForecaster を使用）。

```bash
pip install -r requirements.txt
```

```bash
cd sample
PYTHONPATH=src uvicorn keio_inventory.api.main:app --host 127.0.0.1 --port 8000 --reload
```

PowerShell の場合:
```powershell
cd sample
$env:PYTHONPATH = "src"
python -m uvicorn keio_inventory.api.main:app --host 127.0.0.1 --port 8000
```

## 主要エンドポイント（ベース http://127.0.0.1:8000/api/v1）

| メソッド | パス | 説明 |
|---|---|---|
| GET | /health | ヘルスチェック |
| GET | /segment/abc-xyz | ABC-XYZ セグメント一覧 |
| GET | /forecast/{product_id}/{place_id} | 需要予測（P50/P80/P95） |
| GET | /inventory/safety-stock | 動的安全在庫・発注点・推奨発注量一覧 |
| GET | /order/recommendation | 推奨発注一覧 |
| PUT | /order/recommendation/{id} | 推奨発注の調整 |
| POST | /order/recommendation/{id}/approve | 承認 |
| GET | /anomaly/alerts | 異常アラート一覧 |
| POST | /anomaly/alerts/{id}/ack | アラート対応開始 |
| GET | /dashboard/kpi | KPI |
| GET | /settings | 設定（在庫モード等） |

## 設計書との対応

- domain/services/abc_xyz_service.py  → 設計書 4.2
- domain/services/forecast_service.py → 設計書 4.1（EwmaForecaster デフォルト。LightGBM/Prophet を BaseForecaster 実装で差し替え可）
- domain/services/safety_stock_service.py → 設計書 4.3（pos_only / full モード切替・キャリブレーション）
- domain/services/anomaly_service.py → 設計書 4.4
- domain/services/order_service.py → 設計書 4.5

> 在庫データ未蓄積の前提を反映し、has_inventory=False（pos_only モード）で初期構築しています。在庫データ蓄積後は InventoryEngine(has_inventory=True) で full モードへ切り替えられます。


---

## PostgreSQL 永続化版

インメモリ版（port 8000）に加え、結果を **PostgreSQL** に保存し、APIがDBを参照する CQRS 構成のバージョンを提供します。

### 前提
- PostgreSQL 17 が稼働（サービス `postgresql-x64-17`、port 5432）
- 接続情報は環境変数で指定（デフォルト: 127.0.0.1:5432 / keio_inventory / postgres / postgres）

```powershell
$env:DB_HOST="127.0.0.1"; $env:DB_PORT="5432"
$env:DB_NAME="keio_inventory"; $env:DB_USER="postgres"; $env:DB_PASSWORD="postgres"
```

### 1) スキーマ適用（初回のみ）
スキーマは **Alembic マイグレーション**で管理します（ORMモデル = source of truth）。

```powershell
python db/apply_schema.py    # DB自動作成 + alembic upgrade head + db/seed.sql投入
python db/verify.py          # テーブル/行数確認
python db/smoke_test.py      # CRUDスモークテスト
```

### Alembicマイグレーション基盤
- マイグレーション先: `src/keio_inventory/infra/db/migrations/`
- 設定: `sample/alembic.ini`（ASCII のみ。Windows の cp932 読みでも安全）
- モデルを変更したら:
```powershell
python -m alembic revision --autogenerate -m "add xxx"   # 差分から migration 生成
python -m alembic upgrade head                            # 適用
python -m alembic check                                   # モデルとDBの乖離検証
python -m alembic downgrade -1                            # 必要時ロールバック
```

### 2) バッチ相当の結果永続化
```powershell
PYTHONPATH=src; python -m jobs.run_pipeline_db
# => ABC=8, FC=224, SS=16, REC=16, ALERT=6 ... （result_* テーブルへ書込）
```

### 3) DBバックエンドAPI起動（port 8001）
```powershell
PYTHONPATH=src; python -m uvicorn keio_inventory.api.main_db:app --port 8001
```

### モード切替（在庫データ蓄積の有無）
`m_demand_forecast_param` の `inventory_enabled` に応じて計算モードが切り替わります。

| inventory_enabled | mode | 安全在庫の計算 |
|---|---|---|
| false（デフォルト） | pos_only | POS需要分布から概算 |
| true | full | 実在庫 + リードタイム変動（設計書4.3） |

```sql
-- 在庫データが蓄積された後、運用側で true に切替
UPDATE m_demand_forecast_param SET value = '{"value": true}' WHERE param_key = 'inventory_enabled';
```
切替後は `jobs/run_pipeline_db.py` を再実行すると `result_safety_stock.mode='full'` で結果が更新されます。

### 設計書との対応（DB実装）
- `src/keio_inventory/infra/db/migrations/`（Alembic）→ design/02_data_model.md（スキーマ）
- `db/schema.sql`（旧・手動DDL）→ 役割は Alembic マイグレーションに移行（参照用に残置）
- `db/seed.sql` → マスタ・パラメータのシード
- `src/keio_inventory/infra/db/models.py` → ORMモデル（source of truth）
- `src/keio_inventory/infra/db/repository.py` → リポジトリ層（result_* 永続化・読取）
- `jobs/run_pipeline_db.py` → design/06_batch.md（日次バッチ相当・冪等化）
- `src/keio_inventory/api/main_db.py` → design/05_api.md（DB参照のCQRS API）

---

## Airflow による自動バッチ処理（本番化）

週次・日次の自動バッチ処理を Airflow（Docker Compose）で実行します。

### 構成
- 実行基盤: Docker Compose（airflow/docker-compose.yaml）
  - webserver (UI: http://localhost:8080) / scheduler / メタDB(postgres)
- DAG 定義: dags/
  - daily_inventory_pipeline — 毎日 03:30: 需要予測 → 安全在庫 → 推奨発注 + 異常検知
  - weekly_segment — 毎週日曜 01:00: ABC-XYZ 再計算 → 安全在庫再計算
- タスク実装: jobs/tasks.py（run_forecast / run_safety_stock / run_order_recommendation / run_anomaly / run_segment）
- 在庫DB(ホスト:5432)へ host.docker.internal で接続

### 起動手順
```powershell
# (1回目) メタDB初期化
cd sample/airflow
docker compose up -d
docker compose run --rm scheduler airflow db migrate   # AirflowメタDBスキーマ作成

# 起動
docker compose up -d
```

### 検証済み
- airflow dags trigger で各 DAG を手動実行し、全タスク success を確認
- 結果はホストの在庫DB(result_*)に永続化される（再実行は冪等）

### 注意（本番化の要点）
- SQLAlchemy バージョン: Airflow 同梱は 1.4。在庫コードを declarative_base()（1.4/2.0両対応）に統一済み。
- DAG の sys.path: コンテナ内 DAG プロセッサは PYTHONPATH を引き継がないため、DAG 先頭で src/jobs を明示追加。
- _PIP_ADDITIONAL_REQUIREMENTS: numpy/pandas/scipy/psycopg2 を起動時投入（sqlalchemy は固定しない）。
- 将来のスケーリングは Executor を Celery/Kubernetes に切替可能。

### 認証（webserver ログイン）
- 既定ユーザー: `admin` / `admin123`（Admin ロール）
- ユーザー作成:
\`\`\`powershell
docker exec airflow-scheduler-1 airflow users create --username admin --role Admin --email admin@keio-atman.example --password 変更する
\`\`\`
- 再現スクリプト: `airflow/setup_users.sh`（admin + viewer を作成）
- 認証方式: Airflow 標準の flask-login（Airflow 2.10）。未認証は /login へリダイレクト。
  - 注意: `AUTH_ROLE_PUBLIC` は空のままにして未認証アクセスを禁止（環境変数で `None` にしないこと）。

---

## ダッシュボード連携（バッチ結果の可視化）

Airflowが書き込んだ在庫DB(result_*)を、DBバックエンドAPI経由でブラウザ表示します。

### 起動
```powershell
# DBバックエンドAPI(port 8001)を起動後
cd sample
$env:DB_HOST='127.0.0.1'; $env:DB_PORT='5432'; $env:DB_NAME='keio_inventory'; $env:DB_USER='postgres'; $env:DB_PASSWORD='postgres'
$env:PYTHONPATH='src'
python -m uvicorn keio_inventory.api.main_db:app --port 8001
```

### アクセス
- ダッシュボード: http://127.0.0.1:8001/  （または /dashboard）
- KPI集計API: http://127.0.0.1:8001/api/v1/dashboard/kpi

### ダッシュボード内容(Chart.js)
- KPIカード: 在庫回転率(年近似) / 安全在庫トータル / 推奨発注量トータル / 未対応アラート / 対象商品
- ABC-XYZ 構成（円グラフ）
- 異常アラート種別（棒グラフ）
- 推奨発注ステータス表（pending / approved）

### 処理フロー
`Airflowバッチ(jobs/tasks) → 在庫DB(result_*) → /api/v1/dashboard/kpi → HTMLダッシュボード`

### 実装ファイル
- 集計ロジック: `src/keio_inventory/infra/db/repository.py` の `dashboard_summary()`
- API/HTML: `src/keio_inventory/api/main_db.py`（`/api/v1/dashboard/kpi` と `_DASHBOARD_HTML`）

> 備考: ダッシュボードの Chart.js は CDN から読み込みます。社内ネットワークで CDN が使えない場合は chart.umd.min.js をローカルに配置してください。

---

## 在庫データ蓄積時の full モード自動切替

在庫データ(inventory_daily)の蓄積状況を自動判定し、`pos_only` ⇄ `full` を自動で切り替えます。

### 自動判定ロジック（jobs/tasks.py `_resolve_mode`）
- **対象範囲**: 全(商品 × 店舗) の組
- **蓄積率**: 直近14日以内に在庫データが存在する商品×店舗の割合
- **切替条件** (両方満たすと full へ自動切替):
  - 蓄積率 ≥ 0.8（80%）
  - 最新在庫データが直近7日以内（鮮度OK）
- full に切り替わると `m_demand_forecast_param.inventory_enabled=true` に自動反映
- 蓄積不足・データが古くなると pos_only に戻る(安全側)

### full モードで使う実データ
- **実在庫**: `inventory_daily` の最新 `on_hand_qty`（推奨発注量の計算に使用）
- **実測リードタイム**: `purchase_history` の expected_date - po_date の平均

### デモ手順（在庫データを投入して自動切替を確認）
```powershell
cd sample
python -m jobs.seed_inventory     # 全商品×店舗に直近14日分の在庫+入荷履歴を投入
python -m jobs.run_pipeline_db    # 蓄積率100%を検出し full に自動切替・計算

# 確認
#  - API: /api/v1/settings の inventory_mode=full
#  - API: /api/v1/dashboard/kpi の inventory.mode=full
```

### 実装ファイル
- 判定/取得: `src/keio_inventory/infra/db/repository.py`（inventory_accumulation_status / latest_on_hand / measured_lead_time）
- 自動モード判定: `jobs/tasks.py`（_resolve_mode / _resolve_and_build_engine）
- 実在庫注入: `src/keio_inventory/domain/engine.py`（on_hand_map）
- デモ用シード: `jobs/seed_inventory.py`

### ダッシュボード/APIでの確認
- バッチ実行後、`/api/v1/settings` と `/api/v1/dashboard/kpi` が `mode=full` を表示

---

## KPI実証シミュレーション（提案の核心）

導入前（現行・人手発注） vs 導入後（本システム）を 180日・8商品で時系列シミュレートし、提案KPI(+20%/-5%/-30%)を実証します。

### 実行
```bash
cd sample
PYTHONPATH=src python -m jobs.kpi_sim   # シミュレーション結果表示
python -m pytest tests/test_kpi_sim.py   # KPI目標達成の回帰テスト
```

### 実証結果（現時点）
- 在庫回転率: +32%（目標+20%）
- 欠品率: -87%（目標-5%）
- 廃棄ロス: -39%（目標-30%）

詳細は `KPI実証レポート.md` を参照。


---

## ドキュメント一覧（成果物の場所）

| 資料 | 場所 |
|---|---|
| 導入・実装・メンテナンス手順書 | `sample/docs/導入・実装・メンテナンス手順書.md` |
| 提案書（最終版） | `keio/提出物/2605-22_在庫効率最適化_提案書.md` (+PDF) |
| 提案スライド（提出用15p） | `keio/opendesign/inventory-optimization-deck_提出用.pdf` |
| KPI実証レポート | `keio/レポート/KPI実証レポート.md` |
| 品質・セキュリティチェック | `keio/レポート/品質セキュリティチェックレポート.md` |
| 設計書一式 | `keio/design/` |
| 営業（応募・問い合わせ） | `keio/提出物/応募パッケージ.md` 等 |

## 並列計算（自動切替）

- SKU数に応じて**直列/並列を自動切替**（`run_pipeline_auto`）。
  - 200未満: 直列（オーバーヘッド回避）
  - 200以上: 並列（ProcessPoolExecutor）
- **Windows 注意**: 並列実行は multiprocessing のため、エントリポイントに `if __name__ == "__main__":` ガードが必要。対話シェル・Jupyter での run_pipeline_parallel 直接呼び出しは spawn に失敗する場合あり。
- 本番サーバー（Linux 等）では制約なく並列動作。Windows はローカル開発・デモ用。
