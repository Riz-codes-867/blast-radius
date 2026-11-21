#!/usr/bin/env bash
# Rebuilds app/data/map.json.gz from the pinned sqlglot commit. Run once, offline, then commit the result.
# Needs: python3.13 on linux/amd64 (the labeling image), git, and the kit's requirements.lock + tests.json.
set -euo pipefail
KIT=${1:?path to the unpacked kit (requirements.lock, sample/tests.json)}
HERE=$(cd "$(dirname "$0")" && pwd); WORK=${WORK:-$PWD/map-build}
mkdir -p "$WORK" && cd "$WORK"
[ -d sqlglot ] || git clone https://github.com/tobymao/sqlglot.git
git -C sqlglot checkout -q eb11aec4be8cb2122afe713a4816662c3f0fb2fa
python3 -m venv venv && . venv/bin/activate && pip install -q -r "$KIT/requirements.lock" coverage==7.17.4
cd sqlglot && export SKIP_INTEGRATION=0 PYTHONPATH=$PWD:$HERE
rm -rf .coverage-map && mkdir .coverage-map
# 1) which tests execute which lines (worker-process code run in-process, through pickle)
coverage run --rcfile="$HERE/coveragerc" -m pytest -q -p no:cacheprovider -p prewarm_plugin -p syncpool_plugin tests || true
coverage combine --rcfile="$HERE/coveragerc" -q
python3 "$HERE/buildmap.py" .coverage-map/.coverage "$KIT/sample/tests.json" ../linemap.json
# 2) which non-Python files (fixtures) each test opens
OPENMAP_OUT=../openmap.json python3 -m pytest -q -p no:cacheprovider -p openmap_plugin tests || true
cd .. && python3 "$HERE/build_data.py" linemap.json openmap.json "$KIT/sample/tests.json" sqlglot "$HERE/../app/data/map.json.gz"
