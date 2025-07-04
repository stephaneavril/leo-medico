"""
celery_worker.py – versión única y final
---------------------------------------
• Descarga video .webm de S3 (bucket leocoach)
• Convierte, transcribe, evalúa con OpenAI y actualiza PostgreSQL
"""
from __future__ import annotations
import os, json, time, subprocess, secrets, requests, cv2
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from celery import Celery
import boto3, psycopg2
from botocore.exceptions import ClientError
from evaluator import evaluate_interaction          # ← tu evaluador

# ──────────────────── CONFIG ────────────────────
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("leo_trainer_tasks", broker=REDIS_URL, backend=REDIS_URL)

TEMP_PROCESSING_FOLDER = "/tmp/leo_trainer_processing"
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

AWS_S3_BUCKET_NAME = (
    os.getenv("AWS_S3_BUCKET_NAME", "leocoach")
    .split("#", 1)[0].strip().strip("'\"")
)
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)
aws_transcribe = boto3.client(
    "transcribe",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

DATABASE_URL = os.getenv("DATABASE_URL")
def get_db(): p = urlparse(DATABASE_URL); return psycopg2.connect(
        database=p.path.lstrip("/"), user=p.username, password=p.password,
        host=p.hostname, port=p.port, sslmode="require")

# ──────────────────── UTILIDADES S3 / FFMPEG ────────────────────
def download_s3(bucket, key, dest) -> bool:
    try:
        s3_client.download_file(bucket, key, dest); return True
    except ClientError as e:
        print(f"[S3 ERROR] {e}"); return False

def upload_s3(path, bucket, key) -> str|None:
    try:
        s3_client.upload_file(path, bucket, key)
        return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"
    except ClientError as e:
        print(f"[S3 ERROR] {e}"); return None

def ffmpeg(cmd:list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, check=True); return True
    except subprocess.CalledProcessError as e:
        print(f"[FFMPEG] {e.stderr.decode()}"); return False

# ──────────────────── TAREA PRINCIPAL ────────────────────
@celery_app.task
def process_session_video(data: dict) -> dict:
    id_        = data["session_id"]
    video_key  = data.get("video_object_key")
    duration   = int(data.get("duration", 0))
    timestamp  = datetime.utcnow().isoformat()

    # 1· Descarga .webm ───────────────────────────────
    tmp_webm = os.path.join(TEMP_PROCESSING_FOLDER, video_key)
    if not download_s3(AWS_S3_BUCKET_NAME, video_key, tmp_webm):
        _update_db(id_, "⚠️ Video no encontrado en S3"); 
        return {"status":"error","msg":"video 404"}

    # 2· Convierte / comprime para IA ────────────────
    mp4 = tmp_webm.replace(".webm", ".mp4")
    if ffmpeg(["ffmpeg","-i",tmp_webm,"-c:v","libx264","-preset","fast",
               "-c:a","aac","-y",mp4]): video_ai = mp4
    else:                                       video_ai = tmp_webm

    # 3· Audio + AWS Transcribe ──────────────────────
    wav = tmp_webm.replace(".webm",".wav")
    ffmpeg(["ffmpeg","-i",video_ai,"-vn","-acodec","pcm_s16le",
            "-ar","16000","-ac","1","-y",wav])
    audio_url = upload_s3(wav,AWS_S3_BUCKET_NAME,
                          f"audio/{os.path.basename(wav)}")
    user_txt = _transcribe(audio_url) if audio_url else ""

    # 4· Evaluación IA + tip ─────────────────────────
    public, internal = "⚠️ Evaluación no disponible.", {}
    try:
        res = evaluate_interaction(
            user_text=user_txt := user_txt or "Transcripción no disponible",
            leo_text="",
            video_to_process_path=video_ai
        )
        public, internal = res.get("public", public), res.get("internal", {})
    except Exception as e:
        internal = {"error": str(e)}

    # 5· Guarda en DB ────────────────────────────────
    _update_db(id_, public, internal, duration, video_key, timestamp)

    # Limpieza
    for f in (tmp_webm, mp4, wav):
        if os.path.exists(f): os.remove(f)

    return {"status":"ok","session_id":id_}

# ── helpers DB ───────────────────────────────────────────
def _update_db(id_, public, internal=None, dur=0, key=None, ts=""):
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE interactions SET
          evaluation=%s, evaluation_rh=%s, duration_seconds=%s,
          audio_path=%s, timestamp=%s, visible_to_user=FALSE
        WHERE id=%s;""",
        (public, json.dumps(internal or {}), dur, key, ts, id_))
    conn.commit(); conn.close()

def _transcribe(url:str) -> str:
    job = f"leo-{secrets.token_hex(8)}"
    aws_transcribe.start_transcription_job(
        TranscriptionJobName=job, Media={"MediaFileUri": url},
        MediaFormat="wav", LanguageCode="es-US")
    for _ in range(60):
        s = aws_transcribe.get_transcription_job(
            TranscriptionJobName=job)["TranscriptionJob"]["TranscriptionJobStatus"]
        if s in {"COMPLETED","FAILED"}: break; time.sleep(10)
    if s=="COMPLETED":
        uri = aws_transcribe.get_transcription_job(
            TranscriptionJobName=job)["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        return requests.get(uri).json()["results"]["transcripts"][0]["transcript"]
    return ""
