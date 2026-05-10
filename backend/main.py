from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from backend.crm_engine import CrmEngine

app = FastAPI(
    title="CRM AI Engine",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "CRM AI Engine",
        "mode": "read_only",
    }


@app.get("/crm/summary")
def crm_summary():
    try:
        engine = CrmEngine()
        return engine.summary()
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(error),
            },
        )


@app.get("/crm/followup-risk")
def crm_followup_risk():
    try:
        engine = CrmEngine()
        return {
            "mode": "read_only",
            "scope": "resolved_opportunities_only",
            "followup_risk": {
                "overdue_by_salesperson": engine.overdue_by_salesperson(),
                "overdue_by_team": engine.overdue_by_team(),
                "overdue_by_stage": engine.overdue_by_stage(),
                "overdue_matrix_by_team_salesperson_stage": engine.overdue_matrix_by_team_salesperson_stage(),
            },
        }
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(error),
            },
        )


@app.get("/crm/data-quality/missing-contact")
def crm_missing_contact():
    try:
        engine = CrmEngine()
        return {
            "mode": "read_only",
            "scope": "resolved_opportunities_only",
            "missing_contact_details": engine.missing_contact_details(),
        }
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(error),
            },
        )


@app.get("/dashboard")
def dashboard(request: Request):
    try:
        engine = CrmEngine()
        data = engine.summary()

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "mode": data["mode"],
                "scope": data["scope"],
                "summary": data["summary"],
                "data_quality": data["data_quality"],
                "followup_risk": data["followup_risk"],
            },
        )
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(error),
            },
        )


@app.get("/data-quality/missing-contact")
def missing_contact_page(request: Request):
    try:
        engine = CrmEngine()

        return templates.TemplateResponse(
            "missing_contact.html",
            {
                "request": request,
                "mode": "read_only",
                "scope": "resolved_opportunities_only",
                "rows": engine.missing_contact_details(),
            },
        )
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(error),
            },
        )