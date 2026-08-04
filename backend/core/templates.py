"""Single shared Jinja2Templates instance for every HTML render path.

Dashboard pages, the auth pages (login / no-modules) and the 403 error page
must all render from this one environment: a Jinja global registered here is
visible to every template, with no per-instance registration to keep in sync.
"""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="frontend/templates")
