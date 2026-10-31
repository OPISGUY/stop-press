FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 QT_QPA_PLATFORM=offscreen \
    STOP_PRESS_CONFIG=/config/stop-press.toml PAPER_DATA_DIR=/data
# Calibre's ebook-convert writes the Kindle periodical edition (paper/periodical.py)
RUN apt-get update && apt-get install -y --no-install-recommends calibre && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fonts ./fonts
COPY paper ./paper
# Cut the static font weights the edition PDF needs now, not on the first request.
RUN python -c "from paper.edition import _static_fonts; _static_fonts()"

RUN useradd --system --uid 10001 paper && mkdir -p /data /config && chown paper /data
USER paper
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "paper.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
