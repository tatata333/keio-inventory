#!/usr/bin/env bash
# 在庫最適化プラットフォーム セットアップ (macOS/Linux)
set -e
cd "$(dirname "$0")"

echo '================================================'
echo ' 在庫最適化プラットフォーム セットアップ'
echo '================================================'

# 仮想環境（任意・有れば使う）
if [ -d .venv ]; then source .venv/bin/activate; fi

python setup.py "$@"

echo
echo 'セットアップ完了。起動: python setup.py --server'