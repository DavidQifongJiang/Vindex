import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.videos import router
from app.db.session import get_engine
from app.db.models import Base

app = FastAPI(title="Vindex")
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
LEGACY_FRONTEND_INDEX = FRONTEND_DIR / "index.html"

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

@app.on_event("startup")
def create_tables_on_startup():
    if os.getenv("CREATE_TABLES_ON_STARTUP", "false") == "true":
        Base.metadata.create_all(bind=get_engine())

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "vindex"
    }

@app.get("/", include_in_schema=False)
def frontend():
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return FileResponse(LEGACY_FRONTEND_INDEX)

app.include_router(router)
