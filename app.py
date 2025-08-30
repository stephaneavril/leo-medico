import os
import psycopg2
from urllib.parse import urlparse
import json
import secrets
from datetime import datetime, date
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, make_response, send_file
)
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from typing import Union
import openai
import boto3
from botocore.exceptions import ClientError
import re
from flask import Flask, request, redirect, url_for, flash, render_template

# 1) Carga variables de entorno
load_dotenv(override=True)

# 2) Carpeta temporal para procesar videos
TEMP_PROCESSING_FOLDER = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

# 3) Define BASE_DIR para plantillas y estáticos
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 4) Instancia la app UNA SOLA VEZ, con template y static
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static'),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(16))

# 5) Configura CORS sólo para tu frontend de producción
CORS(
    app,
    resources={r"/*": {
        "origins": [
            os.getenv("FRONTEND_URL", "https://leo-api-ryzd.onrender.com")
        ]
    }},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=True,
)

print("🚀 Iniciando Leo Virtual Trainer (Modo Producción)…")

# Debugging print for all relevant env vars
print(f"DEBUG: OPENAI_API_KEY (first 5 chars): {os.getenv('OPENAI_API_KEY', 'N/A')[:5]}...")
print(f"DEBUG: AWS_ACCESS_KEY_ID: {os.getenv('AWS_ACCESS_KEY_ID', 'N/A')}")
print(f"DEBUG: AWS_SECRET_ACCESS_KEY (last 5 chars): ...{os.getenv('AWS_SECRET_ACCESS_KEY', 'N/A')[-5:]}")

# Retrieve AWS environment variables. Ensure no quotes or comments are in the .env file.
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "").split("#",1)[0].strip().strip("'\"")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-west-2")

# >>>>> THIS IS THE CRITICAL DEBUGGING LINE <<<<<
# It shows exactly what Flask is loading for the bucket name.
print(f"DEBUG: Flask sees AWS_S3_BUCKET_NAME as: '{AWS_S3_BUCKET_NAME}'")
# >>>>> THIS IS THE CRITICAL DEBUGGING LINE <<<<<

# Validate essential AWS variables are not None after loading
if not AWS_ACCESS_KEY_ID:
    print("ERROR: AWS_ACCESS_KEY_ID is not set in .env")
if not AWS_SECRET_ACCESS_KEY:
    print("ERROR: AWS_SECRET_ACCESS_KEY is not set in .env")
if not AWS_S3_BUCKET_NAME:
    print("ERROR: AWS_S3_BUCKET_NAME is not set in .env")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME
)

@app.route("/get_presigned_url/<key>")
def get_presigned_url(key):
    """
    Devuelve una URL firmada (1 h) para reproducir el vídeo privado
    """
    url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": AWS_S3_BUCKET_NAME,
            "Key": key
        },
        ExpiresIn=3600          # 1 hora
    )
    return jsonify({"url": url})

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
                visual_feedback TEXT,
                visible_to_user BOOLEAN DEFAULT FALSE,
                avatar_transcript TEXT,
                rh_comment TEXT
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
    """Asegura que todas las columnas opcionales existan en las tablas
       `interactions` y `users`.  Se ejecuta solo una vez al inicio."""
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        # -------- Tabla interactions --------
        c.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'interactions'
            AND column_name = 'rh_comment';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN rh_comment TEXT;")
            print("Added 'rh_comment' to interactions table.")

        c.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'interactions'
               AND column_name = 'tip';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN tip TEXT;")
            print("Added 'tip' to interactions table.")

        c.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'interactions'
               AND column_name = 'visual_feedback';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN visual_feedback TEXT;")
            print("Added 'visual_feedback' to interactions table.")

        c.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'interactions'
               AND column_name = 'visible_to_user';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN visible_to_user BOOLEAN DEFAULT FALSE;")
            print("Added 'visible_to_user' to interactions table.")

        # -------- Tabla users --------
        c.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'users'
               AND column_name = 'token';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN token TEXT UNIQUE;")
            print("Added 'token' to users table.")

        conn.commit()
        print("🛠️  Database schema patched (PostgreSQL).")

    except Exception as e:
        print(f"🔥 Error patching PostgreSQL database schema: {e}")
        if conn:
            conn.rollback()

    finally:
        if conn:
            conn.close()

# Ejecuta al iniciar la aplicación
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

from datetime import datetime, timedelta, date
import jwt, psycopg2.extras

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")   # ya lo usas en login
JWT_ALG    = "HS256"

def issue_jwt(payload: dict, days: int = 7) -> str:
    """Firma un JWT HS256 que caduca en <days> días."""
    payload = payload.copy()
    payload.update({
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=days)
    })
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

@app.post("/admin/users")
def create_user():
    """Crea usuario (token nuevo) o devuelve el existente."""
    data  = request.get_json(force=True)           # {name,email}
    name  = data.get("name","").strip()
    email = data.get("email","").strip().lower()
    if not name or not email:
        return "falta nombre o email", 400

    conn = get_db_connection()                     # tu helper ya existe
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, token FROM users WHERE email=%s", (email,))
            row = cur.fetchone()

            if row:                                # ya existe
                user_id, token = row["id"], row["token"]
            else:                                  # crear
                token = issue_jwt({"name": name, "email": email})
                cur.execute(
                    """
                    INSERT INTO users (name,email,start_date,end_date,active,token)
                    VALUES (%s,%s,%s,%s,1,%s)
                    RETURNING id
                    """,
                    (
                        name, email,
                        date.today(),                       # start_date
                        date.today() + timedelta(days=365), # end_date = 1 año
                        token
                    )
                )
                user_id = cur.fetchone()[0]
            conn.commit()

        return jsonify({
            "user_id":    user_id,
            "token":      token,
            "date_from":  date.today().isoformat(),
            "date_to":   (date.today()+timedelta(days=365)).isoformat()
        }), 201
    finally:
        conn.close()

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

# -----------------------------
# Limpieza de textos para UI
# -----------------------------
def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        return ""

    text = text.replace('\r\n', ' ').replace('\n', ' ').strip()

    text = text.replace('\\303\\251', 'é')
    text = text.replace('\\303\\241', 'á')
    text = text.replace('\\303\\255', 'í')
    text = text.replace('\\303\\263', 'ó')
    text = text.replace('\\303\\272', 'ú')
    text = text.replace('\\303\\261', 'ñ')
    text = text.replace('\\302\\277', '¿')
    text = text.replace('\\302\\241', '¡')

    words = text.split(' ')
    cleaned_words_list = []
    last_word = None
    for word in words:
        if word != last_word:
            cleaned_words_list.append(word)
        last_word = word
    text = ' '.join(cleaned_words_list)

    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()

    return text

# -----------------------------------------------------------
# NUEVO: Resumen de Calificación por Usuario (server-side)
# -----------------------------------------------------------
from collections import defaultdict

def _parse_frac(txt: str, default=(0, 1)) -> tuple[int, int]:
    """
    Convierte '3/5' -> (3,5). Devuelve default si falla.
    """
    try:
        a, b = str(txt).split("/")
        return int(a), max(1, int(b))
    except Exception:
        return default

def _safe_get(dic, path, default=None):
    """
    Acceso seguro tipo _safe_get(d, 'a.b.c', default)
    """
    try:
        cur = dic
        for key in path.split("."):
            cur = cur.get(key, {})
        return cur if cur != {} else default
    except Exception:
        return default

def build_performance_summaries(processed_data: list[list]) -> list[dict]:
    """
    processed_data: filas que arma admin_panel para el template:
      [0:id, 1:name, 2:email, 3:scenario, 4:user_msgs[], 5:avatar_msgs[],
       6:video_url, 7:timestamp, 8:public, 9:internal_dict, 10:tip, 11:visual, 12:visible_to_user]
    """
    by_user = defaultdict(lambda: {
        "name": "",
        "email": "",
        "sessions": 0,
        "dv_points": [],
        "steps_pct": [],
        "legacy_k": [],
        "red_flags": 0,
        "last_date": None
    })

    for row in processed_data:
        try:
            name  = row[1] or ""
            email = row[2] or ""
            internal = row[9] if isinstance(row[9], dict) else {}
            ts = row[7] or ""

            # Da Vinci points
            dv = internal.get("da_vinci_points") or internal.get("abbott_points") or {}
            dv_total = int(dv.get("total", 0))

            # Steps applied count "X/5"
            steps_str = _safe_get(internal, "da_vinci_step_flags.steps_applied_count", "0/5")
            steps_num, steps_den = _parse_frac(steps_str, (0, 5))
            steps_pct = (steps_num / max(1, steps_den)) * 100.0

            # Knowledge legacy "N/8"
            legacy_str = internal.get("knowledge_score_legacy", "0/8")
            legacy_num, legacy_den = _parse_frac(legacy_str, (0, 8))
            legacy_pct = (legacy_num / max(1, legacy_den)) * 100.0

            # Composite score: 50% dv_norm + 50% legacy_pct
            # dv_norm: normalizamos a 8 puntos como techo razonable
            dv_norm = min(dv_total, 8) / 8.0 * 100.0
            composite = 0.5 * dv_norm + 0.5 * legacy_pct

            # Red flags
            red = 1 if internal.get("disqualifying_phrases_detected") else 0

            bucket = by_user[email]
            bucket["name"]   = name
            bucket["email"]  = email
            bucket["sessions"] += 1
            bucket["dv_points"].append(dv_total)
            bucket["steps_pct"].append(steps_pct)
            bucket["legacy_k"].append(legacy_num)   # guardo el numerador para promedio 0–8
            bucket["red_flags"] += red
            # última fecha
            if ts:
                try:
                    # si viene ISO
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    dt = None
                if dt:
                    if not bucket["last_date"] or dt > bucket["last_date"]:
                        bucket["last_date"] = dt
        except Exception as e:
            print(f"[perf_summaries] fila omitida por error: {e}")

    summaries = []
    for email, agg in by_user.items():
        sessions = max(1, agg["sessions"])
        avg_dv = sum(agg["dv_points"]) / sessions
        avg_steps_pct = sum(agg["steps_pct"]) / sessions
        avg_legacy_num = sum(agg["legacy_k"]) / sessions        # 0–8
        # recomponemos el compuesto con el mismo criterio
        dv_norm = min(avg_dv, 8) / 8.0 * 100.0
        legacy_pct = (avg_legacy_num / 8.0) * 100.0
        avg_score = 0.5 * dv_norm + 0.5 * legacy_pct

        last_date_str = agg["last_date"].strftime("%Y-%m-%d %H:%M") if agg["last_date"] else "—"
        summaries.append({
            "name": agg["name"],
            "email": agg["email"],
            "sessions_published": sessions,
            "avg_score": round(avg_score, 1),
            "avg_dv_points": round(avg_dv, 1),
            "avg_steps_pct": round(avg_steps_pct, 0),
            "avg_legacy": round(avg_legacy_num, 1),
            "red_flags": int(agg["red_flags"]),
            "last_date": last_date_str
        })

    # Ordena por mejor avg_score desc
    summaries.sort(key=lambda x: x["avg_score"], reverse=True)
    return summaries

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

        c.execute("""
            SELECT id, name, email, scenario, message, response, audio_path,
                    timestamp, evaluation, evaluation_rh, tip, visual_feedback,
                    visible_to_user
            FROM interactions
            ORDER BY timestamp DESC
        """)
        raw_data = c.fetchall()

        processed_data = []
        for row in raw_data:
            try:
                user_dialogue_raw = json.loads(row[4]) if row[4] else []
                avatar_dialogue_raw = json.loads(row[5]) if row[5] else []
                if not isinstance(user_dialogue_raw, list):
                    user_dialogue_raw = [str(user_dialogue_raw)]
                if not isinstance(avatar_dialogue_raw, list):
                    avatar_dialogue_raw = [str(avatar_dialogue_raw)]

                cleaned_user_segments = [clean_display_text(str(s).strip()) for s in user_dialogue_raw if str(s).strip()]
                cleaned_avatar_segments = [clean_display_text(str(s).strip()) for s in avatar_dialogue_raw if str(s).strip()]

                cleaned_name = clean_display_text(str(row[1])) if row[1] else ""
                cleaned_email = clean_display_text(str(row[2])) if row[2] else ""
                cleaned_scenario = clean_display_text(str(row[3])) if row[3] else ""

                try:
                    parsed_rh_evaluation = json.loads(row[9])
                    if not parsed_rh_evaluation:
                        parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}
                except (json.JSONDecodeError, TypeError):
                    parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}

                if row[6]:
                    video_url_for_template = f"/video/{row[6]}"
                else:
                    video_url_for_template = None

                current_processed_row = [
                    row[0],                # 0: ID
                    cleaned_name,          # 1: Name
                    cleaned_email,         # 2: Email
                    cleaned_scenario,      # 3: Scenario
                    cleaned_user_segments, # 4: User dialogue (list)
                    cleaned_avatar_segments,#5: Avatar dialogue (list)
                    video_url_for_template,# 6: Video URL
                    row[7],                # 7: Timestamp
                    row[8],                # 8: Public Summary
                    parsed_rh_evaluation,  # 9: Internal JSON
                    row[10],               # 10: Tip
                    row[11],               # 11: Visual feedback
                    row[12]                # 12: visible_to_user
                ]

                if not current_processed_row[8]:
                    current_processed_row[8] = "Análisis IA pendiente."
                if not current_processed_row[10]:
                    current_processed_row[10] = "Consejo pendiente."
                if not current_processed_row[11]:
                    current_processed_row[11] = "Análisis visual pendiente."

                processed_data.append(current_processed_row)

            except Exception as e:
                print(f"Error processing row from database: {e}. Raw row: {row}")
                processed_data.append([
                    row[0] if len(row) > 0 else "N/A",
                    "Error",
                    "Error",
                    "Error al cargar",
                    ["Error al cargar transcripción del participante."],
                    ["Error al cargar transcripción del avatar."],
                    None,
                    "N/A",
                    f"Error de procesamiento: {str(e)}",
                    {"status": f"Error al cargar análisis de RH: {str(e)}"},
                    "Error al cargar consejo.",
                    "Error al cargar feedback visual.",
                    "N/A"
                ])

        c.execute("SELECT id, name, email, start_date, end_date, active, token FROM users")
        users = c.fetchall()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur_usage:
            cur_usage.execute("""
                SELECT u.name, u.email, COALESCE(SUM(i.duration_seconds), 0) AS total_seconds_used
                FROM users u
                LEFT JOIN interactions i ON u.email = i.email
                GROUP BY u.name, u.email
            """)
            usage_rows = cur_usage.fetchall()

        usage_summaries = []
        total_minutes_all_users = 0
        for row_data in usage_rows:
            name_u = row_data.get('name', "Unknown")
            email_u = row_data.get('email', "Unknown")
            secs = row_data.get('total_seconds_used', 0)

            mins = secs // 60
            total_minutes_all_users += mins
            summary = "Buen desempeño general" if mins >= 15 else "Actividad moderada" if mins >= 5 else "Poca actividad, se sugiere seguimiento"
            usage_summaries.append({
                "name": name_u,
                "email": email_u,
                "minutes": mins,
                "summary": summary
            })

        contracted_minutes = 1050

        # >>> NUEVO: construir resumen de calificación por usuario
        performance_summaries = build_performance_summaries(processed_data)

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
        contracted_minutes=contracted_minutes,
        performance_summaries=performance_summaries   # ⬅️ pasa el resumen al template
    )

# --- app.py ----------------------------------------------------------
from uuid import uuid4   # arriba del archivo
import jwt
import os
import datetime as dt_module

JWT_SECRET = os.environ["JWT_SECRET"]      # Añádelo en Render
JWT_ALG    = "HS256"
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://leo-api-ryzd.onrender.com")

@app.route("/start-session", methods=["POST"])
def start_session():
    # 1. Leer formulario
    name     = request.form.get("name")
    email    = request.form.get("email")
    scenario = request.form.get("scenario")
    if not all([name, email, scenario]):
        return "Faltan datos.", 400

    # 2. Validar usuario (tu misma lógica)
    today = date.today().isoformat()
    conn  = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT active, start_date, end_date
                FROM users
                WHERE email = %s
            """, (email,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return "Usuario no registrado.", 403
    active, start, end = row
    if not active or not (start <= today <= end):
        return "Sin vigencia.", 403

    # 3. Crear JWT válido 1 h
    payload = {
        "name":     name,
        "email":    email,
        "scenario": scenario,
        "iat": dt_module.datetime.utcnow(),
        "exp": dt_module.datetime.utcnow() + dt_module.timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

    # 4. Redirigir al Frontend
    url = f"{FRONTEND_URL}/dashboard?auth={token}"
    print("DEBUG_REDIRECT ->", url)
    return redirect(url, code=302)

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

# --- RE-EVALUAR UNA SESIÓN Y GENERAR internal.compact -----------------
from flask import redirect, request
import os, json, psycopg2
from urllib.parse import urlparse
from evaluator import evaluate_and_persist

def _db_conn():
    dsn = os.getenv("DATABASE_URL")
    p = urlparse(dsn)
    return psycopg2.connect(
        database=p.path[1:],
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        sslmode="require",
    )

# Acepta GET y POST para evitar 405 / “Internal Server Error” si alguien abre el link directo
@app.route("/admin/recompute/<int:session_id>", methods=["GET", "POST"])
def admin_recompute(session_id: int):
    user_t, leo_t = "", ""

    # 1) obtener textos de la sesión
    try:
        conn = _db_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_transcript, avatar_transcript
                FROM interactions
                WHERE id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            print(f"[recompute] sesión {session_id} no encontrada")
            return redirect("/admin")
        user_t, leo_t = row[0] or "", row[1] or ""
    except Exception as e:
        print(f"[recompute] error leyendo BD: {e}")
        return redirect("/admin")

    # 2) re-evaluar y persistir (genera/actualiza internal.compact)
    try:
        evaluate_and_persist(session_id, user_t, leo_t, video_path=None)
        print(f"[recompute] sesión {session_id} evaluada OK")
    except Exception as e:
        # No interrumpimos el flujo; volvemos al panel
        print(f"[recompute] error evaluando sesión {session_id}: {e}")

    # 3) volver al panel
    return redirect("/admin")

# ─────────────────────────────────────────────────────────
#  DASHBOARD DATA  –  Devuelve las sesiones del usuario
# ─────────────────────────────────────────────────────────
from flask import jsonify
from flask_cors import cross_origin
import psycopg2.extras
import json

SENTINELS = [
  'Video_Not_Available_Error',
  'Video_Processing_Failed',
  'Video_Missing_Error',
]

from functools import wraps
from flask import request, jsonify

def jwt_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        print(f"[🔐 DEBUG JWT] Authorization header completo: {auth!r}")

        token = None
        if auth.startswith("Bearer "):
            token = auth.split("Bearer ", 1)[1]
        print(f"[🔐 DEBUG JWT] Token extraído: {token!r}")

        if not token:
            print("[🔐 DEBUG JWT ERROR] No se encontró token en la cabecera")
            return jsonify(error="token faltante"), 401

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
            print(f"[🔐 DEBUG JWT] jwt.decode OK → payload: {payload}")
            request.jwt = payload
        except Exception as e:
            print(f"[🔐 DEBUG JWT ERROR] jwt.decode falló: {e}")
            return jsonify(error="token inválido o usuario no autorizado"), 401

        return f(*args, **kwargs)
    return _wrap

@app.get("/dashboard_data")
@cross_origin()
@jwt_required
def dashboard_data():
    email = request.jwt["email"]
    conn  = None
    try:
        print(f"[DEBUG_DASHBOARD] JWT ok para {email}")

        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, scenario, timestamp AS created_at,
                       duration_seconds AS duration,
                       message  AS user_transcript,
                       response AS avatar_transcript,
                       evaluation       AS coach_advice,
                       rh_comment,
                       visual_feedback,
                       audio_path       AS video_s3,
                       tip,
                       evaluation_rh    AS rh_evaluation,
                       visible_to_user
                FROM   interactions
                WHERE  email = %s
                ORDER BY timestamp DESC
                LIMIT  50;
                """,
                (email,)
            )
            raw_rows = cur.fetchall()

        sessions_to_send = []
        for row in raw_rows:
            processed = dict(row)

            for field in ("user_transcript", "avatar_transcript"):
                raw = processed.get(field, "[]")
                try:
                    processed[field] = "\n".join(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    processed[field] = raw

            s3_key = processed.get("video_s3")
            if s3_key and s3_key not in SENTINELS:
                try:
                    processed["video_s3"] = s3_client.generate_presigned_url(
                        ClientMethod='get_object',
                        Params={'Bucket': AWS_S3_BUCKET_NAME, 'Key': s3_key},
                        ExpiresIn=3600
                    )
                except ClientError:
                    processed["video_s3"] = None
            else:
                processed["video_s3"] = None

            if processed.get("visible_to_user"):
                processed["coach_advice"]  = processed.get("coach_advice", "")
                processed["rh_evaluation"] = processed.get("rh_comment", "")
            else:
                processed["coach_advice"]  = ""
                processed["rh_evaluation"] = ""

            sessions_to_send.append(processed)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(duration_seconds),0) FROM interactions WHERE email=%s",
                (email,)
            )
            total_used_seconds = cur.fetchone()[0]

        auth_header = request.headers.get("Authorization", "")
        user_token  = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header

        return jsonify(
            name         = request.jwt["name"],
            email        = email,
            user_token   = user_token,
            sessions     = sessions_to_send,
            used_seconds = total_used_seconds
        ), 200

    except Exception as e:
        app.logger.exception("dashboard_data – error")
        return jsonify(error=f"Error interno: {e}"), 500
    finally:
        if conn:
            conn.close()

@app.route("/video/<path:filename>")
def serve_video(filename):
    presigned = s3_client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': AWS_S3_BUCKET_NAME, 'Key': filename},
        ExpiresIn=3600
    )
    print(f"[SERVE VIDEO] -> {presigned}")
    return redirect(presigned, code=302)

@app.route('/upload_video', methods=['POST'])
@jwt_required
def upload_video():
    email = request.jwt["email"]
    video_file = request.files.get('video')

    if not video_file:
        return jsonify({'status': 'error', 'message': 'Falta el archivo de video.'}), 400

    filename = secure_filename(f"{email.replace('@', '_at_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.webm")
    local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        video_file.save(local_path)
        s3_key = filename
        s3_url = upload_file_to_s3(local_path, AWS_S3_BUCKET_NAME, s3_key)
        if not s3_url:
            raise Exception("Fallo en la subida a S3.")
        return jsonify({'status': 'ok', 's3_object_key': s3_key})
    except Exception as e:
        app.logger.error(f"Error en upload_video: {e}")
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
        return json.dumps([l for l in txt.splitlines() if l.strip()])
    return json.dumps([])

# ------------------------------------------------------------------------
# /log_full_session  – recibe video-key + transcripciones y guarda todo
# ------------------------------------------------------------------------
@app.route("/log_full_session", methods=["POST"])
def log_full_session():
    data = request.get_json() or {}

    name             = data.get("name")
    email            = data.get("email")
    scenario         = data.get("scenario")
    duration         = int(data.get("duration", 0))
    video_key        = data.get("video_object_key") or data.get("s3_object_key")
    user_raw         = data.get("conversation", "")
    avatar_raw       = data.get("avatar_transcript", "")

    user_json   = _as_json_list(user_raw)
    avatar_json = _as_json_list(avatar_raw)

    public_summary       = data.get("evaluation",       "")
    internal_summary_db  = data.get("evaluation_rh",    "")
    tip_text             = data.get("tip",              "")
    posture_feedback     = data.get("visual_feedback",  "")

    timestamp_iso = datetime.utcnow().isoformat()
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
                        audio_path,
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
                    video_key,
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
        
        # ───────── LANZA LA TAREA CELERY ─────────
        from celery_worker import process_session_transcript
        
        task_data = {
            "session_id":      session_id,
            "duration":        duration,
            "video_object_key": video_key,
            "user_transcript": user_json
        }

        result = process_session_transcript.delay(task_data)
        app.logger.info("🚀  Sesión %s ENCOLADA (task_id=%s)", session_id, result.id)

        return jsonify(
            {
                "status": "success",
                "session_id": session_id,
                "video_url": video_url,
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

# --- helper corto ---
def get_db():
    p = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=p.path.lstrip("/"),
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        sslmode="require",
    )

# ---------- NUEVO ENDPOINT ----------
@app.post("/admin/publish_eval/<int:sid>")
def publish_eval(sid: int):
    comment = (request.form.get("comment_rh") or "").strip()
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE interactions SET rh_comment = %s, visible_to_user = TRUE WHERE id = %s;",
            (comment, sid)
        )
    flash(f"Sesión {sid} publicada con comentario RH ✅", "success")
    return redirect(url_for("admin_panel"))

@app.post("/admin/publish_ai/<int:sid>")
def publish_ai(sid: int):
    """Marca la sesión como visible sin añadir comentario RH."""
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE interactions SET rh_comment = NULL, visible_to_user = TRUE WHERE id = %s;",
            (sid,)
        )
    flash(f"Sesión {sid} publicada con análisis IA ✅", "success")
    return redirect(url_for("admin_panel"))

@app.route("/healthz")
def health_check():
    return "OK", 200
