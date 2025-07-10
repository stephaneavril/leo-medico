FROM python:3.11-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["celery", "-A", "celery_worker.celery_app", "worker",
     "--loglevel=info", "--pool=solo", "-n", "worker1@%h"]
