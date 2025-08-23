# === celery_worker.py — Transcript-only + Persistencia por el Evaluador (OPC A) ===
"""
Flujo:
1) Descarga el .webm de S3.
2) Extrae audio (.wav mono 16 kHz) y lo sube a S3.
3) Transcribe con AWS Transcribe (ES).
4) Llama a evaluate_and_persist(session_id, user_text, "", None)
   -> El evaluador GUARDA evaluation_rh con todos los campos (Da Vinci, KPIs, etc.)
5) El worker SOLO actualiza evaluation (public), duration, audio_path (video key), timestamp.

Requisitos:
- FFMPEG presente en el entorno.
- Variables de entorno: REDIS_URL, DATABASE_URL, AWS_* (clave/secret/region/bucket),
  OPENAI_API_KEY (usada por el evaluador), etc.
"""

from __future__ import annotations
import os
import json
import time
import secrets
import logging
import subprocess
import requests
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv
from celery import Celery
import boto3
from botocore.exceptions import ClientError
import psycopg2

# Importa la función que también guarda en BD los puntos/metrics
from evaluator import evaluate_and_persist  # firma: (session_id, user_text, leo_text, video_path)

# ────────────────── CONFIG GENERAL ──────────────────
load_dotenv()

# Límites de tiempo del worker
CELERY_SOFT_LIMIT = int(os.getenv("CELERY_SOFT_LIMIT", 600))   # 10 min
CELERY_HARD_LIMIT = int(os.getenv("CELERY_HARD_LIMIT", 660))   # 11 min

# Celery (Redis) — usa una sola URL para broker/backend
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery(
    "leo_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.broker_transport_options = {"visibility_timeout": 7200}
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    worker_log_format="%(asctime)s %(levelname)s %(message)s",
)
celery_app.conf.imports = ("celery_worker",)

logging.basicConfig(level=logging.INFO, force=True)

# Directorio temporal
TMP_DIR = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TMP_DIR, exist_ok=True)

# AWS
AWS_S3_BUCKET_NAME     = os.getenv("AWS_S3_BUCKET_NAME", "").split("#", 1)[0].strip().strip("'\"")
AWS_S3_REGION_NAME     = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY  = os.getenv("AWS_SECRET_ACCESS_KEY")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

transcribe = boto3.client(
    "transcribe",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

# BD
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var not set")

# ────────────────── HELPERS S3 / FFMPEG ──────────────────

def dl_s3(bucket: str, key: str, dst: str) -> bool:
    try:
        s3.download_file(bucket, key, dst)
        return True
    except ClientError as e:
        logging.error("[S3 DOWNLOAD] %s", e)
        return False

def up_s3(src: str, bucket: str, key: str) -> str | None:
    try:
        s3.upload_file(src, bucket, key)
    except ClientError as e:
        logging.error("[S3 UPLOAD] %s", e)
        return None
    return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"

def run_ffmpeg_to_wav(src_webm: str, dst_wav: str) -> bool:
    try:
        # -vn: sin video; -ar 16000: 16 kHz; -ac 1: mono
        subprocess.run(
            ["ffmpeg", "-i", src_webm, "-vn", "-ar", "16000", "-ac", "1", "-y", dst_wav],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logging.error("[FFMPEG] %s", e.stderr.decode(errors="ignore"))
        return False

# ────────────────── HELPERS BD ──────────────────

def db_conn():
    p = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=p.path.lstrip("/"),
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        sslmode="require",
    )

def _update_db_only_public(
    sid: int,
    public_text: str,
    duration_seconds: int,
    video_key: str | None,
    timestamp_iso: str | None,
):
    """
    Importante: NO toca evaluation_rh (ya la guardó evaluate_and_persist).
    """
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE interactions SET
               evaluation=%s,
               duration_seconds=%s,
               audio_path=%s,
               timestamp=%s,
               visible_to_user=FALSE
           WHERE id=%s;""",
        (public_text, duration_seconds, video_key, timestamp_iso, sid),
    )
    conn.commit()
    conn.close()

# ────────────────── TASK ──────────────────

@celery_app.task(
    soft_time_limit=CELERY_SOFT_LIMIT,
    time_limit=CELERY_HARD_LIMIT,
    bind=True,
    name="celery_worker.process_session_transcript",
)
def process_session_transcript(self, payload: dict):
    """
    Procesa sesión analizando SOLO el transcript del usuario.
    payload esperado:
      - session_id (int)
      - video_object_key (str)  → clave en S3 del .webm
      - duration (int, opcional)
    """
    logging.info("🟢 START task=%s payload=%s", self.request.id, payload)

    sid    = payload.get("session_id")
    vkey   = payload.get("video_object_key")
    dur    = int(payload.get("duration", 0))
    ts_iso = datetime.utcnow().isoformat()

    if not sid:
        logging.error("🚫 payload sin session_id")
        return
    if not vkey:
        logging.warning("🚫 session %s: falta video_object_key", sid)
        _update_db_only_public(sid, "⚠️ Falta video_object_key — no se procesó", dur, None, ts_iso)
        return

    # 1) Descarga .webm
    webm = os.path.join(TMP_DIR, os.path.basename(vkey))
    if not dl_s3(AWS_S3_BUCKET_NAME, vkey, webm):
        _update_db_only_public(sid, "⚠️ Video no encontrado en S3", dur, vkey, ts_iso)
        return

    # 2) Extrae WAV
    wav = webm.rsplit(".", 1)[0] + ".wav"
    if not run_ffmpeg_to_wav(webm, wav):
        _update_db_only_public(sid, "⚠️ No se pudo extraer audio", dur, vkey, ts_iso)
        _safe_rm(webm, wav)
        return

    # 3) Sube WAV y transcribe
    audio_url = up_s3(wav, AWS_S3_BUCKET_NAME, f"audio/{os.path.basename(wav)}")
    user_txt = ""
    if audio_url:
        try:
            job = f"leo-{sid}-{secrets.token_hex(4)}"
            transcribe.start_transcription_job(
                TranscriptionJobName=job,
                Media={"MediaFileUri": audio_url},
                MediaFormat="wav",
                LanguageCode="es-US",  # ajusta si necesitas es-MX
            )
            # Espera con polling simple
            for _ in range(60):  # ~8 min máx (60 * 8s)
                status = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]
                state = status["TranscriptionJobStatus"]
                if state in {"COMPLETED", "FAILED"}:
                    break
                time.sleep(8)
            if state == "COMPLETED":
                uri = status["Transcript"]["TranscriptFileUri"]
                user_txt = requests.get(uri, timeout=20).json()["results"]["transcripts"][0]["transcript"]
            else:
                logging.error("Transcribe FAILED para sid=%s", sid)
        except Exception as e:
            logging.exception("[TRANSCRIBE] sid=%s error=%s", sid, e)

    # Clip para tokens seguros
    MAX_CHARS = 24_000
    user_txt = (user_txt or "")[-MAX_CHARS:]

    # 4) Evalúa y PERSISTE (puntos, fases, KPIs, etc.) vía evaluator
    #    -> evaluate_and_persist(sid, user_txt, leo_text, video_path)
    try:
        res = evaluate_and_persist(sid, user_txt, "", None)
        public_text = res.get("public", "Evaluación generada.")
    except Exception as e:
        logging.exception("[EVALUATE_PERSIST] sid=%s error=%s", sid, e)
        public_text = "⚠️ Evaluación automática no disponible."

    # 5) Actualiza SOLO los campos públicos y operativos (NO evaluation_rh)
    _update_db_only_public(sid, public_text, dur, vkey, ts_iso)

    # 6) Limpieza archivos temporales
    _safe_rm(webm, wav)
    logging.info("✅ DONE task=%s sid=%s", self.request.id, sid)

# ────────────────── UTILS ──────────────────

def _safe_rm(*paths: str):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# ────────────────── INIT DB (por si falta) ──────────────────

def init_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS interactions (
            id SERIAL PRIMARY KEY,
            name TEXT,
            email TEXT,
            scenario TEXT,
            message TEXT,
            response TEXT,
            audio_path TEXT,
            timestamp TEXT,
            evaluation TEXT,
            evaluation_rh TEXT,
            duration_seconds INTEGER DEFAULT 0,
            tip TEXT,
            visual_feedback TEXT,
            visible_to_user BOOLEAN DEFAULT FALSE,
            avatar_transcript TEXT,
            rh_comment TEXT
        );"""
    )
    conn.commit()
    conn.close()

init_db()
