# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba
ARG POETRY_VERSION=2.4.1

FROM ${PYTHON_IMAGE} AS builder

ARG POETRY_VERSION
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        g++ \
        gcc \
        libgdal-dev \
        libgeos-dev \
        libopenblas-dev \
        libpq-dev \
        libproj-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install "poetry==${POETRY_VERSION}"

COPY pyproject.toml poetry.lock requirements-storage.txt requirements-telegram.txt ./
RUN poetry install --only main --no-root --sync

COPY README.md LICENSE ./
COPY factfinder ./factfinder
COPY pymorphy2 ./pymorphy2
COPY soika_uds ./soika_uds
COPY geoanalyzer_storage ./geoanalyzer_storage
RUN poetry install --only main --sync \
    && .venv/bin/python -m pip install \
        --require-hashes \
        --no-deps \
        -r requirements-storage.txt \
    && .venv/bin/python -m pip install \
        --require-hashes \
        --no-deps \
        -r requirements-telegram.txt \
    && .venv/bin/python -c "import telethon; print(telethon.__version__)"

FROM ${PYTHON_IMAGE} AS runtime-base

ARG EXPECTED_GDAL=3.6
ARG EXPECTED_GEOS=3.11
ARG EXPECTED_PROJ=9.1

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/app/.venv/bin:${PATH} \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SOIKA_DATA_DIR=/var/lib/soika \
    SOIKA_MODEL_DIR=/var/cache/soika/models \
    SOIKA_EXPECTED_GDAL=${EXPECTED_GDAL} \
    SOIKA_EXPECTED_GEOS=${EXPECTED_GEOS} \
    SOIKA_EXPECTED_PROJ=${EXPECTED_PROJ}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal32 \
        libgeos-c1v5 \
        libgomp1 \
        libopenblas0-pthread \
        libpq5 \
        libproj25 \
        proj-bin \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && gdalinfo --version | grep -q "GDAL ${EXPECTED_GDAL}" \
    && dpkg-query -W -f='${Version}' libgeos-c1v5 | grep -q "^${EXPECTED_GEOS}" \
    && proj 2>&1 | grep -q "Rel. ${EXPECTED_PROJ}"

RUN groupadd --gid 10001 soika \
    && useradd --uid 10001 --gid soika --create-home --shell /usr/sbin/nologin soika \
    && mkdir -p /app /var/lib/soika /var/cache/soika/models \
    && chown -R soika:soika /app /var/lib/soika /var/cache/soika

WORKDIR /app
COPY --from=builder --chown=soika:soika /app /app

USER soika
EXPOSE 8080
VOLUME ["/var/lib/soika", "/var/cache/soika/models"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["soika-uds", "serve-probes", "--host", "0.0.0.0", "--port", "8080", "--repository-root", "/app"]

FROM runtime-base AS browser-runtime
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/soika/playwright
USER root
COPY requirements-browser.txt /tmp/requirements-browser.txt
RUN python -m pip install \
        --require-hashes \
        --no-deps \
        -r /tmp/requirements-browser.txt \
    && mkdir -p "${PLAYWRIGHT_BROWSERS_PATH}" \
    && python -m playwright install --with-deps chromium \
    && python -c "from playwright.sync_api import sync_playwright; print('playwright-runtime-ok')" \
    && rm -f /tmp/requirements-browser.txt \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R soika:soika "${PLAYWRIGHT_BROWSERS_PATH}"
USER soika

FROM browser-runtime AS cpu
ENV SOIKA_DEVICE=cpu \
    SOIKA_REQUIRE_CUDA=false

FROM runtime-base AS gpu
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    NVIDIA_VISIBLE_DEVICES=all \
    SOIKA_DEVICE=cuda \
    SOIKA_REQUIRE_CUDA=true

FROM cpu AS production
