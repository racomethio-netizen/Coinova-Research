#!/usr/bin/env bash
# Dependency vulnerability audit (Python + dashboard) and static security tests.
# Behind a TLS-inspecting proxy, set REQUESTS_CA_BUNDLE for pip-audit.
set -euo pipefail
cd "$(dirname "$0")/.."
pip-audit --skip-editable --progress-spinner off
(cd apps/dashboard && npm audit)
python -m pytest -q tests/security
