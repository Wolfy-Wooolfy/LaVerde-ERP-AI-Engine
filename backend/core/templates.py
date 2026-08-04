"""Single shared Jinja2Templates instance for every HTML render path.

Dashboard pages, the auth pages (login / no-modules) and the 403 error page
must all render from this one environment: a Jinja global registered here is
visible to every template, with no per-instance registration to keep in sync.
"""

from fastapi.templating import Jinja2Templates

from backend.core.static_manifest import static_url

templates = Jinja2Templates(directory="frontend/templates")

# The single registration point: because every render path shares this
# environment, static_url is available in base.html, the standalone pages
# (login / 403 / no_modules) and every template extending base.html.
templates.env.globals["static_url"] = static_url
