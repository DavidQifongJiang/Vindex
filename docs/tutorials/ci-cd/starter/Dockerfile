FROM python:3.11-slim
WORKDIR /app
ARG RELEASE_SHA=local
ARG FAIL_READY=0
ENV RELEASE_SHA=${RELEASE_SHA} \
    DEMO_FAIL_READY=${FAIL_READY} \
    HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY app.py /app/app.py
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=3s --start-period=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=2)"
CMD ["python", "app.py"]
