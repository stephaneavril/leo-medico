import os
import psycopg2
from urllib.parse import urlparse
import json
import secrets
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, make_response, send_file # type: ignore
from flask_cors import CORS # type: ignore
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from typing import Union

import openai
import boto3
from botocore.exceptions import ClientError
import re

print("\U0001F680 Iniciando Leo Virtual Trainer (Modo Simple)...")

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "leo-trainer-videos")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-west-2")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME
)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set!")
    
    parsed_url = urlparse(database_url)
    conn = psycopg2.connect(
        database=parsed_url.path[1:],
        user=parsed_url.username,
        password=parsed_url.password,
        host=parsed_url.hostname,
        port=parsed_url.port,
        sslmode='require'
    )
    return conn

TEMP_PROCESSING_FOLDER = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
CORS(app)

@app.before_request
def log_request_info():
    print(f"DEBUG_HOOK: Request received: {request.method} {request.path}")
    if request.method == 'POST':
        print(f"DEBUG_HOOK: Form data: {request.form}")
        print(f"DEBUG_HOOK: Files: {request.files}")
        try:
            print(f"DEBUG_HOOK: JSON data: {request.json}")
        except Exception:
            print("DEBUG_HOOK: No JSON data or invalid JSON")

app.config['UPLOAD_FOLDER'] = TEMP_PROCESSING_FOLDER

app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key-fallback")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
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
                visual_feedback TEXT
            );
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                email TEXT UNIQUE,
                start_date TEXT,
                end_date TEXT,
                active INTEGER DEFAULT 1,
                token TEXT UNIQUE
            );
        ''')
        conn.commit()
        print("\U0001F4C3 Database initialized or already exists (PostgreSQL).")
    except Exception as e:
        print(f"\U0001F525 Error initializing PostgreSQL database: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def patch_db_schema():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='interactions' AND column_name='evaluation_rh'")
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN evaluation_rh TEXT;")
            print("Added 'evaluation_rh' to interactions table.")

        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='interactions' AND column_name='tip'")
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN tip TEXT;")
            print("Added 'tip' to interactions table.")

        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='interactions' AND column_name='visual_feedback'")
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN visual_feedback TEXT;")
            print("Added 'visual_feedback' to interactions table.")

        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='token'")
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN token TEXT UNIQUE;")
            print("Added 'token' to users table.")

        conn.commit()
        print("\U0001F527 Database schema patched (PostgreSQL).")
    except Exception as e:
        print(f"\U0001F525 Error patching PostgreSQL database schema: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

init_db()
patch_db_schema()

def upload_file_to_s3(file_path, bucket, object_name=None):
    """Sube un archivo a un bucket de S3"""
    if object_name is None:
        object_name = os.path.basename(file_path)
    try:
        s3_client.upload_file(file_path, bucket, object_name)
        print(f"[S3 UPLOAD] Archivo {file_path} subido a s3://{bucket}/{object_name}")
        return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{object_name}"
    except ClientError as e:
        print(f"[S3 ERROR] Falló la subida a S3: {e}")
        return None

# helpers.py  (o dentro de app.py)

from datetime import date
import psycopg2, psycopg2.extras

def check_user_token(email: str, token: str) -> bool:
    """Devuelve True si el email+token son válidos y el usuario está activo."""
    today = date.today().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT active, start_date, end_date, token
                FROM   users
                WHERE  email = %s
                """,
                (email.lower().strip(),)
            )
            row = cur.fetchone()

        # ─── N O  M O V E R  ───────────────────────────────────────────
        if not row:                   # usuario no existe
            return False

        active, start, end, stored_token = row  # ← ahora sí existe `row`
        print(f"[DEBUG] cookie_token='{token}'  stored_token='{stored_token}'")

        return (
            active
            and (start is None or start <= today)
            and (end   is None or end   >= today)
            and stored_token.strip() == token.strip()
        )
    finally:
        if conn:
            conn.close()

# Todas las rutas de Flask
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

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

# Inside app.py, add this helper function
def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        return ""

    # Normalize common spaces/newlines before splitting
    text = text.replace('\r\n', ' ').replace('\n', ' ').strip()
    
    # Replace common escaped UTF-8 sequences if they appear literally
    # This is a heuristic for specific mis-encodings like "\303\251" becoming literal text
    text = text.replace('\\303\\251', 'é') # Common for 'é'
    text = text.replace('\\303\\241', 'á') # For 'á'
    text = text.replace('\\303\\255', 'í') # For 'í'
    text = text.replace('\\303\\263', 'ó') # For 'ó'
    text = text.replace('\\303\\272', 'ú') # For 'ú'
    text = text.replace('\\303\\261', 'ñ') # For 'ñ'
    text = text.replace('\\302\\277', '¿') # For '¿'
    text = text.replace('\\302\\241', '¡') # For '¡'

    # Attempt to remove repeated words/phrases (heuristic)
    words = text.split(' ')
    cleaned_words_list = []
    last_word = None
    for word in words:
        if word != last_word: # Simple check for direct word repetition
            cleaned_words_list.append(word)
        last_word = word
    text = ' '.join(cleaned_words_list)
    
    # Remove consecutive repeated characters (e.g., "Buenoos" -> "Buenos")
    # This regex replaces 'aa', 'bb', etc. with 'a', 'b' only if they appear 3 or more times
    text = re.sub(r'(.)\1{2,}', r'\1\1', text) # Keep at least two, e.g., "hellooo" -> "helloo"

    # Fix consecutive repeated words like "hola hola" -> "hola"
    # This regex is more robust for actual word repetitions
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
    
    # Further cleaning for concatenated words without space (e.g., "diasdias" -> "dias dias")
    # This pattern looks for a word repeated immediately without a space in between, but is very aggressive.
    # It might be better to skip this if it causes false positives.
    # For a safer approach, if 'HolaHola' is common, you might specifically replace it.
    # For general conversational text, this is hard without an NLU model.
    # text = re.sub(r'([a-zA-ZáéíóúÁÉÍÓÚñÑ]+?)\1([a-zA-ZáéíóúÁÉÍÓÚñÑ]*)', r'\1 \1\2', text)
    
    # Fix multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if not session.get("admin"):
        return redirect("/login")

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                name = request.form["name"]
                email = request.form["email"]
                start = request.form["start_date"]
                end = request.form["end_date"]
                token = secrets.token_hex(8)
                try:
                    c.execute("""INSERT INTO users (name, email, start_date, end_date, active, token)
                                       VALUES (%s, %s, %s, %s, 1, %s)
                                       ON CONFLICT (email) DO UPDATE SET
                                       name = EXCLUDED.name,
                                       start_date = EXCLUDED.start_date,
                                       end_date = EXCLUDED.end_date,
                                       active = EXCLUDED.active,
                                       token = EXCLUDED.token;""", (name, email, start, end, token))
                    conn.commit()
                except Exception as e:
                    if conn:
                        conn.rollback()
                    return f"Error al guardar usuario: {str(e)}", 500
            elif action == "toggle":
                user_id = int(request.form["user_id"])
                c.execute("UPDATE users SET active = 1 - active WHERE id = %s", (user_id,))
            elif action == "regen_token":
                user_id = int(request.form["user_id"])
                new_token = secrets.token_hex(8)
                c.execute("UPDATE users SET token = %s WHERE id = %s", (new_token, user_id))
            conn.commit()

        c.execute("""SELECT id, name, email, scenario, message, response, audio_path, timestamp, evaluation, evaluation_rh, tip, visual_feedback
                         FROM interactions
                         ORDER BY timestamp DESC""")
        raw_data = c.fetchall()

        processed_data = []
        for row in raw_data:
            try:
                # Parse raw JSON from DB
                user_dialogue_raw = json.loads(row[4]) if row[4] else []
                avatar_dialogue_raw = json.loads(row[5]) if row[5] else []

                # Ensure they are lists, even if JSON was a single string
                if not isinstance(user_dialogue_raw, list):
                    user_dialogue_raw = [str(user_dialogue_raw)]
                if not isinstance(avatar_dialogue_raw, list):
                    avatar_dialogue_raw = [str(avatar_dialogue_raw)]

                # Clean each individual segment
                cleaned_user_segments = [clean_display_text(s) for s in user_dialogue_raw if s.strip()]
                cleaned_avatar_segments = [clean_display_text(s) for s in avatar_dialogue_raw if s.strip()]

                # Clean name and email for display
                cleaned_name = clean_display_text(row[0]) if row[0] else ""
                cleaned_email = clean_display_text(row[1]) if row[1] else ""

            except (json.JSONDecodeError, TypeError) as e:
                print(f"Error parsing conversation JSON: {e}")
                # Provide default cleaned segments in case of error
                cleaned_user_segments = ["Error al cargar transcripción del participante (JSON inválido)."]
                cleaned_avatar_segments = ["Error al cargar transcripción del avatar (JSON inválido)."]
                cleaned_name = clean_display_text(row[0]) if row[0] else ""
                cleaned_email = clean_display_text(row[1]) if row[1] else ""


            try:
                parsed_rh_evaluation = json.loads(row[9])
                if not parsed_rh_evaluation:
                  parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}
            except (json.JSONDecodeError, TypeError):
                parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}


            processed_row = list(row)
            processed_row[0] = cleaned_name # Use cleaned name for display
            processed_row[1] = cleaned_email # Use cleaned email for display
            processed_row[3] = cleaned_user_segments # Pass cleaned user segments to row[3]
            processed_row[4] = cleaned_avatar_segments # Pass cleaned avatar segments to row[4]
            # row[5] originally 'response', we can assign parsed_rh_evaluation to row[9] directly
            # Ensure row[9] is indeed evaluation_rh from DB query
            processed_row[9] = parsed_rh_evaluation


            if not processed_row[8]:
                 processed_row[8] = "Análisis IA pendiente."
            if not processed_row[10]:
                 processed_row[10] = "Consejo pendiente."
            if not processed_row[11]:
                 processed_row[11] = "Análisis visual pendiente."

            processed_data.append(processed_row)

        c.execute("SELECT id, name, email, start_date, end_date, active, token FROM users")
        users = c.fetchall()

        c.execute("""
            SELECT u.name, u.email, COALESCE(SUM(i.duration_seconds), 0) AS total_seconds_used
            FROM users u
            LEFT JOIN interactions i ON u.email = i.email
            GROUP BY u.name, u.email
        """)
        usage_rows = c.fetchall()

        usage_summaries = []
        total_minutes_all_users = 0
        for name_u, email_u, secs_dict in usage_rows: # secs_dict is a dict from RealDictCursor
            mins = secs_dict['total_seconds_used'] // 60
            total_minutes_all_users += mins
            summary = "Buen desempeño general" if mins >= 15 else "Actividad moderada" if mins >= 5 else "Poca actividad, se sugiere seguimiento"
            usage_summaries.append({
                "name": name_u,
                "email": email_u,
                "minutes": mins,
                "summary": summary
            })

        contracted_minutes = 1050

    except Exception as e:
        print(f"Error en el panel de administración (PostgreSQL): {e}")
        if conn:
            conn.rollback()
        return f"Error en el panel de administración: {str(e)}", 500
    finally:
        if conn:
            conn.close()

    return render_template(
        "admin.html",
        data=processed_data,
        users=users,
        usage_summaries=usage_summaries,
        total_minutes=total_minutes_all_users,
        contracted_minutes=contracted_minutes
    )

# --- app.py ----------------------------------------------------------
from uuid import uuid4   # arriba del archivo

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
            cur.execute("""SELECT active,start_date,end_date,token
                           FROM users WHERE email=%s""", (email,))
            row = cur.fetchone()

        if not row:                       return "Usuario no registrado.", 403
        active, start, end, db_token = row
        if not active:                    return "Usuario inactivo.", 403
        if not (start <= today <= end):   return "Fuera de rango.", 403

        # --- decide qué token usar ----------------------------
        token = db_token                 # ✔  mantén el que ya existe
        # ▸  o descomenta para rotar cada login:
        # token = uuid4().hex
        # with conn.cursor() as cur:
        #     cur.execute("UPDATE users SET token=%s WHERE email=%s",
        #                 (token, email))
        # conn.commit()

    finally:
        conn.close()

    # ───────── cookies (¡dentro de la función!) ───────────────────
    cookie_opts = dict(
        max_age  = 60*60*24*30,   # 30 días
        path     = "/",
        samesite = "Lax",
        secure   = False          # pon True cuando tengas HTTPS
    )

    redirect_url = "http://localhost:3000/interactive-session"
    resp = make_response(redirect(redirect_url, code=302))

    for k, v in {
        "user_name":     name,
        "user_email":    email,
        "user_token":    token,
        "user_scenario": scenario,
    }.items():
        resp.set_cookie(k, v, **cookie_opts)

    print(f"[DEBUG] cookie token = {token}")
    return resp                     # 👈  ¡ahora sí se devuelve!

@app.route("/validate_user", methods=["POST"])
def validate_user_endpoint():
    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    token = data.get("token")
    today = date.today().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT active, start_date, end_date, token FROM users WHERE email=%s", (email,))
        row = c.fetchone()
        conn.close()

        if not row:
            return "Usuario no registrado.", 403
        if not row[0]:
            return "Usuario inactivo. Contacta a RH.", 403
        if not (row[1] <= today <= row[2]):
            return "Acceso fuera de rango permitido.", 403
        if row[3] != token:
            return "Token inválido.", 403
        return jsonify({"status": "ok", "message": "Usuario validado correctamente."}), 200

    except Exception as e:
        print(f"ERROR: validate_user failed: {e}")
        if conn:
            conn.rollback()
        return f"Error interno al validar usuario: {str(e)}", 500
    finally:
        if conn:
            conn.close()

# ─────────────────────────────────────────────────────────
#  DASHBOARD DATA  –  Devuelve las sesiones del usuario
# ─────────────────────────────────────────────────────────
from flask import jsonify
from flask_cors import cross_origin
import psycopg2.extras

@app.route("/dashboard_data", methods=["POST"])
@cross_origin()
def dashboard_data():
    """
    Espera JSON con:  { "name": "...", "email": "..." , "token": "..." }
    Devuelve lista de sesiones grabadas para el usuario autenticado.
    """
    conn = None
    try:
        data = request.get_json(force=True)
        name   = data.get("name")
        email  = data.get("email")
        token  = data.get("token")

        # 1) Validación muy básica del usuario
        if not check_user_token(email, token):
            return jsonify({"error": "token inválido"}), 401

        # 2) Consulta de sesiones y cálculo de tiempo en la MISMA transacción/cursor
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # First query: Fetch sessions
            cur.execute(
                """
                SELECT  id,
                        scenario,
                        timestamp as created_at,         -- Map 'timestamp' from 'interactions' to 'created_at'
                        duration_seconds as duration,    -- Map 'duration_seconds' to 'duration'
                        message as user_transcript,      -- Map 'message' (user input) to 'user_transcript'
                        response as avatar_transcript,   -- Map 'response' (Leo's response) to 'avatar_transcript'
                        evaluation as coach_advice,      -- Map 'evaluation' to 'coach_advice'
                        visual_feedback,                 -- Direct map
                        audio_path as video_s3           -- Map 'audio_path' (S3 key) to 'video_s3'
                FROM    interactions                     -- Change table name from 'sessions' to 'interactions'
                WHERE   email = %s
                ORDER BY timestamp DESC
                LIMIT   50;
                """,
                (email,),
            )
            sessions = cur.fetchall()

            # Second query: Calculate total used seconds (using the SAME open cursor)
            # IMPORTANT FIX: Handle fetchone() possibly returning None
            cur.execute(
                 """
                SELECT COALESCE(SUM(duration_seconds), 0) AS total_seconds_used
                FROM interactions
                WHERE email = %s;
                """,
                (email,)
            )
            result = cur.fetchone() # Fetch the result
            total_used_seconds = result['total_seconds_used'] if result is not None else 0

        return jsonify({"sessions": sessions, "used_seconds": total_used_seconds}), 200

    except Exception as e:
        app.logger.exception("dashboard_data error")
        return jsonify({"error": str(e)}), 500

    finally:
        if conn:
            conn.close()

@app.route("/video/<path:filename>")
def serve_video(filename):
    s3_video_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
    print(f"[SERVE VIDEO] Redirigiendo a S3: {s3_video_url}")
    return redirect(s3_video_url, code=302)

@app.route('/upload_video', methods=['POST'])
def upload_video():
    video_file = request.files.get('video')
    name = request.form.get('name')
    email = request.form.get('email')

    if not video_file or not name or not email:
        return jsonify({'status': 'error', 'message': 'Faltan datos (video, nombre o correo).'}), 400

    filename = secure_filename(f"{email}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.webm")
    local_path = os.path.join(TEMP_PROCESSING_FOLDER, filename)
    os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)
    video_file.save(local_path)

    try:
        s3_key = filename
        s3_url = upload_file_to_s3(local_path, AWS_S3_BUCKET_NAME, s3_key)
        if not s3_url:
            raise Exception("No se pudo subir el archivo a S3.")

        print(f"[S3] Subido a: {s3_url}")

        return jsonify({'status': 'ok', 'video_url': s3_url, 's3_object_key': s3_key})
    except Exception as e:
        print(f"[ERROR] upload_video: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

# ------------------------------------------------------------------------
# UTIL – normaliza la transcripción antes de guardarla
# ------------------------------------------------------------------------
def _as_json_list(txt: Union[str, list]) -> str:
    """
    Asegura que lo que se guarde en la BD sea siempre un JSON-list
    aún si el front-end manda un string plano.
    """
    if isinstance(txt, list):
        return json.dumps(txt)
    if isinstance(txt, str):
        # divide en líneas si viene todo junto
        return json.dumps([l for l in txt.splitlines() if l.strip()])
    return json.dumps([])

# ------------------------------------------------------------------------
# /log_full_session  – recibe video-key + transcripciones y guarda todo
# ------------------------------------------------------------------------
@app.route("/log_full_session", methods=["POST"])
def log_full_session():
    data = request.get_json() or {}

    # 0) Campos mínimos que esperamos
    name             = data.get("name")
    email            = data.get("email")
    scenario         = data.get("scenario")
    duration         = int(data.get("duration", 0))
    video_key        = data.get("video_object_key")      # <-- puede ser None
    user_raw         = data.get("conversation", "")
    avatar_raw       = data.get("avatar_transcript", "")

    # 1) Normalizamos SIEMPRE a JSON-list para evitar errores de parseo después
    user_json   = _as_json_list(user_raw)
    avatar_json = _as_json_list(avatar_raw)

    # 2) Resúmenes / tips  ——  (por ahora placeholders vacíos → generarás después)
    public_summary       = data.get("evaluation",       "")  # IA externa
    internal_summary_db  = data.get("evaluation_rh",    "")  # RH interna
    tip_text             = data.get("tip",              "")
    posture_feedback     = data.get("visual_feedback",  "")

    # 3) Timestamp estándar ISO-UTC
    timestamp_iso = datetime.utcnow().isoformat()

    # 4) Montamos URL S3 completa *solo si* tenemos key
    video_url = (
        f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{video_key}"
        if video_key else None
    )

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interactions
                       (name, email, scenario,
                        message, response,
                        audio_path,            -- guardamos la key (no la URL)
                        timestamp,
                        evaluation, evaluation_rh,
                        duration_seconds,
                        tip, visual_feedback)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (
                    name, email, scenario,
                    user_json,
                    avatar_json,
                    video_key,            # se servirá vía /video/<key>
                    timestamp_iso,
                    public_summary,
                    internal_summary_db,
                    duration,
                    tip_text,
                    posture_feedback,
                ),
            )
            session_id = cur.fetchone()[0]
        conn.commit()
        print(f"[DB] Sesión #{session_id} registrada correctamente.")
        return jsonify(
            {
                "status": "success",
                "session_id": session_id,
                "video_url": video_url,  # por si el front la necesita
                "message": "Sesión registrada.",
            }
        ), 200

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"[ERROR] log_full_session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        if conn:
            conn.close()

@app.route("/healthz")
def health_check():
    return "OK", 200

if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True)