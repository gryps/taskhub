FROM python:3.12-slim-bookworm AS runtime

ARG TASKHUB_VERSION=0.1.0-alpha
ARG TASKHUB_COMMIT=unknown
ARG CODEX_VERSION=0.153.4
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    TASKHUB_HOST=0.0.0.0 \
    TASKHUB_PORT=8200 \
    TASKHUB_CODEX_CLI_BIN=/usr/local/bin/codex \
    TASKHUB_CODEX_PLUS_HOME=/var/lib/taskhub/config/model-accounts/plus \
    TASKHUB_CODEX_PRO_HOME=/var/lib/taskhub/config/model-accounts/pro \
    TASKHUB_CODEX_API_HOME=/var/lib/taskhub/config/model-accounts/api

RUN groupadd --gid 10001 taskhub \
    && useradd --uid 10001 --gid taskhub --create-home --shell /usr/sbin/nologin taskhub

WORKDIR /opt/taskhub
RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o Acquire::Retries=8 \
    && apt-get install -y -o Acquire::Retries=8 --no-install-recommends ca-certificates curl openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/codex-home \
    && curl -fsSL --retry 5 https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
    && HOME=/opt/codex-home CODEX_RELEASE="${CODEX_VERSION}" CODEX_INSTALL_DIR=/usr/local/bin \
       CODEX_NON_INTERACTIVE=1 sh /tmp/install-codex.sh \
    && codex --version \
    && chmod -R a+rX /opt/codex-home \
    && rm -f /tmp/install-codex.sh
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

LABEL org.opencontainers.image.title="TaskHub V2 Seed Controller" \
      org.opencontainers.image.version="${TASKHUB_VERSION}" \
      org.opencontainers.image.revision="${TASKHUB_COMMIT}"

COPY deploy/docker/entrypoint.sh /usr/local/bin/taskhub-entrypoint
RUN chmod 0755 /usr/local/bin/taskhub-entrypoint \
    && mkdir -p /var/lib/taskhub \
    && chown -R taskhub:taskhub /var/lib/taskhub /opt/taskhub

USER taskhub
EXPOSE 8200
VOLUME ["/var/lib/taskhub"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/api/health', timeout=2).read()"]

ENTRYPOINT ["/usr/local/bin/taskhub-entrypoint"]
CMD ["python", "-m", "uvicorn", "taskhub_v2.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
