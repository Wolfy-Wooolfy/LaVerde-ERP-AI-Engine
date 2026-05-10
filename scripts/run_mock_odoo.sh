#!/usr/bin/env bash
# Start the Mock Odoo JSON-RPC server for local development and testing.
# Usage: ./scripts/run_mock_odoo.sh [PORT]
set -euo pipefail

PORT="${1:-18069}"
echo "Starting Mock Odoo server on port $PORT ..."
python -m tests.mock_odoo.server "$PORT"
