FROM python:3.12-slim-bookworm

ARG APP_VERSION=development
LABEL org.opencontainers.image.title="client-server-test"
LABEL org.opencontainers.image.revision="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION} \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50051 \
    DATABASE_PATH=/data/metering.sqlite3 \
    SEED_PATH=/app/server/data.json

WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN python -m pip install --no-cache-dir -r server/requirements.txt \
    && groupadd --gid 1000 metering \
    && useradd --uid 1000 --gid 1000 --no-create-home metering \
    && mkdir /data && chown 1000:1000 /data
COPY --chown=1000:1000 server /app/server
USER 1000:1000

EXPOSE 8000 50051
CMD ["python", "-m", "server"]
