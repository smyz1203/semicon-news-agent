#!/bin/sh
# Vercel이 배포할 때 실행하는 빌드: web/ + data/ → _site/ (agent/build_site.py)
# Vercel 빌드 서버에는 여러 Python이 있으므로 3.12 이상을 골라 쓰고, 필요한 패키지(PyYAML)만 따로 설치한다.
set -e

PY=$(command -v python3.12 || command -v python3.13 || command -v python3.14 || command -v python3)
echo "Python: $PY ($("$PY" --version))"

"$PY" -m venv .vercel-venv
.vercel-venv/bin/python -m pip install --quiet --disable-pip-version-check PyYAML==6.0.3
.vercel-venv/bin/python -m agent.build_site
