#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/oracle/xiaogang/venv/bin/python3.11}"
export PATH="${XG_BUILD_TOOLS_BIN:-/oracle/xiaogang/build-tools/py311/bin}:${PATH}"
OUTPUT_DIR="${1:-${ROOT}/dist/protected-mocktc}"
BUILD_DIR="${OUTPUT_DIR}.build"

"${PYTHON_BIN}" -m py_compile "${ROOT}/mocktc_app/app.py" \
  "${ROOT}/deployment/protected_launcher.py"
rm -rf "${OUTPUT_DIR}" "${BUILD_DIR}"
mkdir -p "${OUTPUT_DIR}" "${BUILD_DIR}/source/mocktc_app"
cp "${ROOT}/mocktc_app/app.py" "${BUILD_DIR}/source/mocktc_app/app.py"
cp "${ROOT}/deployment/protected_launcher.py" "${BUILD_DIR}/source/protected_launcher.py"
cp "${ROOT}/scripts/sync_production_fixtures.py" \
  "${BUILD_DIR}/source/sync_production_fixtures.py"
cp -a "${ROOT}/mocktc_app/templates" "${ROOT}/mocktc_app/static" \
  "${ROOT}/mocktc_app/fixtures" "${BUILD_DIR}/source/mocktc_app/"

(cd "${BUILD_DIR}/source" && PYTHONPATH="${BUILD_DIR}/source" "${PYTHON_BIN}" -m nuitka \
  --standalone --assume-yes-for-downloads --remove-output \
  --output-dir="${BUILD_DIR}" --output-filename=xg-mocktc \
  --include-package=mocktc_app --include-package=gunicorn \
  --include-data-dir="${BUILD_DIR}/source/mocktc_app/templates=mocktc_app/templates" \
  --include-data-dir="${BUILD_DIR}/source/mocktc_app/static=mocktc_app/static" \
  --include-data-dir="${BUILD_DIR}/source/mocktc_app/fixtures=mocktc_app/fixtures" \
  "${BUILD_DIR}/source/protected_launcher.py")

mv "${BUILD_DIR}/protected_launcher.dist" "${OUTPUT_DIR}/runtime"
# The fixture synchronizer is an operator-only maintenance utility.  Build it
# as a separate native bundle so the air-gapped delivery medium can carry the
# precise, reviewable sync contract without exposing its Python source.
(cd "${BUILD_DIR}/source" && PYTHONPATH="${BUILD_DIR}/source" "${PYTHON_BIN}" -m nuitka \
  --standalone --assume-yes-for-downloads --remove-output \
  --output-dir="${BUILD_DIR}" --output-filename=xg-mocktc-fixture-sync \
  sync_production_fixtures.py)
mkdir -p "${OUTPUT_DIR}/maintenance"
mv "${BUILD_DIR}/sync_production_fixtures.dist" \
  "${OUTPUT_DIR}/maintenance/mocktc-fixture-sync"
test -x "${OUTPUT_DIR}/maintenance/mocktc-fixture-sync/xg-mocktc-fixture-sync" \
  || { echo "protected MockTC fixture synchronizer build failed" >&2; exit 1; }
find "${OUTPUT_DIR}" -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' -o -name '*.map' \) \
  -print -quit | grep -q . \
  && { echo "protected MockTC release contains source artifacts" >&2; exit 1; } || true
find "${OUTPUT_DIR}" -type f -exec chmod go-w {} +
printf '%s\n' "$(git -C "${ROOT}" rev-parse HEAD)" > "${OUTPUT_DIR}/SOURCE_COMMIT"
sha256sum "${OUTPUT_DIR}/runtime/xg-mocktc" > "${OUTPUT_DIR}/SHA256SUMS.txt"
echo "protected MockTC: ${OUTPUT_DIR}"
