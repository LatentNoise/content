# Content front-end (Content Console): Streamlit, a pure API client of the back-end.
# Built from the REPO ROOT (compose sets context: .) so the shared
# content-sdk package is available to install — one client for all UIs.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Content Admin" \
    org.opencontainers.image.description="Operations console for the Content engine" \
    org.opencontainers.image.licenses="AGPL-3.0-or-later" \
    org.opencontainers.image.source="https://github.com/LatentNoise/content" \
    org.opencontainers.image.authors="Yann Orieult"

WORKDIR /app

# --no-compile: pip writes ~76 MB of .pyc into site-packages otherwise,
# and this layer is ours, so skipping them really shrinks the image (the
# stdlib's own bytecode lives in the base layer and cannot be reclaimed).
# The cost is compiling on first import, once, in a long-running server.
# Same choice the backend image already makes.
RUN pip install --no-cache-dir --no-compile "streamlit>=1.40"

# The official SDK — the single client for the /api/v1 contract (its deps bring
# httpx + pydantic). The UI uses its dict-compat surface (content_sdk.compat).
COPY packages/python-sdk/ /tmp/content-sdk/
RUN pip install --no-cache-dir --no-compile /tmp/content-sdk \
    && rm -rf /tmp/content-sdk

# AGPL §4: the licence travels with the program we distribute.
COPY LICENSE NOTICE ./
COPY apps/web-admin/app.py ./
COPY apps/web-admin/.streamlit/ ./.streamlit/

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    CONTENT_API_URL=http://content:8000

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).status==200 else 1)"

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
