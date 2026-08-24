"""ワンクリック・セットアップ（自動初期化）

使い方:
  python setup.py            # 全自動セットアップ(依存/DB/シード/テスト/起動)
  python setup.py --test     # テストのみ
  python setup.py --db       # DB構築のみ
  python setup.py --server   # DB構築後にAPI起動をブロック実行

環境変数: DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD（デフォルト: postgres@127.0.0.1:5432/keio_inventory）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")


def _env_path() -> dict:
    env = dict(os.environ)
    cur = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = SRC + (os.pathsep + cur if cur else "")
    return env


def _run(cmd: list[str], cwd=HERE, env=None, capture=False):
    print(">>> " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env or _env_path(), capture_output=capture, text=True)
    if r.returncode != 0:
        print(r.stdout or "")
        print(r.stderr or "")
        raise SystemExit(f"FAILED: {' '.join(cmd)}")
    return r


def install_deps():
    print("==> Python 依存をインストール ...")
    _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def build_db():
    print("==> DB構築（作成・スキーマ・シード）...")
    _run([sys.executable, "db/apply_schema.py"], env=_env_path())


def verify():
    print("==> スキーマ整合とテスト ...")
    _run([sys.executable, "-m", "alembic", "check"], env=_env_path())
    _run([sys.executable, "-m", "pytest", "-q"], env=_env_path())


def run_server():
    print("==> APIを起動します（Ctrl+C で停止）...")
    _run([sys.executable, "-m", "uvicorn", "keio_inventory.api.main_db:app",
          "--host", "127.0.0.1", "--port", "8001"], env=_env_path())


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "all"
    if mode in ("all", "--all"):
        install_deps()
        build_db()
        verify()
        print("✔ セットアップ完了。API起動: python setup.py --server")
    elif mode == "--test":
        verify()
    elif mode == "--db":
        build_db()
    elif mode == "--server":
        build_db()
        run_server()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
