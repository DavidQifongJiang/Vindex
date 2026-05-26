from fastapi import FastAPI
from app.api.videos import router
from app.db.session import get_engine
from app.db.models import Base


import os

if os.getenv("CREATE_TABLES_ON_STARTUP", "false") == "true":
    Base.metadata.create_all(bind=get_engine())
app = FastAPI(title="Vindex")
app.include_router(router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "vindex"
    }