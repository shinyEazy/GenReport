FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120

WORKDIR /workspace/GenReport/src

COPY src/requirements.txt /tmp/requirements.txt

RUN apt-get update \
    && apt-get install --yes --no-install-recommends file \
    && rm -rf /var/lib/apt/lists/*

RUN uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY src ./

EXPOSE 8011

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011"]
