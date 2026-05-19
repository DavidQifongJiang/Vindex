from fastapi import FastAPI
from app.api.videos import router

app = FastAPI(title="Vindex")
app.include_router(router)