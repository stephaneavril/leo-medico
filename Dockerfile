FROM python:3.11-slim

# 1) Paquetes de sistema que necesita tu análisis de vídeo
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# 2) Dependencias Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) Copia tu código
COPY . .

# 4) Arranque Celery (una sola línea)
CMD ["celery", "-A", "celery_worker.celery_app", "worker", "--loglevel=info", "--pool=solo", "-n", "worker1@%h"]
