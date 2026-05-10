# Start the Mock Odoo JSON-RPC server for local development and testing.
# Usage: .\scripts\run_mock_odoo.ps1 [Port]
param([int]$Port = 18069)
Write-Host "Starting Mock Odoo server on port $Port ..."
python -m tests.mock_odoo.server $Port
