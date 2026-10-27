FROM python:3.12-slim

# FFmpeg lands here in the encoding phase; installed now so images are stable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY pytest.ini ./

RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /bot/data /bot/temp /bot/logs \
    && chown -R botuser:botuser /bot/data /bot/temp /bot/logs

USER botuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import pathlib,sys; sys.exit(0 if pathlib.Path('/bot/logs/animatrixx.log').exists() else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main"]
