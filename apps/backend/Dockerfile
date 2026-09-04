# Content backend: API + worker + yt-dlp/ffmpeg engine.
# Same proven base as HomeTube (yt-dlp + ffmpeg preinstalled, multi-arch
# amd64/arm64, Alpine/musl): wheels only, non-root, tini as PID 1.
#
# Pinned to an exact release AND its digest. The tag is for humans; the digest
# is what Docker actually resolves, so the same commit builds the same image
# next year — a floating `:latest` silently changed the engine's most
# regression-prone dependency on every rebuild, with nothing recording which
# version a given image had been built on.
#
# This digest is the multi-arch OCI index, so linux/amd64 and linux/arm64 both
# resolve from it (the CI tag build needs both).
#
# Bumping it is a deliberate, validated act: see
# docs/operations/ytdlp-base-image.md. A scheduled workflow files an issue when
# a newer upstream image appears; it never edits this file.
ARG YTDLP_BASE_VERSION=2026.08.19
ARG YTDLP_BASE_DIGEST=sha256:11c9231bd209580b25e81daad39b3bbbd06c31443d0f9dab2c196e90c6e76156
FROM jauderho/yt-dlp:${YTDLP_BASE_VERSION}@${YTDLP_BASE_DIGEST}

# Re-declared: an ARG consumed by FROM is out of scope in the build stage.
ARG YTDLP_BASE_VERSION
ARG YTDLP_BASE_DIGEST

LABEL org.opencontainers.image.base.name="docker.io/jauderho/yt-dlp:${YTDLP_BASE_VERSION}" \
    org.opencontainers.image.base.digest="${YTDLP_BASE_DIGEST}" \
    org.opencontainers.image.title="Content API" \
    org.opencontainers.image.description="Declarative resource-to-artifact generation engine" \
    org.opencontainers.image.licenses="AGPL-3.0-or-later" \
    org.opencontainers.image.source="https://github.com/LatentNoise/content" \
    org.opencontainers.image.authors="Yann Orieult" \
    org.opencontainers.image.url="https://github.com/LatentNoise/content" \
    org.opencontainers.image.version="0.6.8"

# ttf-dejavu: PDF output for non-Latin scripts. Without a Unicode font the
# renderer falls back to Helvetica, which draws Latin-1 only — a Japanese or
# Cyrillic translation would lose its characters.
RUN apk add --no-cache tini ca-certificates curl ttf-dejavu

# yt-dlp self-update at build time. OFF by default, and deliberately so: it
# used to run unconditionally, which meant the version actually shipped was
# whatever upstream published that morning. The pin above would have been
# decoration — two builds of the same commit could carry different engines, and
# a regression could not be reproduced or attributed.
#
# The trade-off is real: YouTube breaks stale yt-dlp quickly (n-challenge /
# signature changes), so a pinned build does age. That is handled by *noticing*
# rather than by drifting — the weekly check files an issue, the maintainer
# validates and bumps the pin (docs/operations/ytdlp-base-image.md), and the
# running instance warns via CONTENT_YTDLP_MAX_AGE_DAYS.
#
# An operator who would rather have the newest yt-dlp than a reproducible image:
#   docker compose build --build-arg YTDLP_SELF_UPDATE=true content
ARG YTDLP_SELF_UPDATE=false
RUN if [ "$YTDLP_SELF_UPDATE" = "true" ]; then \
        yt-dlp -U 2>&1 | tail -1 || true; \
    fi
# Recorded in the build log: which yt-dlp this image actually carries. Step 6 of
# the update procedure reads it back from a running container.
RUN yt-dlp --version

# Install into a virtual environment, not the base's Python.
#
# The base stopped shipping pip and marked its system Python
# externally-managed (PEP 668), so installing there is now both impossible and
# refused by policy — `pip: not found`, and behind it an EXTERNALLY-MANAGED
# marker that would have said no anyway. `PIP_BREAK_SYSTEM_PACKAGES` used to
# cover the second half; nothing covers the first.
#
# A venv is what PEP 668 points at, and it asks nothing of the base beyond
# `venv` itself, which is stdlib. Verified against both the pinned base and the
# current one: `python3 -m venv` yields a working pip on each, so this is not a
# fix that only works after the next bump.
#
# Everything downstream resolves through it — the dependency install, the
# optional STT extra, and the CMD that starts the app — because it is first on
# PATH. yt-dlp and ffmpeg are untouched: they are base binaries, and yt-dlp
# pins its own interpreter in its shebang.
RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Project metadata first for layer caching.
COPY apps/backend/pyproject.toml ./

# The runtime dependencies come from the pyproject — the file above — rather
# than from a list repeated here.
#
# This used to be a hand-copied list, and that is exactly how the published
# image shipped broken for four releases: 0.5.0 added the uploads route, whose
# UploadFile parameter makes FastAPI require python-multipart at *registration*
# time, the pyproject was not updated either, and the image's list certainly
# was not. `create_app()` then raised on boot, so every tag from 0.5.0 to 0.6.3
# had an API that could not start. Two lists could disagree; one cannot.
#
# `pdf` (reportlab, writing) and `read` (pypdf, reading) ship by default: both
# are pure-Python wheels that install on musl with no toolchain, and a feature
# that only works for whoever added an extra is not a feature you can announce.
# `stt` stays optional below — those wheels are heavy.
#
# --only-binary forces prebuilt wheels (pydantic-core on musl) so the build
# fails fast instead of compiling Rust.
RUN python3 -c "\
import tomllib;\
p = tomllib.load(open('pyproject.toml','rb'))['project'];\
extras = p.get('optional-dependencies', {});\
deps = p['dependencies'] + extras.get('pdf', []) + extras.get('read', []) + extras.get('tts', []);\
open('/tmp/requirements.txt','w').write(chr(10).join(deps))" \
    && cat /tmp/requirements.txt \
    && pip install --no-cache-dir --only-binary=:all: --no-compile \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Optional speech-to-text runner (audio.transcribe → transcript/summary from
# audio). Off by default — the wheels are heavy (ctranslate2). Enable with:
#   docker compose build --build-arg INSTALL_STT=true content
ARG INSTALL_STT=false
RUN if [ "$INSTALL_STT" = "true" ]; then \
        pip install --no-cache-dir --only-binary=:all: --no-compile "faster-whisper>=1.0"; \
    fi

# Typst: the preferred PDF renderer (document.render_pdf). A statically linked
# musl binary, so it needs no runtime dependencies on this Alpine base.
#
# Pinned to an exact release and verified by checksum. The Alpine package lags
# well behind (0.12 vs 0.15) and Typst is pre-1.0 with breaking syntax changes
# between minors, so tracking a distro package would silently change how the
# shipped template compiles. The Python binding is not an option either: it
# publishes no musl wheels.
#
# ~46 MB on disk. Disable with:
#   docker compose build --build-arg INSTALL_TYPST=false content
# ReportLab then renders every PDF, which is why it stays a supported fallback
# rather than a temporary implementation.
ARG INSTALL_TYPST=true
ARG TYPST_VERSION=0.15.1
ARG TYPST_SHA256_X86_64=a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c
ARG TYPST_SHA256_AARCH64=5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee
RUN <<'SH'
set -eux
if [ "$INSTALL_TYPST" != "true" ]; then
    echo "Typst not installed (INSTALL_TYPST=$INSTALL_TYPST)"
    exit 0
fi
case "$(uname -m)" in
    x86_64)  TARGET=x86_64-unknown-linux-musl;  SHA="$TYPST_SHA256_X86_64" ;;
    aarch64) TARGET=aarch64-unknown-linux-musl; SHA="$TYPST_SHA256_AARCH64" ;;
    *)
        # An unsupported architecture is not a build failure: the image stays
        # usable and ReportLab renders PDFs.
        echo "no Typst build for $(uname -m); falling back to ReportLab"
        exit 0
        ;;
esac
ARCHIVE="typst-${TARGET}.tar.xz"
URL="https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${ARCHIVE}"
curl -fsSL -o "/tmp/${ARCHIVE}" "$URL"
echo "${SHA}  /tmp/${ARCHIVE}" | sha256sum -c -
tar -xJf "/tmp/${ARCHIVE}" -C /tmp
install -m 0755 "/tmp/typst-${TARGET}/typst" /usr/local/bin/typst
# Apache-2.0 §4: the licence travels with the binary we redistribute.
mkdir -p /usr/local/share/licenses/typst
cp "/tmp/typst-${TARGET}/LICENSE" /usr/local/share/licenses/typst/ 2>/dev/null || true
rm -rf "/tmp/${ARCHIVE}" "/tmp/typst-${TARGET}"
typst --version
SH

# AGPL §4: the licence travels with the program we distribute.
COPY LICENSE NOTICE ./
COPY apps/backend/content/ ./content/

RUN <<'SH'
set -eux
mkdir -p /data
addgroup -g 1000 content
adduser -D -s /bin/sh -u 1000 -G content content
chown -R content:content /app /data
# Privilege dropper for the entrypoint: the container starts as root only
# long enough to make the mounted state writable (see docker-entrypoint.sh).
apk add --no-cache su-exec
SH

COPY --chmod=0755 apps/backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# No `USER content` here: the entrypoint drops to that user itself, after
# adopting bind mounts Docker created as root. Anything the image runs still
# runs unprivileged — `docker compose exec content id` reports uid 1000.

ENV CONTENT_DATA_DIR=/data \
    CONTENT_DB_PATH=/data/content.db \
    CONTENT_MAX_CONCURRENT_JOBS=2

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "content.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
