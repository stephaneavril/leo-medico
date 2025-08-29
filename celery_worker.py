# === celery_worker.py — Eval con o sin video (fallback a transcript en BD) ===
from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional

import psycopg2
import boto3
from celery import Celery

# ─────────────────── CONFIG ───────────────────
LOG_LEVEL = os.getenv("EVAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("leo_worker")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var not set")

# Broker/backend (Render ya te inyecta REDIS_URL)
REDIS_URL = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))

# S3 (opcional, solo si quieres descargar el video)
AWS_S3_BUCKET_NAME     = (os.getenv("AWS_S3_BUCKET_NAME", "").split("#", 1)[0]).strip().strip("'\"")
AWS_S3_REGION_NAME     = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY  = os.getenv("AWS_SECRET_ACCESS_KEY")

TMP_DIR = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TMP_DIR, exist_ok=True)

# ───────────────── Celery APP ─────────────────
# ⚠️ Igual que el worker “que sí funciona”: forzamos a Celery a importar este módulo.
celery_app = Celery(
    "leo_tasks",
    broker=REDIS_URL,
    backend=os.getenv("CELERY_RESULT_BACKEND", REDIS_URL),
)
celery_app.conf.imports = ("celery_worker",)  # <- clave para que registre tareas de este módulo
# (Opcional) deja todo en la cola por defecto “celery”
celery_app.conf.task_default_queue = "celery"

# ──────────────── Evaluator import ────────────
# Mantiene tu interfaz: evaluate_and_persist(session_id, user_text, avatar_text, video_path)
from evaluator import evaluate_and_persist

# ──────────────── DB helpers ──────────────────
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

def _update_db_only_public(session_id: int, public_text: str,
                           duration_seconds: Optional[int],
                           audio_key: Optional[str],
                           ts_iso: Optional[str]):
    """Actualiza SOLO 'evaluation' + metadatos. NO pisa evaluation_rh."""
    try:
        conn = db_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            UPDATE interactions
               SET evaluation = %s,
                   duration_seconds = COALESCE(%s, duration_seconds),
                   audio_path = COALESCE(%s, audio_path),
                   timestamp = COALESCE(%s, timestamp)
             WHERE id = %s;
            """,
            (public_text, duration_seconds, audio_key, ts_iso, session_id),
        )
        conn.commit()
        conn.close()
        logger.info("✓ Public eval actualizada (session_id=%s)", session_id)
    except Exception as e:
        logger.exception("No se pudo actualizar bloque público (session_id=%s): %s", session_id, e)

def _get_transcript_from_db(session_id: int) -> str:
    """
    Usa avatar_transcript si existe; si no, intenta 'message' (puede ser JSON array).
    Compacta espacios y recorta a 24k chars.
    """
    txt = ""
    try:
        conn = db_conn()
        cur  = conn.cursor()
        cur.execute("SELECT avatar_transcript FROM interactions WHERE id=%s;", (session_id,))
        row = cur.fetchone()
        if row and row[0]:
            txt = str(row[0])

        if not txt or len(txt.strip()) < 2:
            cur.execute("SELECT message FROM interactions WHERE id=%s;", (session_id,))
            row = cur.fetchone()
            if row and row[0]:
                raw = row[0]
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        txt = " ".join(map(str, data))
                    elif isinstance(data, dict):
                        txt = " ".join(map(str, data.values()))
                    else:
                        txt = str(data)
                except Exception:
                    txt = str(raw)
        conn.close()
    except Exception as e:
        logger.exception("Error leyendo transcript de BD (session_id=%s): %s", session_id, e)

    txt = re.sub(r"\s+", " ", txt or "").strip()
    MAX_CHARS = 24000
    if len(txt) > MAX_CHARS:
        txt = txt[-MAX_CHARS:]
    return txt

# ──────────────── S3 helpers (opcional) ───────
def _s3_client() -> Optional[boto3.client]:
    if not (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_S3_BUCKET_NAME):
        return None
    return boto3.client(
        "s3",
        region_name=AWS_S3_REGION_NAME,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

def _maybe_download_from_s3(object_key: str) -> Optional[str]:
    try:
        s3 = _s3_client()
        if not s3:
            logger.info("S3 no configurado; omito descarga de %s", object_key)
            return None
        local_path = os.path.join(TMP_DIR, os.path.basename(object_key))
        s3.download_file(AWS_S3_BUCKET_NAME, object_key, local_path)
        logger.info("Descargado de S3: %s -> %s", object_key, local_path)
        return local_path
    except Exception as e:
        logger.warning("No se pudo descargar de S3 (%s): %s", object_key, e)
        return None

# ──────────────── Lógica central ──────────────
def _process_session_transcript_logic(payload: dict):
    """
    payload:
      - session_id (int)   [obligatorio]
      - video_object_key   [opcional]
      - timestamp_iso      [opcional]
      - user_text          [opcional: si ya viene transcript consolidado]
    """
    try:
        sid = int(payload.get("session_id") or 0)
    except Exception:
        sid = 0
    if not sid:
        logger.error("payload sin session_id: %r", payload)
        return

    vkey: Optional[str] = payload.get("video_object_key")
    ts_iso: Optional[str] = payload.get("timestamp_iso") or datetime.utcnow().isoformat()
    user_txt = payload.get("user_text") or ""

    # Si no hay video: caemos a transcript desde BD
    if not vkey:
        logger.warning("🚫 session %s: falta video_object_key — uso transcript en BD", sid)
        if not user_txt:
            user_txt = _get_transcript_from_db(sid)
        try:
            res = evaluate_and_persist(sid, user_txt, "", None)
            public_text = res.get("public", "Evaluación generada (sin video).")
        except Exception as e:
            logger.exception("[EVALUATE_PERSIST sin video] sid=%s error=%s", sid, e)
            public_text = "⚠️ Evaluación automática no disponible."
        _update_db_only_public(sid, public_text, duration_seconds=None, audio_key=None, ts_iso=ts_iso)
        return

    # Si hay video, descarga (opcional). Evaluamos igual por transcript (sin ASR aquí).
    local_video: Optional[str] = _maybe_download_from_s3(vkey)

    if not user_txt:
        user_txt = _get_transcript_from_db(sid)

    try:
        res = evaluate_and_persist(sid, user_txt, "", local_video)
        public_text = res.get("public", "Evaluación generada.")
    except Exception as e:
        logger.exception("[EVALUATE_PERSIST con video] sid=%s error=%s", sid, e)
        public_text = "⚠️ Evaluación automática no disponible."

    _update_db_only_public(sid, public_text, duration_seconds=None, audio_key=vkey, ts_iso=ts_iso)

    # Limpieza
    try:
        if local_video and os.path.exists(local_video):
            os.remove(local_video)
    except Exception:
        pass

# ──────────────── REGISTRO DE TAREAS ──────────
# 1) Nombre totalmente calificado (como tu ejemplo que sí funciona)
@celery_app.task(name="celery_worker.process_session_transcript", bind=True)
def task_fqdn(self, payload: dict):
    return _process_session_transcript_logic(payload)

# 2) Alias no calificado (como el que vi en tu log de error)
@celery_app.task(name="process_session_transcript", bind=True)
def task_alias(self, payload: dict):
    return _process_session_transcript_logic(payload)

# ──────────────── CLI manual ──────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--session_id", type=int, required=True)
    ap.add_argument("--video_object_key", type=str, default="")
    ap.add_argument("--user_text", type=str, default="")
    args = ap.parse_args()
    payload = {
        "session_id": args.session_id,
        "video_object_key": args.video_object_key or None,
        "user_text": args.user_text or None,
        "timestamp_iso": datetime.utcnow().isoformat()
    }
    _process_session_transcript_logic(payload)
