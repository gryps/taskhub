FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm

ARG TASKHUB_VERSION=0.1.0-alpha
ARG TASKHUB_COMMIT=unknown
ARG CODEX_VERSION=0.153.4
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    TASKHUB_NODE_ROLE=test \
    TASKHUB_NODE_WORK_ROOT=/var/lib/taskhub-node/jobs \
    TASKHUB_NODE_CACHE_ROOT=/var/lib/taskhub-node/cache \
    PIP_CACHE_DIR=/var/lib/taskhub-node/cache/pip \
    npm_config_cache=/var/lib/taskhub-node/cache/npm \
    TASKHUB_CODEX_CLI_BIN=/usr/local/bin/codex \
    TASKHUB_CODEX_PLUS_HOME=/var/lib/taskhub-node/codex/plus \
    TASKHUB_CODEX_PRO_HOME=/var/lib/taskhub-node/codex/pro \
    TASKHUB_CODEX_API_HOME=/var/lib/taskhub-node/codex/api

COPY --from=node-runtime /usr/local/ /usr/local/

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o Acquire::Retries=8 \
    && apt-get install -y -o Acquire::Retries=8 --no-install-recommends ca-certificates curl git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 taskhub \
    && useradd --uid 10001 --gid taskhub --create-home --shell /usr/sbin/nologin taskhub \
    && node --version \
    && npm --version
RUN mkdir -p /opt/codex-home \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 30 \
       https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
    && HOME=/opt/codex-home CODEX_RELEASE="${CODEX_VERSION}" CODEX_INSTALL_DIR=/usr/local/bin \
       CODEX_NON_INTERACTIVE=1 sh /tmp/install-codex.sh \
    && codex --version \
    && chmod -R a+rX /opt/codex-home \
    && rm -f /tmp/install-codex.sh

WORKDIR /opt/taskhub
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,id=taskhub-pip,target=/root/.cache/pip,sharing=locked \
    python -m pip install '.[dev,browser]' \
    && mkdir -p /var/lib/taskhub-node/jobs /var/lib/taskhub-node/cache/pip /var/lib/taskhub-node/cache/npm /var/lib/taskhub-node/cache/uv \
    && chown -R taskhub:taskhub /var/lib/taskhub-node /opt/taskhub

LABEL org.opencontainers.image.title="TaskHub Unified Worker Node" \
      org.opencontainers.image.version="${TASKHUB_VERSION}" \
      org.opencontainers.image.revision="${TASKHUB_COMMIT}"

USER taskhub
EXPOSE 8020
VOLUME ["/var/lib/taskhub-node"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import os,urllib.request; q=urllib.request.Request('http://127.0.0.1:8020/api/health',headers={'Authorization':'Bearer '+os.environ['TASKHUB_NODE_TOKEN']}); urllib.request.urlopen(q,timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "taskhub_v2.node_agent:create_node_app", "--factory", "--host", "0.0.0.0", "--port", "8020"]
