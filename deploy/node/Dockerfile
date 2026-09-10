FROM python:3.12-slim-bookworm

ARG TASKHUB_VERSION=0.1.0-alpha
ARG TASKHUB_COMMIT=unknown

LABEL org.opencontainers.image.title="TaskHub Unified Worker Node" \
      org.opencontainers.image.version="${TASKHUB_VERSION}" \
      org.opencontainers.image.revision="${TASKHUB_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    TASKHUB_NODE_ROLE=test \
    TASKHUB_NODE_WORK_ROOT=/var/lib/taskhub-node/jobs

RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends curl git nodejs npm openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 taskhub \
    && useradd --uid 10001 --gid taskhub --create-home --shell /usr/sbin/nologin taskhub

WORKDIR /opt/taskhub
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install . \
    && mkdir -p /var/lib/taskhub-node/jobs \
    && chown -R taskhub:taskhub /var/lib/taskhub-node /opt/taskhub

USER taskhub
EXPOSE 8020
VOLUME ["/var/lib/taskhub-node"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import os,urllib.request; q=urllib.request.Request('http://127.0.0.1:8020/api/health',headers={'Authorization':'Bearer '+os.environ['TASKHUB_NODE_TOKEN']}); urllib.request.urlopen(q,timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "taskhub_v2.node_agent:create_node_app", "--factory", "--host", "0.0.0.0", "--port", "8020"]
