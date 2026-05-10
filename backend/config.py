import os
from dotenv import load_dotenv

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_API_KEY = os.getenv("ODOO_API_KEY")


def validate_config():
    missing = []

    if not ODOO_URL:
        missing.append("ODOO_URL")

    if not ODOO_DB:
        missing.append("ODOO_DB")

    if not ODOO_USERNAME:
        missing.append("ODOO_USERNAME")

    if not ODOO_API_KEY:
        missing.append("ODOO_API_KEY")

    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))