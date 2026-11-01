# The public demo: the normal server without Calibre (the demo serves no editions), with demo/stop-press.toml.
# Build from the repo root: docker build -f demo/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    STOP_PRESS_CONFIG=/config/stop-press.toml PAPER_DATA_DIR=/data
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/fonts ./fonts
COPY server/paper ./paper
COPY demo/stop-press.toml /config/stop-press.toml

RUN useradd --system --uid 10001 paper && mkdir -p /data && chown paper /data
USER paper
EXPOSE 8000
CMD ["uvicorn", "paper.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
