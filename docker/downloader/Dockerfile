ARG BASE_IMAGE=python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

FROM ${BASE_IMAGE} AS builder
COPY docker/downloader/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 && /opt/venv/bin/pip uninstall -y pip

FROM ${BASE_IMAGE}
ARG APP_UID=10001
RUN python -m pip uninstall -y pip \
 && addgroup -S -g ${APP_UID} downloader \
 && adduser -S -D -H -u ${APP_UID} -G downloader downloader
COPY --from=builder /opt/venv /opt/venv
COPY scripts/download_model.py /app/download_model.py
ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HOME=/tmp/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1
USER ${APP_UID}:${APP_UID}
ENTRYPOINT ["python", "/app/download_model.py"]
