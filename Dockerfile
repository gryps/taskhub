FROM python:3.12-slim-bookworm AS runtime

ARG TASKHUB_VERSION=0.1.0-alpha
ARG TASKHUB_COMMIT=unknown

LABEL org.opencontainers.image.title="TaskHub V2 Seed Controller" \
      org.opencontainers.image.version="${TASKHUB_VERSION}" \
      org.opencontainers.image.revision="${TASKHUB_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    TASKHUB_HOST=0.0.0.0 \
    TASKHUB_PORT=8200

RUN groupadd --gid 10001 taskhub \
    && useradd --uid 10001 --gid taskhub --create-home --shell /usr/sbin/nologin taskhub

WORKDIR /opt/taskhub
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

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
