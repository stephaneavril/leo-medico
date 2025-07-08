from __future__ import annotations
import os, json, time, subprocess, secrets, requests, cv2
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from celery import Celery
import boto3, psycopg2
from botocore.exceptions import ClientError
from evaluator import evaluate_interaction  # firma: user_text, leo_text, video_path

"""
Celery worker completo para Leo Coach
------------------------------------
• Descarga el video de S3, lo convierte, transcribe y evalúa.
• Analiza presencia facial y genera tip/postura (OpenCV + cascada, sin MediaPipe para simplificar).
• Guarda resultado en PostgreSQL.
• Incluye validación defensiva cuando falta `video_object_key` para que el worker no se caiga.
"""

# ───────── CONFIG GLOBAL ─────────
load_dotenv()

# ⏱️  ------------- NUEVO -------------------
CELERY_SOFT_LIMIT = int(os.getenv("CELERY_SOFT_LIMIT", 240))   # 4 min (avisa SIGUSR1)
CELERY_HARD_LIMIT = int(os.getenv("CELERY_HARD_LIMIT", 300))   # 5 min (mata el proceso)
# ⏱️  -----

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("leo_tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(task_track_started=True, task_acks_late=True, worker_prefetch_multiplier=1)

TMP_DIR = "/tmp/leo_trainer_processing"
os.makedirs(TMP_DIR, exist_ok=True)

AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "leotrainer2").split("#", 1)[0].strip("'\"")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

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

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var not set")


def db():
    p = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=p.path.lstrip("/"),
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        sslmode="require",
    )

# ───────── Helpers S3 / ffmpeg ─────────

def dl_s3(bucket: str, key: str, dst: str) -> bool:
    try:
        s3.download_file(bucket, key, dst)
        return True
    except ClientError as e:
        print("[S3]", e)
        return False


def up_s3(src: str, bucket: str, key: str) -> str | None:
    try:
        s3.upload_file(src, bucket, key)
    except ClientError as e:
        print("[S3]", e)
        return None
    return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"


def ffmpeg(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print("[FFMPEG]", e.stderr.decode())
        return False

# ───────── Face‑detection sencilla (OpenCV) ─────────
FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def analyze_video_posture(video_path: str) -> tuple[str, str]:
    """Devuelve (public_feedback, visual_json)"""
    total, detected = 0, 0
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return "⚠️ No se pudo analizar el video", "{}"
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        total += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = FACE_CASCADE.detectMultiScale(gray, 1.1, 4)
        if len(faces):
            detected += 1
    cap.release()
    ratio = detected / total if total else 0
    pub = "😃 Buen contacto visual" if ratio > 0.7 else "🧐 Trata de mantener la mirada al frente"
    visual = json.dumps({"frames": total, "face_frames": detected, "ratio": ratio})
    return pub, visual

# ─────────  TAREA CELERY ÚNICA ─────────
@celery_app.task(soft_time_limit=CELERY_SOFT_LIMIT,
                 time_limit=CELERY_HARD_LIMIT)
def process_session_video(d: dict):
    sid  = d.get("session_id")
    vkey = d.get("video_object_key")
    dur  = int(d.get("duration", 0))
    ts_iso = datetime.utcnow().isoformat()

    if not vkey:
        _update_db(sid, "⚠️ Tarea sin video_object_key")
        print(f"[WARN] session {sid}: video_object_key missing")
        return

    # 1· Descarga WEBM
    webm = os.path.join(TMP_DIR, vkey)
    if not dl_s3(AWS_S3_BUCKET_NAME, vkey, webm):
        _update_db(sid, "⚠️ Video no encontrado en S3")
        return

    # 2· Convierte a MP4
    mp4 = webm.replace(".webm", ".mp4")
    ffmpeg(["ffmpeg", "-i", webm, "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", mp4])

    # 3· Analiza postura / cara
    posture_pub, posture_json = analyze_video_posture(mp4)

    # 4· Extrae audio y transcribe
    wav = webm.replace(".webm", ".wav")
    ffmpeg(["ffmpeg", "-i", mp4, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-y", wav])
    audio_url = up_s3(wav, AWS_S3_BUCKET_NAME, f"audio/{os.path.basename(wav)}")
    user_txt = ""
    if audio_url:
        job = f"leo-{secrets.token_hex(6)}"
        transcribe.start_transcription_job(
            TranscriptionJobName=job,
            Media={"MediaFileUri": audio_url},
            MediaFormat="wav",
            LanguageCode="es-US",
        )
        for _ in range(60):
            st = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]["TranscriptionJobStatus"]
            if st in {"COMPLETED", "FAILED"}:
                break
            time.sleep(10)
        if st == "COMPLETED":
            uri = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
            user_txt = requests.get(uri).json()["results"]["transcripts"][0]["transcript"]
 
    # ────────────── AQUÍ AÑADES EL RECORTE ──────────────
    MAX_CHARS = 24_000           # 24 000 ≈ 6-7 páginas de texto.
    user_txt = user_txt[-MAX_CHARS:]
    # ────────────────────────────────────────────────────
    # 5· Evaluación con OpenAI
    try:
        res = evaluate_interaction(user_txt, "", mp4)
        pub_eval = res.get("public", "Evaluación no disponible.")
        rh_eval  = res.get("internal", {})
    except Exception as e:
        pub_eval, rh_eval = "⚠️ Evaluación automática no disponible.", {"error": str(e)}

    # 6· Guarda en BD
    _update_db(
        sid,
        pub_eval,
        rh_eval,
        dur,
        vkey,
        ts_iso,
        tip=posture_pub,
        visual_json=posture_json,
    )

    # Limpieza
    for f in (webm, mp4, wav):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass


def _update_db(
    sid: int,
    pub: str,
    rh: dict | None = None,
    dur: int = 0,
    vkey: str | None = None,
    ts: str | None = None,
    tip: str | None = None,
    visual_json: str | None = None,
):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """UPDATE interactions SET
               evaluation=%s, evaluation_rh=%s, duration_seconds=%s,
               audio_path=%s, timestamp=%s, tip=%s, visual_feedback=%s,
               visible_to_user=FALSE
               WHERE id=%s;""",
        (pub, json.dumps(rh or {}), dur, vkey, ts, tip, visual_json, sid),
    )
    conn.commit()
    conn.close()

# ───────── INIT DB: corrige coma perdida ─────────
def init_db():
    conn = db()
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
            avatar_transcript TEXT
        );"""
    )
    conn.commit()
    conn.close()

# Ejecuta init_db al arrancar worker
init_db()
