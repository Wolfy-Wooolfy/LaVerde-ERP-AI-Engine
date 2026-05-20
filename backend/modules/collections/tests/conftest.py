import os

_defaults = {
    "ODOO_URL":             "http://127.0.0.1:18069",
    "ODOO_DB":              "testdb",
    "ODOO_USERNAME":        "test@test.com",
    "ODOO_API_KEY":         "test-api-key",
    "BASIC_AUTH_USERNAME":  "testadmin",
    "BASIC_AUTH_PASSWORD":  "testpass",
    "ENVIRONMENT":          "development",
    "DEBUG":                "true",
    "LOG_LEVEL":            "DEBUG",
    "CACHE_TTL_SECONDS":    "5",
}
for _k, _v in _defaults.items():
    os.environ.setdefault(_k, _v)
