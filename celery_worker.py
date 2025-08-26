# === celery_worker.py — Descarga, transcribe (AWS) con fallback, evalúa y persiste ===
from __future__ import annotations

import os, json, time, logging, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import boto3
from botocore.exceptions import ClientError
import psycopg2

from celery import Celery
from dotenv import load_dotenv

from evaluator import evaluate_and_persist  # (session_id, user_text, leo_text, video_path)

# ───────────────────────────────── CONFIG ─────────────────────────────────
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("celery_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("leo_tasks", broker=REDIS_URL, backend=REDIS_URL)

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_DB   = os.getenv("PG_DB", "postgres")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PWD  = os.getenv("PG_PWD", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

AWS_REGION          = os.getenv("AWS_REGION", "us-east-1")
AWS_S3_BUCKET_NAME  = os.getenv("AWS_S3_BUCKET_NAME", "")
AWS_TRANSCRIBE_LANG = os.getenv("AWS_TRANSCRIBE_LANG", "es-US")
AWS_TRANSCRIBE_ENABLED = os.getenv("AWS_TRANSCRIBE_ENABLED", "1") == "1"

# Normaliza el idioma a uno válido para Transcribe (evita BadRequest es-MX)
_lang_map = {
    "es-mx": "es-US",
    "es_mx": "es-US",
    "es":    "es-US",
    "es-us": "es-US",
    "es_es": "es-ES",
}
_lc = (AWS_TRANSCRIBE_LANG or "").lower().strip()
AWS_TRANSCRIBE_LANG = _lang_map.get(_lc, AWS_TRANSCRIBE_LANG)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")  # Debe estar en PATH
AUDIO_RATE = 16000

# ─────────────────────────────── DB UTILS ────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PWD
    )

def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS interactions (
        id SERIAL PRIMARY KEY,
        session_id TEXT,
        timestamp TIMESTAMP DEFAULT NOW(),
        audio_path TEXT,
        evaluation TEXT,          -- bloque público mostrado al usuario
        evaluation_rh TEXT,       -- JSON interno (da_vinci_points, KPIs, etc.)
        duration_seconds INTEGER DEFAULT 0,
        tip TEXT,
        visual_feedback TEXT,
        visible_to_user BOOLEAN DEFAULT FALSE,
        avatar_transcript TEXT,
        rh_comment TEXT
    );
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()

init_db()

# ─────────────────────────────── S3 HELPERS ──────────────────────────────
def s3_download_to_tmp(key: str, bucket: Optional[str] = None) -> str:
    bucket = bucket or AWS_S3_BUCKET_NAME
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET_NAME no está configurado.")
    s3 = boto3.client("s3", region_name=AWS_REGION)
    fd, tmp_path = tempfile.mkstemp(prefix="leo_", suffix=Path(key).suffix or ".webm")
    os.close(fd)
    try:
        s3.download_file(bucket, key, tmp_path)
        logger.info(f"S3 descargado: s3://{bucket}/{key} -> {tmp_path}")
        return tmp_path
    except ClientError as e:
        raise RuntimeError(f"Error descargando de S3: {e}")

# ───────────────────────────── FFMPEG (audio) ────────────────────────────
def extract_wav_16k(src_video: str) -> Tuple[str, Optional[int]]:
    out_wav = src_video + ".wav"
    cmd = [
        FFMPEG_BIN, "-y", "-i", src_video,
        "-ac", "1", "-ar", str(AUDIO_RATE), "-vn", out_wav
    ]
    t0 = time.time()
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dur = int(time.time() - t0)
    logger.info(f"Audio extraído (mono {AUDIO_RATE} Hz): {out_wav}")
    return out_wav, dur

# ───────────────────────────── Transcribe AWS ────────────────────────────
def transcribe_aws_localfile(wav_path: str) -> str:
    """
    Minimal viable: sube a S3 y usa StartTranscriptionJob.
    Si no se logra, devuelve "" (el caller hará fallback al transcript del front).
    """
    if not AWS_TRANSCRIBE_ENABLED:
        return ""

    # Subir a S3 temporal para transcribir (necesita bucket con permisos)
    s3_key = f"transcribe_input/{Path(wav_path).name}"
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.upload_file(wav_path, AWS_S3_BUCKET_NAME, s3_key)
    except Exception as e:
        logger.warning(f"No se pudo subir WAV a S3 para Transcribe: {e}")
        return ""

    transcribe = boto3.client("transcribe", region_name=AWS_REGION)
    job_name = f"leo-{int(time.time())}-{Path(wav_path).stem}".replace(".", "-")
    media_uri = f"s3://{AWS_S3_BUCKET_NAME}/{s3_key}"

    try:
        transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": media_uri},
            MediaFormat="wav",
            LanguageCode=AWS_TRANSCRIBE_LANG,
            Settings={"ShowSpeakerLabels": False},
        )
        # Poll sencillo (máx 90s)
        for _ in range(90):
            job = transcribe.get_transcription_job(TranscriptionJobName=job_name)
            status = job["TranscriptionJob"]["TranscriptionJobStatus"]
            if status in ("COMPLETED", "FAILED"):
                break
            time.sleep(1.5)

        job = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        if job["TranscriptionJob"]["TranscriptionJobStatus"] != "COMPLETED":
            logger.warning(f"Transcribe no COMPLETED: {job['TranscriptionJob']['TranscriptionJobStatus']}")
            return ""

        uri = job["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        # Descargar el JSON final
        import requests
        resp = requests.get(uri, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("results", {}).get("transcripts", [{}])[0].get("transcript", "")
        return (text or "").strip()

    except Exception as e:
        logger.warning(f"AWS Transcribe error: {e}")
        return ""
    finally:
        # Limpieza S3 temporal (ignorar errores)
        try:
            s3.delete_object(Bucket=AWS_S3_BUCKET_NAME, Key=s3_key)
        except Exception:
            pass

# ───────────────────────────── UTIL FRONT TXT ────────────────────────────
def unify_front_transcript(raw_front: Any) -> str:
    """
    Acepta str JSON (lista) o lista ya parseada. Devuelve un string unificado.
    """
    if not raw_front:
        return ""
    try:
        if isinstance(raw_front, str):
            txt = raw_front.strip()
            if txt.startswith("["):
                arr = json.loads(txt)
            else:
                arr = [txt]
        elif isinstance(raw_front, list):
            arr = raw_front
        else:
            arr = []
        parts = [str(s).strip() for s in arr if str(s).strip()]
        return "\n".join(parts)
    except Exception:
        return ""

# ────────────────────────────── TASK PRINCIPAL ───────────────────────────
@celery_app.task(name="process_session_transcript")
def process_session_transcript(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload esperado (mínimo):
    {
      "session_id": 123,
      "s3_key" | "video_object_key" | "object_key": "uploads/abc123.webm",
      "user_transcript": [... o str],   # opcional (del front)
      "leo_text": "",                   # opcional (transcript avatar)
      "duration_seconds" | "duration": 0
    }
    """
    logger.info(f"🟢 START payload={payload}")
    sid = payload.get("session_id")
    if not sid:
        raise ValueError("Falta 'session_id' en payload.")

    # Acepta alias para el key del objeto en S3
    s3_key = payload.get("s3_key") or payload.get("video_object_key") or payload.get("object_key")
    s3_url = payload.get("s3_url")
    user_transcript_front = payload.get("user_transcript")
    leo_text = payload.get("leo_text") or ""
    dur_front = int(payload.get("duration_seconds") or payload.get("duration") or 0)

    # 1) Obtener archivo local (priorizar s3_key)
    local_video = None
    try:
        if s3_key:
            local_video = s3_download_to_tmp(s3_key)
        elif s3_url:
            # Descargar por URL (presigned)
            import requests
            fd, p = tempfile.mkstemp(prefix="leo_", suffix=".webm")
            os.close(fd)
            r = requests.get(s3_url, timeout=60)
            r.raise_for_status()
            with open(p, "wb") as f:
                f.write(r.content)
            local_video = p
        else:
            raise ValueError("Falta s3_key/video_object_key/object_key o s3_url en payload.")
    except Exception as e:
        logger.error(f"Error obteniendo video: {e}")
        local_video = None

    # 2) Extraer WAV 16k si hay video
    local_wav, dur_audio = (None, None)
    if local_video:
        try:
            local_wav, dur_audio = extract_wav_16k(local_video)
        except Exception as e:
            logger.warning(f"No se pudo extraer audio: {e}")

    # 3) Transcribir con AWS (si posible)
    user_txt = ""
    if local_wav:
        try:
            user_txt = transcribe_aws_localfile(local_wav)
        except Exception as e:
            logger.warning(f"Fallo transcripción AWS: {e}")

    # 4) Fallback con transcript del front si lo anterior quedó corto o vacío
    if not user_txt or len(user_txt.split()) < 12:  # umbral mínimo
        fallback_txt = unify_front_transcript(user_transcript_front)
        if fallback_txt:
            logger.info("Usando FALLBACK del transcript del front (por baja/ausente transcripción AWS).")
            # Opcional: combinar ambos
            if user_txt:
                user_txt = (user_txt.strip() + "\n" + fallback_txt.strip()).strip()
            else:
                user_txt = fallback_txt

    # 5) Evaluar y persistir (solo si tenemos algo de texto)
    if not user_txt:
        public_msg = "No se obtuvo transcripción utilizable. Intenta nuevamente o revisa tu micrófono."
        # Aun así dejamos rastro mínimo en BD (sin tocar evaluation_rh)
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE interactions SET evaluation = %s, duration_seconds = %s, audio_path = %s, timestamp = %s WHERE id = %s",
                    (public_msg, int(dur_front or dur_audio or 0), s3_key or s3_url or "", datetime.utcnow(), int(sid))
                )
            conn.commit()
        finally:
            conn.close()
        logger.info(f"🔴 DONE sid={sid} reason=no_transcript")
        return {"ok": False, "reason": "no_transcript", "session_id": sid}

    res = evaluate_and_persist(str(sid), user_txt, leo_text, local_video or "")

    # 6) Actualizar bloque público y metadatos (NO tocar evaluation_rh aquí)
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE interactions SET evaluation = %s, duration_seconds = %s, audio_path = %s, timestamp = %s WHERE id = %s",
                (res.get("public", ""), int(dur_front or dur_audio or 0), s3_key or s3_url or "", datetime.utcnow(), int(sid))
            )
        conn.commit()
    finally:
        conn.close()

    logger.info(f"✅ DONE sid={sid} level={res.get('level', 'alto')}")
    return {"ok": True, "session_id": sid, "level": res.get("level", "alto")}
