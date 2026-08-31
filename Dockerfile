# syntax=docker/dockerfile:1
#
# Deterministic, hardened runtime image for the MSSP pipeline.
#
# Generic (upstream) properties:
#   * The base image is pinned by digest so a clean checkout reproduces the same
#     filesystem starting point.
#   * Python dependencies are installed frozen from uv.lock (no resolver drift).
#   * The bundled CMS ACO-MS CLI is verified against a recorded checksum.
#   * Full source-commit / release / dependency provenance is baked in as build
#     arguments and OCI image labels so the running image is traceable to its
#     source.
#   * The runtime process runs as a non-root user.
#
# Client-specific values (which output backends to install, the registry the
# image is pushed to, the release id and destinations) are supplied by the build
# policy at build time -- never baked into this file. The default extras keep the
# image usable from a clean checkout with only the core processing engine.
FROM python:3.11-slim@sha256:1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6

# Release provenance. These are supplied by scripts/build-and-push-image.sh from
# the clean checkout being built; they default to "unknown" so an ad-hoc local
# build still succeeds.
ARG SOURCE_COMMIT=unknown
ARG RELEASE_ID=unknown
ARG DEPENDENCY_CHECKSUM=unknown

# Which optional dependency groups to install into the image. The set of backends
# is a client build choice; the *frozen* install is the generic guarantee.
ARG PIP_EXTRAS=processing

# Pinned uv installer for a reproducible dependency install.
ARG UV_VERSION=0.10.9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:$PATH" \
    MSSP_SOURCE_COMMIT="$SOURCE_COMMIT" \
    MSSP_RELEASE_ID="$RELEASE_ID" \
    MSSP_DEPENDENCY_CHECKSUM="$DEPENDENCY_CHECKSUM"

LABEL org.opencontainers.image.title="mssp-pipeline" \
      org.opencontainers.image.revision="$SOURCE_COMMIT" \
      org.opencontainers.image.version="$RELEASE_ID" \
      com.z2healthinsights.mssp.dependency-checksum="$DEPENDENCY_CHECKSUM"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates expect \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Created before the application is copied so the copied
# tree and virtualenv can be owned by it.
RUN groupadd --system --gid 10001 mssp \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/mssp mssp

COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY mssp_pipeline ./mssp_pipeline
COPY bin ./bin
COPY release ./release
COPY docker/entrypoint.sh /usr/local/bin/mssp-entrypoint
COPY docker/bootstrap-config.sh /usr/local/bin/mssp-bootstrap-config

# Deterministic, frozen dependency install. uv.lock fixes every transitive
# version; --frozen refuses to update it, so the resolved environment is a
# function of the checkout alone. The extras list is comma-separated and split
# with POSIX word-splitting (this RUN executes under /bin/sh).
RUN set -eu; \
    python -m pip install "uv==${UV_VERSION}"; \
    extra_flags=""; \
    old_ifs="$IFS"; IFS=','; \
    for _extra in $PIP_EXTRAS; do \
      _extra="$(printf '%s' "$_extra" | tr -d '[:space:]')"; \
      if [ -n "$_extra" ]; then extra_flags="$extra_flags --extra $_extra"; fi; \
    done; \
    IFS="$old_ifs"; \
    uv sync --frozen --no-dev $extra_flags

# Verify the bundled CMS ACO-MS CLI against its recorded checksum, then select
# the Linux build as the runnable binary.
RUN set -eux; \
    sha256sum --check release/cms-binaries.sha256; \
    if [ -f /app/bin/acoms-cli-linux ]; then cp /app/bin/acoms-cli-linux /app/bin/acoms-cli; fi; \
    chmod +x /usr/local/bin/mssp-entrypoint /usr/local/bin/mssp-bootstrap-config; \
    if [ -f /app/bin/acoms-cli ]; then chmod +x /app/bin/acoms-cli; fi; \
    chown -R mssp:mssp /app

USER mssp

ENTRYPOINT ["mssp-entrypoint"]
CMD ["mssp-pipeline"]
