# === celery_worker.py — Análisis SOLO por Transcript (manteniendo transcripción de audio) ===
"""
Nueva versión del worker que:
1. Descarga el .webm de S3.
2. Extrae audio a .wav y lo sube a S3.
3. Usa AWS Transcribe para generar el **transcript del USUARIO**.
4. Invoca `evaluate_interaction(user_text, "", None)` (sin avatar, sin video).
5. Guarda resultado en PostgreSQL.

No se importa `cv2` ni se analizan frames. El video sigue disponible para que RH lo reproduzca en el panel.
"""
from __future__ import annotations
import os, json, time, subprocess, secrets, requests, logging, traceback
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from celery import Celery
import boto3, psycopg2
from botocore.exceptions import ClientError
from evaluator import evaluate_interaction  # firma: user_text, avatar_text, video_path

# ──────────────────── CONFIG ────────────────────
load_dotenv()

CELERY_SOFT_LIMIT = int(os.getenv("CELERY_SOFT_LIMIT", 600))   # 10 min avisa
CELERY_HARD_LIMIT = int(os.getenv("CELERY_HARD_LIMIT", 660))   # 11 min mata

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("leo_tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    worker_log_format="%(asctime)s %(levelname)s %(message)s",
)
logging.basicConfig(level=logging.INFO, force=True)

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

# ───────── Helpers S3 / ffmpeg ─────────

def dl_s3(bucket: str, key: str, dst: str) -> bool:
    try:
        s3.download_file(bucket, key, dst)
        return True
    except ClientError as e:
        logging.error("[S3] %s", e)
        return False


def up_s3(src: str, bucket: str, key: str) -> str | None:
    try:
        s3.upload_file(src, bucket, key)
    except ClientError as e:
        logging.error("[S3] %s", e)
        return None
    return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"


def run_ffmpeg(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logging.error("[FFMPEG] %s", e.stderr.decode())
        return False

# ───────── DB helper ─────────

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

# ───────── Celery Task ─────────

@celery_app.task(
    soft_time_limit=CELERY_SOFT_LIMIT,
    time_limit=CELERY_HARD_LIMIT,
    bind=True,
    name="celery_worker.process_session_transcript",
)
def process_session_transcript(self, payload: dict):
    """Procesa sesión analizando SOLO el transcript del usuario.
    Espera keys:
        session_id, video_object_key (para reproducir en admin),
        duration (segundos, opcional)
    """
    logging.info("🟢 START %s payload=%s", self.request.id, payload)

    sid   = payload.get("session_id")
    vkey  = payload.get("video_object_key")  # se guarda pero no se analiza
    dur   = int(payload.get("duration", 0))
    ts_iso = datetime.utcnow().isoformat()

    if not vkey:
        _update_db(sid, "⚠️ Falta video_object_key — no se procesó", vkey=vkey)
        logging.warning("🚫 session %s: video_object_key missing", sid)
        return

    # 1· Descarga el .webm para extraer audio
    webm = os.path.join(TMP_DIR, os.path.basename(vkey))
    if not dl_s3(AWS_S3_BUCKET_NAME, vkey, webm):
        _update_db(sid, "⚠️ Video no encontrado en S3", vkey=vkey)
        return

    # 2· Extrae audio (wav mono 16 kHz)
    wav = webm.rsplit(".", 1)[0] + ".wav"
    if not run_ffmpeg(["ffmpeg", "-i", webm, "-vn", "-ar", "16000", "-ac", "1", "-y", wav]):
        _update_db(sid, "⚠️ No se pudo extraer audio", vkey=vkey)
        return

    # 3· Sube audio y lanza AWS Transcribe
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
            status = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]
            if status["TranscriptionJobStatus"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(8)
        if status["TranscriptionJobStatus"] == "COMPLETED":
            uri = status["Transcript"]["TranscriptFileUri"]
            user_txt = requests.get(uri).json()["results"]["transcripts"][0]["transcript"]

    # 4· Recorta para cumplir limits de tokens
    MAX_CHARS = 24_000
    user_txt = user_txt[-MAX_CHARS:]

    # 5· Llama a evaluador (sin avatar, sin video)
    try:
        res = evaluate_interaction(user_txt, "", None)
        pub_eval = res.get("public",  "Evaluación no disponible.")
        rh_eval  = res.get("internal", {})
    except Exception as e:
        logging.exception("[EVALUATE] %s", e)
        pub_eval, rh_eval = "⚠️ Evaluación automática no disponible.", {"error": str(e)}

    # 6· Guarda en BD
    _update_db(sid, pub_eval, rh_eval, dur, vkey, ts_iso)

    # 7· Limpieza temp
    for f in (webm, wav):
        try:
            if os.path.exists(f):
                os.remove(f)
        except FileNotFoundError:
            pass
    logging.info("✅ DONE  task %s", self.request.id)


# ───────── BD Update ─────────

def _update_db(
    sid: int,
    pub: str,
    rh: dict | None = None,
    dur: int = 0,
    vkey: str | None = None,
    ts: str | None = None,
):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE interactions SET
               evaluation=%s, evaluation_rh=%s,
               duration_seconds=%s, audio_path=%s, timestamp=%s,
               visible_to_user=FALSE
               WHERE id=%s;""",
        (pub, json.dumps(rh or {}), dur, vkey, ts, sid),
    )
    conn.commit()
    conn.close()


# ───────── INIT DB (sigue igual) ─────────

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
            avatar_transcript TEXT
        );"""
    )
    conn.commit()
    conn.close()

init_db()
