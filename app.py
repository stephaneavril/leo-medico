from __future__ import annotations

# ----------------------------------------------------------------------
#  I M P O R T S
# ----------------------------------------------------------------------
import os
import re
import json
import jwt
import secrets
from uuid import uuid4
from datetime import datetime, date, timedelta
from urllib.parse import urlparse
from typing import Union
from functools import wraps

import boto3
from botocore.exceptions import ClientError
import psycopg2
import psycopg2.extras
from flask import (
    Flask, request, jsonify, render_template,
    redirect, session, send_file, url_for, make_response
)
from flask_cors import CORS, cross_origin
from werkzeug.utils import secure_filename

from dotenv import load_dotenv

# Celery task (sin circular import: celery_worker **no** importa app.py)
from celery_worker import process_session_video

# ----------------------------------------------------------------------
#  1) C O N F I G U R A C I Ó N   B Á S I C A
# ----------------------------------------------------------------------
load_dotenv(override=True)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMP_PROCESSING_FOLDER = os.getenv(
    "TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing"
)
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(16))

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://leo-frontend.onrender.com")
CORS(
    app,
    resources={r"/*": {"origins": [FRONTEND_URL]}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=True,
)
app.config["UPLOAD_FOLDER"] = TEMP_PROCESSING_FOLDER

# ----------------------------------------------------------------------
#  2) C L I E N T E S   E X T E R N O S   (AWS y PostgreSQL)
# ----------------------------------------------------------------------
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME    = os.getenv("AWS_S3_BUCKET_NAME", "").split("#", 1)[0].strip("'\" ")
AWS_S3_REGION_NAME    = os.getenv("AWS_S3_REGION_NAME", "us-west-2")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME,
)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

def get_db_connection():
    parsed = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        sslmode="require",
    )

# ----------------------------------------------------------------------
#  3) B D   –  C R E A C I Ó N   Y   P A R C H E S
# ----------------------------------------------------------------------
def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
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
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                email TEXT UNIQUE,
                start_date TEXT,
                end_date TEXT,
                active INTEGER DEFAULT 1,
                token TEXT UNIQUE
            );
        """)
        conn.commit()
        print("📄  Base de datos creada / existente.")
    except Exception as e:
        print(f"🔥 init_db error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def patch_db_schema():
    fields = [
        ("interactions", "tip",              "ALTER TABLE interactions ADD COLUMN tip TEXT;"),
        ("interactions", "visual_feedback",  "ALTER TABLE interactions ADD COLUMN visual_feedback TEXT;"),
        ("interactions", "avatar_transcript","ALTER TABLE interactions ADD COLUMN avatar_transcript TEXT;"),
        ("interactions", "visible_to_user",  "ALTER TABLE interactions ADD COLUMN visible_to_user BOOLEAN DEFAULT FALSE;"),
        ("interactions", "evaluation_rh",    "ALTER TABLE interactions ADD COLUMN evaluation_rh TEXT;"),
        ("users",        "token",            "ALTER TABLE users ADD COLUMN token TEXT UNIQUE;"),
    ]
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        for table, column, ddl in fields:
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                 WHERE table_name=%s AND column_name=%s;
            """, (table, column))
            if not cur.fetchone():
                print(f"🛠️  Añadiendo columna '{column}' a {table} …")
                cur.execute(ddl)
        conn.commit()
    except Exception as e:
        print(f"🔥 patch_db_schema error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

init_db()
patch_db_schema()

# ----------------------------------------------------------------------
#  4) U T I L I D A D E S   G E N E R A L E S
# ----------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG    = "HS256"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def issue_jwt(payload: dict, ttl_days: int = 7) -> str:
    payload = payload.copy()
    payload.update(iat=datetime.utcnow(),
                   exp=datetime.utcnow() + timedelta(days=ttl_days))
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def upload_file_to_s3(local_path: str, bucket: str, key: str) -> str | None:
    try:
        s3_client.upload_file(local_path, bucket, key)
        return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{key}"
    except ClientError as e:
        print(f"[S3] Upload error: {e}")
        return None

def _json_list(obj) -> str:
    if isinstance(obj, list):
        return json.dumps(obj)
    if isinstance(obj, str):
        return json.dumps([l for l in obj.splitlines() if l.strip()])
    return "[]"

# ----------------------------------------------------------------------
#  5)  H O O K S   &   M I D D L E W A R E
# ----------------------------------------------------------------------
@app.before_request
def log_request_info():
    print(f"[REQ] {request.method} {request.path}")
    if request.method == "POST":
        print(f"↳ FORM: {request.form}")
        print(f"↳ FILES: {request.files}")
        try:
            print(f"↳ JSON: {request.json}")
        except Exception:
            pass

# ----------------------------------------------------------------------
#  6)  A U T H   D E C O R A D O R  (JWT)
# ----------------------------------------------------------------------
def jwt_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.split("Bearer ", 1)[1] if auth.startswith("Bearer ") else None
        if not token:
            return jsonify(error="token faltante"), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
            request.jwt = payload
        except Exception as e:
            print(f"[JWT ERROR] {e}")
            return jsonify(error="token inválido"), 401
        return f(*args, **kwargs)
    return _wrap

# ----------------------------------------------------------------------
#  7)  R U T A S   P Ú B L I C A S   (login / salud / vídeo firmado)
# ----------------------------------------------------------------------
@app.route("/healthz")
def health_check():
    return "OK", 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["password"] == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return "Contraseña incorrecta", 403
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/get_presigned_url/<key>")
def get_presigned_url(key):
    url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )
    return jsonify({"url": url})

@app.route("/video/<path:filename>")
def serve_video(filename):
    presigned = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": filename},
        ExpiresIn=3600,
    )
    return redirect(presigned, code=302)

# ----------------------------------------------------------------------
#  8)  G E S T I Ó N   D E   U S U A R I O S
# ----------------------------------------------------------------------
@app.post("/admin/users")
def create_user():
    data  = request.get_json(force=True)
    name  = data.get("name","").strip()
    email = data.get("email","").strip().lower()
    if not name or not email:
        return "falta nombre o email", 400

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, token FROM users WHERE email=%s", (email,))
            row = cur.fetchone()
            if row:
                user_id, token = row["id"], row["token"]
            else:
                token = issue_jwt({"name": name, "email": email})
                cur.execute("""
                    INSERT INTO users (name,email,start_date,end_date,active,token)
                         VALUES (%s,%s,%s,%s,1,%s)
                         RETURNING id
                """, (name, email, date.today(),
                      date.today() + timedelta(days=365), token))
                user_id = cur.fetchone()[0]
        conn.commit()
        return jsonify(
            user_id=user_id,
            token=token,
            date_from=date.today().isoformat(),
            date_to=(date.today()+timedelta(days=365)).isoformat(),
        ), 201
    finally:
        conn.close()

# ----------------------------------------------------------------------
#  9)  S E S I Ó N   D E   I A   (start-session / upload / log)
# ----------------------------------------------------------------------
SENTINELS = [
  "Video_Not_Available_Error",
  "Video_Processing_Failed",
  "Video_Missing_Error",
]

@app.route("/start-session", methods=["POST"])
def start_session():
    name     = request.form.get("name")
    email    = request.form.get("email")
    scenario = request.form.get("scenario")
    if not all([name, email, scenario]):
        return "Faltan datos.", 400

    today = date.today().isoformat()
    conn  = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT active, start_date, end_date
                  FROM users WHERE email = %s
            """, (email,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return "Usuario no registrado.", 403
    active, start, end = row
    if not active or not (start <= today <= end):
        return "Sin vigencia.", 403

    token = jwt.encode(
        {
            "name": name,
            "email": email,
            "scenario": scenario,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm=JWT_ALG,
    )
    url = f"{FRONTEND_URL}/dashboard?auth={token}"
    return redirect(url, code=302)

@app.route("/upload_video", methods=["POST"])
@jwt_required
def upload_video():
    email = request.jwt["email"]
    video_file = request.files.get("video")
    if not video_file:
        return jsonify(status="error", message="Falta archivo de video"), 400

    filename = secure_filename(
        f"{email.replace('@', '_at_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.webm"
    )
    local_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    try:
        video_file.save(local_path)
        if not upload_file_to_s3(local_path, AWS_S3_BUCKET_NAME, filename):
            raise Exception("Fallo en la subida a S3.")
        return jsonify(status="ok", s3_object_key=filename)
    except Exception as e:
        return jsonify(status="error", message=str(e)), 500
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

def _as_json_list(txt: Union[str, list]) -> str:
    if isinstance(txt, list):
        return json.dumps(txt)
    if isinstance(txt, str):
        return json.dumps([l for l in txt.splitlines() if l.strip()])
    return json.dumps([])

@app.route("/log_full_session", methods=["POST"])
def log_full_session():
    data = request.get_json() or {}
    name       = data.get("name")
    email      = data.get("email")
    scenario   = data.get("scenario")
    duration   = int(data.get("duration", 0))
    video_key  = data.get("video_object_key") or data.get("s3_object_key")

    user_json   = _as_json_list(data.get("conversation", ""))
    avatar_json = _as_json_list(data.get("avatar_transcript", ""))

    timestamp_iso = datetime.utcnow().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO interactions
                       (name,email,scenario,message,response,audio_path,
                        timestamp,duration_seconds)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                name, email, scenario,
                user_json, avatar_json,
                video_key, timestamp_iso, duration
            ))
            session_id = cur.fetchone()[0]
        conn.commit()
        process_session_video.delay({
            "session_id": session_id,
            "name": name,
            "email": email,
            "scenario": scenario,
            "duration": duration,
            "video_object_key": video_key,
            "conversation": user_json,
            "avatar_transcript": avatar_json
        })
        return jsonify(status="success", session_id=session_id), 200
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(status="error", message=str(e)), 500
    finally:
        if conn:
            conn.close()

# ----------------------------------------------------------------------
# 10)  D A S H B O A R D   &   A D M I N
# ----------------------------------------------------------------------
def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", " ").replace("\n", " ").strip()
    replacements = {
        r"\303\251": "é", r"\303\241": "á", r"\303\255": "í",
        r"\303\263": "ó", r"\303\272": "ú", r"\303\261": "ñ",
        r"\302\277": "¿", r"\302\241": "¡",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)             # aaa → aa
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if not session.get("admin"):
        return redirect("/login")

    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        # -------- Acciones POST (alta/editar usuarios) ----------
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                name  = request.form["name"]
                email = request.form["email"]
                start = request.form["start_date"]
                end   = request.form["end_date"]
                token = secrets.token_hex(8)
                cur.execute("""
                    INSERT INTO users (name,email,start_date,end_date,active,token)
                    VALUES (%s,%s,%s,%s,1,%s)
                    ON CONFLICT (email) DO UPDATE SET
                        name=EXCLUDED.name,
                        start_date=EXCLUDED.start_date,
                        end_date=EXCLUDED.end_date,
                        active=EXCLUDED.active,
                        token=EXCLUDED.token;
                """, (name,email,start,end,token))
            elif action == "toggle":
                cur.execute("UPDATE users SET active = 1 - active WHERE id = %s",
                            (int(request.form["user_id"]),))
            elif action == "regen_token":
                cur.execute("UPDATE users SET token = %s WHERE id = %s",
                            (secrets.token_hex(8), int(request.form["user_id"])))
            conn.commit()

        # -------- Datos para el template ------------------------
        cur.execute("""
            SELECT id,name,email,scenario,message,response,audio_path,timestamp,
                   evaluation,evaluation_rh,tip,visual_feedback,visible_to_user
              FROM interactions
             ORDER BY timestamp DESC
        """)
        raw_data = cur.fetchall()

        processed_data = []
        for row in raw_data:
            # índices descritos en tu código previo
            user_dialogue_raw   = json.loads(row[4]) if row[4] else []
            avatar_dialogue_raw = json.loads(row[5]) if row[5] else []
            cleaned_user_seg    = [clean_display_text(str(s)) for s in user_dialogue_raw]
            cleaned_avatar_seg  = [clean_display_text(str(s)) for s in avatar_dialogue_raw]

            video_url = f"/video/{row[6]}" if row[6] else None
            try:
                rh_eval = json.loads(row[9]) if row[9] else {"status":"pendiente"}
            except Exception:
                rh_eval = {"status":"pendiente"}

            processed_data.append([
                row[0],                                         # id
                clean_display_text(row[1] or ""),               # name
                clean_display_text(row[2] or ""),               # email
                clean_display_text(row[3] or ""),               # scenario
                cleaned_user_seg,                               # user dialogue
                cleaned_avatar_seg,                             # avatar diag
                video_url,                                      # video
                row[7],                                         # ts
                row[8] or "Análisis IA pendiente.",             # resumen
                rh_eval,                                        # rh eval
                row[10] or "Consejo pendiente.",                # tip
                row[11] or "Análisis visual pendiente.",        # visual
                row[12],                                        # visible
            ])

        cur.execute("SELECT id,name,email,start_date,end_date,active,token FROM users")
        users = cur.fetchall()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur_u:
            cur_u.execute("""
                SELECT u.name,u.email,COALESCE(SUM(i.duration_seconds),0) AS total_seconds_used
                  FROM users u
             LEFT JOIN interactions i ON u.email=i.email
              GROUP BY u.name,u.email
            """)
            usage_rows = cur_u.fetchall()

        usage_summaries, total_minutes = [], 0
        for r in usage_rows:
            mins = r["total_seconds_used"] // 60
            total_minutes += mins
            usage_summaries.append({
                "name": r["name"], "email": r["email"], "minutes": mins,
                "summary": ("Buen desempeño" if mins>=15 else
                            "Actividad moderada" if mins>=5 else
                            "Baja actividad"),
            })

    except Exception as e:
        print(f"[ADMIN] error: {e}")
        if conn:
            conn.rollback()
        return f"Error en admin: {e}", 500
    finally:
        if conn:
            conn.close()

    return render_template(
        "admin.html",
        data=processed_data,
        users=users,
        usage_summaries=usage_summaries,
        total_minutes=total_minutes,
        contracted_minutes=1050,
    )

@app.route("/admin/publish_eval/<int:session_id>", methods=["POST"])
def publish_eval(session_id):
    if not session.get("admin"):
        return redirect("/login")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE interactions SET visible_to_user=TRUE WHERE id=%s",
                        (session_id,))
        conn.commit()
    finally:
        conn.close()
    return redirect("/admin")

# ----------------------------------------------------------------------
# 11)  D A S H B O A R D   P A R A   U S U A R I O
# ----------------------------------------------------------------------
@app.get("/dashboard_data")
@cross_origin()
@jwt_required
def dashboard_data():
    email = request.jwt["email"]
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,scenario,timestamp AS created_at,duration_seconds AS duration,
                       message AS user_transcript,response AS avatar_transcript,
                       evaluation AS coach_advice,visual_feedback,
                       audio_path AS video_s3,tip,evaluation_rh AS rh_evaluation
                  FROM interactions
                 WHERE email=%s
              ORDER BY timestamp DESC
                 LIMIT 50
            """, (email,))
            raw_rows = cur.fetchall()

            sessions = []
            for row in raw_rows:
                sess = dict(row)
                # Transcripciones en texto plano
                for field in ("user_transcript", "avatar_transcript"):
                    raw = sess.get(field, "[]")
                    try: sess[field] = "\n".join(json.loads(raw))
                    except Exception: pass

                key = sess.get("video_s3")
                if key and key not in SENTINELS:
                    try:
                        sess["video_s3"] = s3_client.generate_presigned_url(
                            ClientMethod="get_object",
                            Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": key},
                            ExpiresIn=3600,
                        )
                    except ClientError:
                        sess["video_s3"] = None
                else:
                    sess["video_s3"] = None
                sessions.append(sess)

            cur.execute("""
                SELECT COALESCE(SUM(duration_seconds),0) AS total_seconds_used
                  FROM interactions WHERE email=%s
            """, (email,))
            total_used_seconds = cur.fetchone()["total_seconds_used"]

        auth_header = request.headers.get("Authorization", "")
        user_token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header

        return jsonify(
            name=request.jwt["name"],
            email=email,
            user_token=user_token,
            sessions=sessions,
            used_seconds=total_used_seconds,
        ), 200

    except Exception as e:
        app.logger.exception("dashboard_data error")
        return jsonify(error=str(e)), 500
    finally:
        if conn:
            conn.close()

# ----------------------------------------------------------------------
# 12)  P U N T O   D E   E N T R A D A   L O C A L
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
