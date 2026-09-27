#!/usr/bin/env bash
# Chạy app từ mã nguồn trên macOS/Linux (lần đầu sẽ tạo môi trường và cài thư viện).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Đang tạo môi trường Python..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements-dev.txt
fi

exec .venv/bin/python -m dichyk "$@"
