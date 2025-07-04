# celery_worker.py – versión depurada 2025‑07‑04
# -------------------------------------------------
"""
Worker de Celery para Leo‑Trainer.
Se conecta a Redis (broker + backend) y PostgreSQL, procesa los videos
subidos, extrae audio, llama a AWS Transcribe, evalúa con OpenAI y guarda
resultado.  Esta versión corrige:
  • Uso de **una** única instancia de Celery.
  • Puerto/URL de Redis coherente con Render.
  • Eliminación del puerto 6378 y fallback a 6379.
  • CREATE TABLE sin comentarios SQL que rompían PostgreSQL.
"""

from __future__ import annotations
import os
import json
import time
import subprocess
from datetime import datetime
from urllib.parse import urlparse

from celery import Celery
from dotenv import load_dotenv
import requests
import cv2

import boto3
from botocore.exceptions import ClientError
import psycopg2

from evaluator import evaluate_interaction  # tu evaluador IA

# ---------------------------------------------------------------------
# Carga de variables de entorno
# ---------------------------------------------------------------------
load_dotenv()

# 1) Redis ------------------------------------------------------------
REDIS_URL = (
    os.getenv("REDIS_URL")             # inyectado por Render (recommended)
    or os.getenv("CELERY_BROKER_URL")  # compatibilidad Heroku/otros
    or "redis://localhost:6379/0"      # fallback local
)

celery_app = Celery("leo_trainer_tasks", broker=REDIS_URL, backend=REDIS_URL)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Mexico_City",
    enable_utc=False,
)

# 2) Carpetas temporales ---------------------------------------------
TEMP_PROCESSING_FOLDER = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

# 3) AWS (S3 + Transcribe) -------------------------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "leo-trainer-videos")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-west-2")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

transcribe_client = boto3.client(
    "transcribe",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

# 4) PostgreSQL -------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set!")

def get_db_connection():
    p = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=p.path[1:],
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        sslmode="require",
    )

# ---------------------------------------------------------------------
#   Base de datos – creación / parcheo
# ---------------------------------------------------------------------

def init_db():
    """Crea las tablas si no existen."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
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
                avatar_transcript TEXT,
                visible_to_user BOOLEAN DEFAULT FALSE
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                email TEXT UNIQUE,
                start_date TEXT,
                end_date TEXT,
                active INTEGER DEFAULT 1,
                token TEXT UNIQUE
            );
            """
        )
        conn.commit()
        print("📄 Database initialized (PostgreSQL).")
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"🔥 DB init error: {e}")
    finally:
        if conn:
            conn.close()


def patch_db_schema():
    """Añade columnas faltantes de forma idempotente."""
    statements = [
        ("interactions", "avatar_transcript", "ALTER TABLE interactions ADD COLUMN avatar_transcript TEXT;"),
        ("interactions", "tip", "ALTER TABLE interactions ADD COLUMN tip TEXT;"),
        ("interactions", "visual_feedback", "ALTER TABLE interactions ADD COLUMN visual_feedback TEXT;"),
        ("interactions", "visible_to_user", "ALTER TABLE interactions ADD COLUMN visible_to_user BOOLEAN DEFAULT FALSE;"),
        ("users", "token", "ALTER TABLE users ADD COLUMN token TEXT UNIQUE;"),
    ]
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for table, column, sql in statements:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s;
                """,
                (table, column),
            )
            if not cur.fetchone():
                cur.execute(sql)
                print(f"🛠 Added column {column} to {table}.")
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"🔥 DB patch error: {e}")
    finally:
        if conn:
            conn.close()


# Ejecutamos inmediatamente
init_db()
patch_db_schema()

# ---------------------------------------------------------------------------
# Utilidades (S3, ffmpeg, visión por computador)
# ---------------------------------------------------------------------------

def upload_file_to_s3(file_path: str, bucket: str, object_name: str | None = None) -> str | None:
    if object_name is None:
        object_name = os.path.basename(file_path)
    try:
        s3_client.upload_file(file_path, bucket, object_name)
        return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{object_name}"
    except ClientError as e:
        print(f"[S3 ERROR] {e}")
        return None

def download_file_from_s3(bucket: str, object_name: str, file_path: str) -> bool:
    try:
        s3_client.download_file(bucket, object_name, file_path)
        return True
    except ClientError as e:
        print(f"[S3 ERROR] {e}")
        return False

def convert_webm_to_mp4(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path,
    ]
    return _run_ffmpeg(cmd, input_path)

def compress_video_for_ai(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=160:120,format=gray",
        "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "32k", "-ac", "1",
        "-y", output_path,
    ]
    return _run_ffmpeg(cmd, input_path)

def _run_ffmpeg(cmd: list[str], ref: str) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[FFMPEG ERROR] {ref}: {e.stderr.decode()}")
        return False


def analyze_video_posture(video_path: str) -> tuple[str, str, str]:
    """Retorna (feedback_usuario, feedback_interno, porcentaje_detectado)"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return (
            "⚠️ No se pudo abrir el video para análisis visual.",
            "Error en video",
            "N/A",
        )
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    total = int(min(200, cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    detected = 0
    for _ in range(total):
        ret, frame = cap.read()
        if not ret:
            break
        faces = face_cascade.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 1.3, 5)
        if len(faces):
            detected += 1
    cap.release()
    ratio = detected / total if total else 0
    if ratio >= 0.7:
        return "✅ Te mantuviste visible y profesional.", "Correcta", f"{ratio*100:.1f}%"
    if ratio > 0:
        return "⚠️ Mejora tu visibilidad frente a la cámara.", "Visibilidad parcial", f"{ratio*100:.1f}%"
    return "❌ No se detectó rostro en el video.", "No detectado", "0.0%"

# ---------------------------------------------------------------------------
# Tarea principal
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, acks_late=True)
def process_session_video(self, data: dict) -> dict:  # noqa: C901 – función larga pero explícita
    """Procesa el video de una sesión y actualiza DB con evaluación IA."""
    # --- Extracción de campos básicos --------------------------------------------------
    session_id: int = data["session_id"]
    name = data.get("name")
    email = data.get("email")
    scenario = data.get("scenario")
    duration = int(data.get("duration", 0))
    video_key = data.get("video_object_key")

    timestamp = datetime.utcnow().isoformat()

    # Defaults ------------------------------------------------------------------------
    user_transcript = ""
    public_summary = "Evaluación no disponible."
    internal_summary = {}
    tip_text = "Consejo no disponible."
    posture_feedback = "Análisis visual no realizado."
    final_video_url = None

    if not video_key:
        return {"status": "error", "error": "video_object_key faltante"}

    # ---------------------------------------------------------------------------
    # Descarga / conversión / compresión de video
    # ---------------------------------------------------------------------------
    tmp_webm = os.path.join(TEMP_PROCESSING_FOLDER, video_key)
    if not os.path.exists(tmp_webm):
        if not download_file_from_s3(AWS_S3_BUCKET_NAME, video_key, tmp_webm):
            return {"status": "error", "error": "No se encontró el video en S3"}

    mp4_key = video_key.replace(".webm", ".mp4")
    tmp_mp4 = os.path.join(TEMP_PROCESSING_FOLDER, mp4_key)

    video_path_for_ai = tmp_webm
    if convert_webm_to_mp4(tmp_webm, tmp_mp4):
        video_path_for_ai = tmp_mp4
        compressed = tmp_mp4.replace(".mp4", "_compressed.mp4")
        if compress_video_for_ai(tmp_mp4, compressed):
            video_path_for_ai = compressed

    # ---------------------------------------------------------------------------
    # Extracción de audio y transcripción con AWS Transcribe
    # ---------------------------------------------------------------------------
    audio_key = f"audio/{os.path.splitext(video_key)[0]}.wav"
    tmp_audio = os.path.join(TEMP_PROCESSING_FOLDER, os.path.basename(audio_key))

    ffmpeg_cmd = [
        "ffmpeg", "-i", video_path_for_ai, "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1", "-y", tmp_audio,
    ]
    if _run_ffmpeg(ffmpeg_cmd, video_path_for_ai):
        audio_url = upload_file_to_s3(tmp_audio, AWS_S3_BUCKET_NAME, audio_key)
        if audio_url:
            job_name = f"leo-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
            aws_transcribe.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={"MediaFileUri": audio_url},
                MediaFormat="wav",
                LanguageCode="es-US",
            )
            for _ in range(60):
                status = aws_transcribe.get_transcription_job(TranscriptionJobName=job_name)[
                    "TranscriptionJob"
                ]["TranscriptionJobStatus"]
                if status in {"COMPLETED", "FAILED"}:
                    break
                time.sleep(10)
            if status == "COMPLETED":
                uri = aws_transcribe.get_transcription_job(TranscriptionJobName=job_name)[
                    "TranscriptionJob"
                ]["Transcript"]["TranscriptFileUri"]
                user_transcript = requests.get(uri).json()["results"]["transcripts"][0]["transcript"]

    # ---------------------------------------------------------------------------
    # Análisis postura visual
    # ---------------------------------------------------------------------------
    if os.path.exists(video_path_for_ai):
        posture_feedback, _, _ = analyze_video_posture(video_path_for_ai)

    # ---------------------------------------------------------------------------
    # Evaluación IA y tip personalizado
    # ---------------------------------------------------------------------------
    if user_transcript.strip():
        try:
            summaries = evaluate_interaction(user_transcript, "", video_path_for_ai)
            public_summary = summaries.get("public", public_summary)
            internal_summary = summaries.get("internal", {})
        except Exception as e:
            internal_summary = {"error": str(e)}
            public_summary = "⚠️ Evaluación automática no disponible."    
    else:
        public_summary = "⚠️ No se pudo transcribir la intervención del participante."

    # Generar tip -------------------------------------------------------------
    try:
        from openai import OpenAI
        chat = OpenAI(api_key=os.getenv("OPENAI_API_KEY")).chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un coach médico empático y útil."},
                {"role": "user", "content": user_transcript or ""},
            ],
            temperature=0.7,
        )
        tip_text = chat.choices[0].message.content.strip()
    except Exception as e:
        tip_text = f"⚠️ No se pudo generar tip: {e}"

    # ---------------------------------------------------------------------------
    # Guardar en DB
    # ---------------------------------------------------------------------------
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE interactions SET
                    evaluation=%s,
                    evaluation_rh=%s,
                    tip=%s,
                    visual_feedback=%s,
                    audio_path=%s,
                    duration_seconds=%s,
                    timestamp=%s,
                    visible_to_user=FALSE
                WHERE id=%s;
                """,
                (
                    public_summary,
                    json.dumps(internal_summary, ensure_ascii=False),
                    tip_text,
                    posture_feedback,
                    final_video_url,
                    duration,
                    timestamp,
                    session_id,
                ),
            )
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"🔥  Error actualizando DB: {e}")
    finally:
        if conn:
            conn.close()

    # Limpieza ----------------------------------------------------------------
    for f in [tmp_webm, tmp_mp4, tmp_audio, video_path_for_ai]:
        try:
            if f and os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    return {
        "status": "ok",
        "evaluation": public_summary,
        "tip": tip_text,
        "visual_feedback": posture_feedback,
        "final_video_url": final_video_url,
        "timestamp": timestamp,
        "name": name,
        "email": email,
    }
