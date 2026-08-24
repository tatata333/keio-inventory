@echo off
chcp 65001 >nul
echo 在庫最適化プラットフォームを起動しています...
cd /d %~dp0
python setup.py --server
pause