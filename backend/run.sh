#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 优先复用仓库内 .venv；解释器软链失效或依赖缺失时回落到系统 python3，
# 两者都没有 fastapi 才新建虚拟环境安装依赖。
if [ -x .venv/bin/python ] && .venv/bin/python -c "import fastapi" >/dev/null 2>&1; then
  PY=.venv/bin/python
elif python3 -c "import fastapi" >/dev/null 2>&1; then
  PY=python3
else
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  .venv/bin/pip install -q -r requirements.txt
  PY=.venv/bin/python
fi

exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
