FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates expect \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY mssp_pipeline ./mssp_pipeline
COPY bin ./bin
COPY docker/entrypoint.sh /usr/local/bin/mssp-entrypoint
COPY docker/bootstrap-config.sh /usr/local/bin/mssp-bootstrap-config

ARG PIP_EXTRAS=processing
RUN python -m pip install --upgrade pip \
    && pip install ".[$PIP_EXTRAS]"

RUN set -eux; \
    if [ -f /app/bin/acoms-cli-linux ]; then cp /app/bin/acoms-cli-linux /app/bin/acoms-cli; fi; \
    chmod +x /usr/local/bin/mssp-entrypoint /usr/local/bin/mssp-bootstrap-config; \
    if [ -f /app/bin/acoms-cli ]; then chmod +x /app/bin/acoms-cli; fi

ENTRYPOINT ["mssp-entrypoint"]
CMD ["mssp-pipeline"]
