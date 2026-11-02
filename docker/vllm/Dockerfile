ARG BASE_IMAGE=ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

FROM ${BASE_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      libnuma1 \
      libtcmalloc-minimal4 \
      python3.12 \
 && rm -rf /var/lib/apt/lists/*

FROM base AS builder
RUN apt-get update \
 && apt-get install -y --no-install-recommends patch python3.12-venv \
 && rm -rf /var/lib/apt/lists/*
COPY docker/vllm/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python3.12 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 && /opt/venv/bin/pip uninstall -y pip
COPY docker/vllm/patches /tmp/patches
RUN for p in /tmp/patches/*.patch; do \
      patch --forward --strip=1 --directory=/opt/venv/lib/python3.12/site-packages < "$p" || exit 1; \
    done

FROM base AS runtime
ARG APP_UID=10001
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ python3.12-dev \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid ${APP_UID} vllm \
 && useradd --system --uid ${APP_UID} --gid vllm --create-home --home-dir /home/vllm --shell /usr/sbin/nologin vllm
COPY --from=builder /opt/venv /opt/venv
COPY src/vllm_privacy /opt/vllm-privacy/vllm_privacy
ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/opt/vllm-privacy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/vllm \
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4:/opt/venv/lib/libiomp5.so \
    VLLM_LOGGING_CONFIG_PATH=/opt/vllm-privacy/vllm_privacy/logging.json \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1
USER ${APP_UID}:${APP_UID}
WORKDIR /home/vllm
EXPOSE 8000
ENTRYPOINT ["vllm", "serve"]
