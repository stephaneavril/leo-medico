# celery_worker.py ─ versión estable 2025-07-04
from __future__ import annotations
import os, json, time, subprocess, secrets, requests
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from celery import Celery
import boto3, psycopg2
from botocore.exceptions import ClientError
from evaluator import evaluate_interaction          # (firma: user_text, leo_text, video_path)

# ───────── CONFIG GLOBAL ─────────
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("leo_tasks", broker=REDIS_URL, backend=REDIS_URL)

TMP_DIR = "/tmp/leo_trainer_processing"; os.makedirs(TMP_DIR, exist_ok=True)

AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "leocoach").split("#",1)[0].strip("'\"")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_ACCESS_KEY_ID  = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

s3 = boto3.client("s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME)

transcribe = boto3.client("transcribe",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME)

DATABASE_URL = os.getenv("DATABASE_URL")
def db(): p=urlparse(DATABASE_URL);return psycopg2.connect(
        database=p.path.lstrip("/"), user=p.username, password=p.password,
        host=p.hostname, port=p.port, sslmode="require")

# ───────── Helpers S3 / ffmpeg ─────────
def dl_s3(bucket,key,dst):  # ↓ devuelve bool
    try: s3.download_file(bucket,key,dst); return True
    except ClientError as e: print("[S3]",e); return False

def up_s3(src,bucket,key)->str|None:
    try: s3.upload_file(src,bucket,key)
    except ClientError as e: print("[S3]",e); return None
    return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"

def ffmpeg(cmd:list[str])->bool:
    try: subprocess.run(cmd,stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,check=True); return True
    except subprocess.CalledProcessError as e:
        print("[FFMPEG]",e.stderr.decode()); return False

# ─────────  TAREA CELERY ÚNICA ─────────
@celery_app.task
def process_session_video(d:dict):
    sid         = d["session_id"]
    vkey        = d["video_object_key"]
    dur         = int(d.get("duration",0))
    ts_iso      = datetime.utcnow().isoformat()

    # 1· Descarga WEBM
    webm = os.path.join(TMP_DIR, vkey)
    if not dl_s3(AWS_S3_BUCKET_NAME,vkey,webm):
        _update_db(sid,"⚠️ Video no encontrado en S3"); return

    # 2· Convierte → MP4 (para IA) 
    mp4 = webm.replace(".webm",".mp4")
    if ffmpeg(["ffmpeg","-i",webm,"-c:v","libx264","-preset","fast",
               "-c:a","aac","-y",mp4]):
        video_ai = mp4
    else:
        video_ai = webm

    # 3· Extrae audio + Transcribe
    wav = webm.replace(".webm",".wav")
    ffmpeg(["ffmpeg","-i",video_ai,"-vn","-acodec","pcm_s16le",
            "-ar","16000","-ac","1","-y",wav])
    audio_url = up_s3(wav,AWS_S3_BUCKET_NAME,f"audio/{os.path.basename(wav)}")
    user_txt = ""
    if audio_url:
        job = f"leo-{secrets.token_hex(6)}"
        transcribe.start_transcription_job(
            TranscriptionJobName=job,
            Media={"MediaFileUri":audio_url},
            MediaFormat="wav",
            LanguageCode="es-US")
        for _ in range(60):
            st = transcribe.get_transcription_job(
                TranscriptionJobName=job)["TranscriptionJob"]["TranscriptionJobStatus"]
            if st in {"COMPLETED","FAILED"}: break
            time.sleep(10)
        if st=="COMPLETED":
            uri = transcribe.get_transcription_job(
                TranscriptionJobName=job)["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
            user_txt = requests.get(uri).json()["results"]["transcripts"][0]["transcript"]

    # 4· Evalúa con OpenAI
    clean_txt = user_txt.strip() or "Transcripción no disponible"
    try:
        res = evaluate_interaction(clean_txt, "", video_ai)
        pub  = res.get("public","Evaluación no disponible.")
        rh   = res.get("internal",{})
    except Exception as e:
        pub, rh = "⚠️ Evaluación automática no disponible.", {"error":str(e)}

    # 5· Guarda en BD
    _update_db(sid,pub,rh,dur,vkey,ts_iso)

    # Limpieza tmp
    for f in (webm,mp4,wav):
        try: os.remove(f)
        except FileNotFoundError: pass

def _update_db(sid:int,pub:str,rh:dict|None=None,dur:int=0,vkey:str|None=None,ts:str|None=None):
    conn=db(); cur=conn.cursor()
    cur.execute("""UPDATE interactions SET
                   evaluation=%s, evaluation_rh=%s, duration_seconds=%s,
                   audio_path=%s, timestamp=%s, visible_to_user=FALSE
                   WHERE id=%s;""",
                (pub,json.dumps(rh or {}),dur,vkey,ts,sid))
    conn.commit(); conn.close()
