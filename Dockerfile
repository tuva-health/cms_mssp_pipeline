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
# Only the Linux build is useful here, and copying it to the final name means
# the wheel built below also carries the correct architecture.
COPY bin/acoms-cli-linux ./bin/acoms-cli
COPY docker/entrypoint.sh /usr/local/bin/mssp-entrypoint
COPY docker/bootstrap-config.sh /usr/local/bin/mssp-bootstrap-config

ARG PIP_EXTRAS=processing
RUN python -m pip install --upgrade pip \
    && pip install ".[$PIP_EXTRAS]"

RUN set -eux; \
    chmod +x /usr/local/bin/mssp-entrypoint /usr/local/bin/mssp-bootstrap-config; \
    chmod +x /app/bin/acoms-cli

ENTRYPOINT ["mssp-entrypoint"]
CMD ["mssp-pipeline"]
