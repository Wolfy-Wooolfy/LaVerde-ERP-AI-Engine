import requests

from backend.config import (
    ODOO_URL,
    ODOO_DB,
    ODOO_USERNAME,
    ODOO_API_KEY,
    validate_config,
)


class OdooClient:
    def __init__(self):
        validate_config()
        self.url = ODOO_URL.rstrip("/") + "/jsonrpc"
        self.db = ODOO_DB
        self.username = ODOO_USERNAME
        self.api_key = ODOO_API_KEY
        self.uid = None

    def _call(self, service, method, args):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": service,
                "method": method,
                "args": args,
            },
            "id": 1,
        }

        response = requests.post(self.url, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()

        if "error" in data:
            raise RuntimeError(data["error"])

        return data.get("result")

    def authenticate(self):
        if self.uid:
            return self.uid

        self.uid = self._call(
            "common",
            "authenticate",
            [
                self.db,
                self.username,
                self.api_key,
                {},
            ],
        )

        if not self.uid:
            raise RuntimeError("Odoo authentication failed")

        return self.uid

    def execute_kw(self, model, method, args=None, kwargs=None):
        uid = self.authenticate()

        if args is None:
            args = []

        if kwargs is None:
            kwargs = {}

        return self._call(
            "object",
            "execute_kw",
            [
                self.db,
                uid,
                self.api_key,
                model,
                method,
                args,
                kwargs,
            ],
        )