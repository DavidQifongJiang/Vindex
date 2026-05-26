from fastapi import FastAPI
from app.api.videos import router
from app.db.session import engine
from app.db.models import Base


Base.metadata.create_all(bind=engine)
app = FastAPI(title="Vindex")
app.include_router(router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "vindex"
    }