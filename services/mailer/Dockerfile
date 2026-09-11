# LatentNoise Mailer — one small image, no build step.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAILER_DB_PATH=/data/mailer.db

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY mailer /app/mailer
RUN pip install --no-cache-dir . && mkdir -p /data

# Runs as a non-root user: this process holds the SMTP credentials of the
# whole estate, so it gets no more privilege than sending needs.
RUN useradd --system --uid 10001 mailer && chown -R mailer:mailer /data
USER mailer

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "mailer.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
