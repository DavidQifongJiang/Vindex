FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
ARG VITE_AUTH_ENABLED=false
ARG VITE_COGNITO_DOMAIN=
ARG VITE_COGNITO_CLIENT_ID=
ARG VITE_COGNITO_REDIRECT_URI=
ENV VITE_AUTH_ENABLED=$VITE_AUTH_ENABLED
ENV VITE_COGNITO_DOMAIN=$VITE_COGNITO_DOMAIN
ENV VITE_COGNITO_CLIENT_ID=$VITE_COGNITO_CLIENT_ID
ENV VITE_COGNITO_REDIRECT_URI=$VITE_COGNITO_REDIRECT_URI
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.12.0 \
    && pip install --no-cache-dir -r requirements.txt
# Bake the models into the image so tasks scaled up from zero start without
# downloading them. Set PRELOAD_MODELS=false for faster local rebuilds.
ARG PRELOAD_MODELS=true
ARG WHISPER_MODEL=tiny
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
RUN if [ "$PRELOAD_MODELS" = "true" ]; then \
      python -c "import whisper; whisper.load_model('$WHISPER_MODEL')" \
      && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$EMBEDDING_MODEL')"; \
    fi
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
COPY --from=frontend /frontend/dist /app/app/frontend/dist
EXPOSE 8000
CMD sh -ec "if [ \"${RUN_MIGRATIONS_ON_STARTUP:-true}\" = \"true\" ]; then alembic upgrade head; fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
