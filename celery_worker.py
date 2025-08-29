# === celery_worker.py — Eval con o sin video (fallback a transcript en BD) ===
"""
Flujo resumido
--------------
- Recibe payload con: session_id (obligatorio), video_object_key (opcional), timestamp_iso (opcional)
- Si viene video_object_key:
    1) [Opcional] Descarga de S3 (si AWS_* configurado)
    2) (Opcional) Extrae audio / transcribe si lo necesitas (hook disponible)
    3) Llama a evaluate_and_persist(session_id, user_text, leo_text="", video_path o None)
    4) Actualiza SOLO el bloque 'evaluation' público y metadatos
- Si NO viene video_object_key:
    • Fallback: intenta leer 'avatar_transcript' (y si no, 'message') desde la BD
    • Evalúa igual (texto-solo) y actualiza bloque público
- Nunca pisa 'evaluation_rh' (lo guarda evaluator.evaluate_and_persist)
"""

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

# ---------- Config básica ----------
LOG_LEVEL = os.getenv("EVAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL    = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "")) or "redis://localhost:6379/0"

# AWS (opcional para descargar el video)
AWS_S3_BUCKET_NAME     = (os.getenv("AWS_S3_BUCKET_NAME", "").split("#", 1)[0]).strip().strip("'\"")
AWS_S3_REGION_NAME     = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY  = os.getenv("AWS_SECRET_ACCESS_KEY")

TMP_DIR = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TMP_DIR, exist_ok=True)

# ---------- Instancia Celery ----------
# Usa SIEMPRE la misma instancia para crear y registrar tareas
app = Celery(
    "leo_worker",
    broker=REDIS_URL,
    backend=os.getenv("CELERY_RESULT_BACKEND", REDIS_URL),
)

# Alias para que tu comando de Render siga funcionando tal cual:
# celery -A celery_worker:celery_app worker ...
celery_app = app

# Config defensiva (opcional, no rompe nada)
app.conf.update(
    task_default_queue="celery",
    task_routes={"process_session_transcript": {"queue": "celery"}},
)

__all__ = ["app", "celery_app"]

# ---------- DB helpers ----------
def db_conn():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL env var is required")
    parsed = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        sslmode="require",
    )

def _update_db_only_public(session_id: int, public_text: str, duration_seconds: Optional[int], audio_key: Optional[str], ts_iso: Optional[str]):
    """
    Actualiza SOLO el bloque público y metadatos. NO pisa evaluation_rh.
    """
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
            (public_text, duration_seconds, audio_key, ts_iso, session_id)
        )
        conn.commit()
        conn.close()
        logger.info("✓ Actualizado bloque público de session_id=%s", session_id)
    except Exception as e:
        logger.exception("No se pudo actualizar bloque público (session_id=%s): %s", session_id, e)

def _get_transcript_from_db(session_id: int) -> str:
    """
    Intenta leer avatar_transcript (texto plano).
    Si no existe, intenta 'message' (que podría ser JSON o texto).
    """
    txt = ""
    try:
        conn = db_conn()
        cur  = conn.cursor()
        # 1) avatar_transcript
        cur.execute("SELECT avatar_transcript FROM interactions WHERE id=%s;", (session_id,))
        row = cur.fetchone()
        if row and row[0]:
            txt = str(row[0])

        # 2) fallback: message
        if not txt or len(txt.strip()) < 2:
            cur.execute("SELECT message FROM interactions WHERE id=%s;", (session_id,))
            row = cur.fetchone()
            if row and row[0]:
                val = row[0]
                # Puede venir como JSON (lista de turnos) o como texto
                try:
                    data = json.loads(val)
                    if isinstance(data, list):
                        txt = " ".join(map(str, data))
                    elif isinstance(data, dict):
                        txt = " ".join(map(str, data.values()))
                    else:
                        txt = str(data)
                except Exception:
                    txt = str(val)

        conn.close()
    except Exception as e:
        logger.exception("Error leyendo transcript de BD (session_id=%s): %s", session_id, e)

    # Compactar espacios y recortar para tokens
    txt = re.sub(r"\s+", " ", txt or "").strip()
    MAX_CHARS = 24000
    if len(txt) > MAX_CHARS:
        txt = txt[-MAX_CHARS:]
    return txt

# ---------- S3 helper (opcional) ----------
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
    """
    Descarga el objeto a /tmp si las credenciales están configuradas.
    Devuelve ruta local o None si no descargó.
    """
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

# ---------- Import del evaluator ----------
from evaluator import evaluate_and_persist

# ---------- Tarea principal ----------
@app.task(name="process_session_transcript", bind=True)
def process_session_transcript(self, payload: dict):
    """
    payload esperado:
    {
        "session_id": int,
        "video_object_key": "s3/key/opcional" | None,
        "timestamp_iso": "YYYY-MM-DDTHH:MM:SSZ" | None,
        "user_text": "opcional, si ya viene transcript listo"
    }
    """
    try:
        sid = int(payload.get("session_id") or 0)
    except Exception:
        sid = 0
    if not sid:
        logger.error("payload sin session_id: %r", payload)
        return

    vkey: Optional[str] = payload.get("video_object_key")
    ts_iso: Optional[str] = payload.get("timestamp_iso")
    if not ts_iso:
        ts_iso = datetime.utcnow().isoformat()

    # Por si el frontend ya mandó texto procesado:
    user_txt = payload.get("user_text") or ""

    # 1) Si NO hay video —> FALLBACK a BD (avatar_transcript/message)
    if not vkey:
        logger.warning("🚫 session %s: falta video_object_key — uso transcript en BD", sid)
        if not user_txt:
            user_txt = _get_transcript_from_db(sid)

        # Evalúa AÚN SIN VIDEO
        try:
            res = evaluate_and_persist(sid, user_txt, "", None)
            public_text = res.get("public", "Evaluación generada (sin video).")
        except Exception as e:
            logger.exception("[EVALUATE_PERSIST sin video] sid=%s error=%s", sid, e)
            public_text = "⚠️ Evaluación automática no disponible."

        _update_db_only_public(sid, public_text, duration_seconds=None, audio_key=None, ts_iso=ts_iso)
        return

    # 2) Si hay video —> (opcional) descarga y procesa
    local_video: Optional[str] = _maybe_download_from_s3(vkey)

    # TODO: Si quieres ASR aquí, implementa extracción de audio y transcripción.
    # Para mantenerlo simple y robusto, leemos de BD si no llega 'user_text'.
    if not user_txt:
        user_txt = _get_transcript_from_db(sid)

    # Eval con o sin video_path
    try:
        res = evaluate_and_persist(sid, user_txt, "", local_video)
        public_text = res.get("public", "Evaluación generada.")
    except Exception as e:
        logger.exception("[EVALUATE_PERSIST con video] sid=%s error=%s", sid, e)
        public_text = "⚠️ Evaluación automática no disponible."

    # Metadatos: si no tienes duración real, déjalo en None para no pisar
    _update_db_only_public(sid, public_text, duration_seconds=None, audio_key=vkey, ts_iso=ts_iso)

    # Limpieza local
    try:
        if local_video and os.path.exists(local_video):
            os.remove(local_video)
    except Exception:
        pass


# ---------- Registro defensivo de la tarea ----------
# Si por cualquier motivo el decorador no se ejecutó en import-time,
# garantizamos que la tarea esté registrada en ESTA instancia.
if "process_session_transcript" not in app.tasks:
    app.tasks.register(process_session_transcript)
    logger.info("Registro defensivo: process_session_transcript añadido a app.tasks")

# ---------- Utilidad manual (opcional) ----------
if __name__ == "__main__":
    # Pequeña prueba manual (invocar como script)
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
    # Con bind=True, usa .run(...) para ejecutar síncrono sin broker
    process_session_transcript.run(payload)
