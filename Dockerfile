# Imagen única para la API y el worker; el comando decide el proceso.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# ffprobe valida formato y duración reales de los audios.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser:appuser /app/var
USER appuser

EXPOSE 8000
# --ws-max-size: un cuadro de cámara pesa como máximo 300 kB.
# --no-access-log: los enlaces de aceptación llevan un token en la ruta.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --ws-max-size 400000 --no-access-log"]
