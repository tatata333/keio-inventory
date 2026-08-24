@echo off
chcp 65001 >nul
echo ================================================
echo  在庫最適化プラットフォーム セットアップ
echo ================================================
echo.
cd /d %~dp0

REM 依存・DB構築・テストを一括実行
python setup.py %*
if errorlevel 1 (
  echo.
  echo セットアップに失敗しました。エラーを確認してください。
  pause
  exit /b 1
)
echo.
echo セットアップが完了しました。
echo 起動:  python setup.py --server  （または python setup.py --db を実行後）
echo 詳細:  docs/導入・実装・メンテナンス手順書.md
pause