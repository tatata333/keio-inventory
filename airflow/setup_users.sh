#!/usr/bin/env bash
# Airflow ユーザー作成スクリプト（開発用）
#
# 使い方:  docker exec airflow-scheduler-1 bash -c 'bash -s' < airflow/setup_users.sh
#   または、うこの中身の docker exec コマンドをそのまま実行。
set -e

SC_CMD='docker exec airflow-scheduler-1'

# Admin (運用管理者) -- 環境変数 AIRFLOW_ADMIN_* で上書き可能
ADMIN_USER=${AIRFLOW_ADMIN_USER:-admin}
ADMIN_PW=${AIRFLOW_ADMIN_PW:-admin123}
ADMIN_MAIL=${AIRFLOW_ADMIN_MAIL:-admin@keio-atman.example}

echo '== create/update Admin =='
docker exec airflow-scheduler-1 airflow users create \
  --username "$ADMIN_USER" \
  --firstname Keio \
  --lastname Admin \
  --role Admin \
  --email "$ADMIN_MAIL" \
  --password "$ADMIN_PW" 2>/dev/null || \
docker exec airflow-scheduler-1 airflow users create \
  --username "$ADMIN_USER" --firstname Keio --lastname Admin --role Admin \
  --email "$ADMIN_MAIL" --password "$ADMIN_PW"

# Viewer (参照専用: 経営・確認役) -- 必要なら追加
VIEWER_USER=${AIRFLOW_VIEWER_USER:-viewer}
VIEWER_PW=${AIRFLOW_VIEWER_PW:-viewer123}
echo '== create Viewer =='
docker exec airflow-scheduler-1 airflow users create \
  --username "$VIEWER_USER" --firstname Viewer --lastname Readonly --role Viewer \
  --email view@keio-atman.example --password "$VIEWER_PW" 2>/dev/null || echo "viewer exists"

echo '== current users =='
docker exec airflow-scheduler-1 airflow users list