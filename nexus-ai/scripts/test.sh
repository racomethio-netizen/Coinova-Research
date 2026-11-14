#!/usr/bin/env bash
# Run the full local check: lint, types, unit + exchange + security tests, and the
# integration tests when NEXUS_TEST_DATABASE_URL / NEXUS_TEST_REDIS_URL are set.
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
mypy -p nexus_common -p nexus_config -p nexus_db -p nexus_security -p nexus_exchange -p nexus_market -p nexus_strategies -p nexus_ai -p nexus_risk -p nexus_backtest -p nexus_execution -p nexus_notify -p nexus_api
python -m pytest "$@"
