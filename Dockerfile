FROM python:3.11-slim

# 1) Paquetes de sistema que usan ffmpeg y OpenCV
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# 2) Variables de entorno útiles
ENV PYTHONUNBUFFERED=1 \
    C_FORCE_ROOT=true          

# 3) Dependencias Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Copia del código
COPY . .

# 5) Lanzar el worker (4 procesos prefork)
CMD ["bash", "-c", "exec celery -A celery_worker:celery_app worker \
      --pool=prefork --concurrency=4 \
      --loglevel=info --hostname=worker1@%h \
      --heartbeat-interval=30 \
      --soft-time-limit=900 --time-limit=960"]
