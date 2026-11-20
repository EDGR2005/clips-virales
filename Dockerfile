# Imagen solo para el panel/monitor (sin Whisper, mediapipe ni ffmpeg).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PANEL_EN_CONTENEDOR=1

RUN groupadd --system --gid 10001 clips \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin clips \
    && mkdir -p /data && chown clips:clips /data

WORKDIR /app
COPY requirements-panel.txt .
RUN pip install -r requirements-panel.txt
COPY --chown=root:root monitor_directos.py panel.py ./

USER clips
VOLUME ["/data"]
EXPOSE 8080

# Dentro del contenedor escucha en 0.0.0.0; docker-compose solo publica el
# puerto en 127.0.0.1 del VPS, así que sigue sin quedar expuesto a internet.
CMD ["python", "panel.py", "--host", "0.0.0.0", "--port", "8080", \
     "--salida", "/data/picos", "--estado", "/data/panel_canales.json"]
