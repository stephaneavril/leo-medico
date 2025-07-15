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
    print("ERROR: AWS_S3_BUCKET_NAME is not set in .env") # This is what we expect to debug

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
                avatar_transcript TEXT
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
    # For 'HolaHola' specifically, you might want to specifically replace it.
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
                # Original DB row structure (from SELECT query):
                # (id, name, email, scenario, message, response, audio_path, timestamp, evaluation, evaluation_rh, tip, visual_feedback)
                # Indices:
                # 0: id
                # 1: name
                # 2: email
                # 3: scenario
                # 4: message (user_transcript)
                # 5: response (avatar_transcript)
                # 6: audio_path (video_s3)
                # 7: timestamp
                # 8: evaluation (public_summary)
                # 9: evaluation_rh (raw JSON string)
                # 10: tip
                # 11: visual_feedback
                # 12: visible_to_user

                # Safely parse JSON fields
                user_dialogue_raw = json.loads(row[4]) if row[4] else []
                avatar_dialogue_raw = json.loads(row[5]) if row[5] else []
                if not isinstance(user_dialogue_raw, list):
                    # If it's a single string (not a list of segments), wrap it as a single segment
                    user_dialogue_raw = [str(user_dialogue_raw)]
                if not isinstance(avatar_dialogue_raw, list):
                    # If it's a single string (not a list of segments), wrap it as a single segment
                    avatar_dialogue_raw = [str(avatar_dialogue_raw)]

                # Clean each individual segment. Ensure string conversion before strip/clean.
                cleaned_user_segments = [clean_display_text(str(s).strip()) for s in user_dialogue_raw if str(s).strip()]
                cleaned_avatar_segments = [clean_display_text(str(s).strip()) for s in avatar_dialogue_raw if str(s).strip()]

                # Clean name, email, and scenario for display. Use correct DB indices and ensure string conversion.
                cleaned_name = clean_display_text(str(row[1])) if row[1] else "" # DB row[1] is name
                cleaned_email = clean_display_text(str(row[2])) if row[2] else "" # DB row[2] is email
                cleaned_scenario = clean_display_text(str(row[3])) if row[3] else "" # DB row[3] is scenario

                # Parse RH evaluation
                try:
                    parsed_rh_evaluation = json.loads(row[9])
                    if not parsed_rh_evaluation:
                      parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}
                except (json.JSONDecodeError, TypeError):
                    parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}


                    # --- NUEVO ---
                if row[6]:                                   # row[6] = s3_key
                    video_url_for_template = f"/video/{row[6]}"
                else:
                    video_url_for_template = None
# --------------
                # Construct the row for the template with consistent indexing
                # This list's indices should correspond to what admin.html now expects.
                current_processed_row = [
                    row[0], # 0: ID (for delete button form action)
                    cleaned_name, # 1: Name (for h3 display)
                    cleaned_email, # 2: Email (for h3 display)
                    cleaned_scenario, # 3: Scenario (for scenario display)
                    cleaned_user_segments, # 4: User dialogue (list of segments)
                    cleaned_avatar_segments, # 5: Avatar dialogue (list of segments)
                    video_url_for_template, # 6: Video URL (audio_path)
                    row[7], # 7: Timestamp
                    row[8], # 8: Public Summary (evaluation)
                    parsed_rh_evaluation, # 9: RH evaluation (full dict for detailed analysis)
                    row[10], # 10: Tip
                    row[11], # 11: Visual feedback
                    row[12] # 12: visible_to_user
                ]

                # Filling in default messages if certain fields are empty
                if not current_processed_row[8]: # Public summary (index 8)
                    current_processed_row[8] = "Análisis IA pendiente."
                if not current_processed_row[10]: # Tip (index 10)
                    current_processed_row[10] = "Consejo pendiente."
                if not current_processed_row[11]: # Visual Feedback (index 11)
                    current_processed_row[11] = "Análisis visual pendiente."

                processed_data.append(current_processed_row)

            except Exception as e:
                print(f"Error processing row from database: {e}. Raw row: {row}")
                # Directly construct the placeholder list for error cases
                processed_data.append([
                    row[0] if len(row) > 0 else "N/A", # 0: ID (if available)
                    "Error", # 1: Name
                    "Error", # 2: Email
                    "Error al cargar", # 3: Scenario
                    ["Error al cargar transcripción del participante."], # 4: User segments
                    ["Error al cargar transcripción del avatar."], # 5: Avatar segments
                    None, # 6: Video URL
                    "N/A", # 7: Timestamp
                    f"Error de procesamiento: {str(e)}", # 8: Public Summary
                    {"status": f"Error al cargar análisis de RH: {str(e)}"}, # 9: RH Evaluation (dict)
                    "Error al cargar consejo.", # 10: Tip
                    "Error al cargar feedback visual.", # 11: Visual Feedback
                    "N/A"   # 12: visible_to_user placeholder
                ])

        c.execute("SELECT id, name, email, start_date, end_date, active, token FROM users")
        users = c.fetchall()

        # Assuming the 'usage_summaries' query will still return correct data (from dashboard_data example).
        # If your `get_db_connection()` doesn't return a RealDictCursor by default for all queries,
        # ensure this part is adapted to use tuple indexing if needed.
        # However, the provided dashboard_data function *does* use RealDictCursor, so for consistency:
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
            # Accessing by key as RealDictCursor is used for this query
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

    # 3. Crear JWT válido 2 min
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

# ─────────────────────────────────────────────────────────
#  DASHBOARD DATA  –  Devuelve las sesiones del usuario
# ─────────────────────────────────────────────────────────
from flask import jsonify
from flask_cors import cross_origin
import psycopg2.extras
import json                           # <──  te hace falta para json.loads

# Define SENTINELS aquí también si tu app.py usa esta lógica en otro lugar,
# o asegúrate de que esté definida donde la uses.
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
        # 1) Leer el header Authorization
        auth = request.headers.get("Authorization", "")
        print(f"[🔐 DEBUG JWT] Authorization header completo: {auth!r}")

        # 2) Extraer token
        token = None
        if auth.startswith("Bearer "):
            token = auth.split("Bearer ", 1)[1]
        print(f"[🔐 DEBUG JWT] Token extraído: {token!r}")

        # 3) Si falta token
        if not token:
            print("[🔐 DEBUG JWT ERROR] No se encontró token en la cabecera")
            return jsonify(error="token faltante"), 401

        # 4) Intentar decodificarlo
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
            print(f"[🔐 DEBUG JWT] jwt.decode OK → payload: {payload}")
            request.jwt = payload
        except Exception as e:
            print(f"[🔐 DEBUG JWT ERROR] jwt.decode falló: {e}")
            return jsonify(error="token inválido o usuario no autorizado"), 401

        # 5) Si todo bien, seguimos
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

        # 1. Traemos las sesiones de la BD
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
            processed = dict(row)           # ← copia mutable

            # ── 1. Transcripciones a texto plano ─────────────────
            for field in ("user_transcript", "avatar_transcript"):
                raw = processed.get(field, "[]")
                try:
                    processed[field] = "\n".join(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    processed[field] = raw

            # ── 2. URL firmada del video (si aplica) ────────────
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

            # ── 3. Mostrar/ocultar análisis según visible_to_user ─
            if processed.get("visible_to_user"):
                processed["coach_advice"]  = processed.get("coach_advice", "")
                processed["rh_evaluation"] = processed.get("rh_comment", "")
            else:
                processed["coach_advice"]  = ""
                processed["rh_evaluation"] = ""

            sessions_to_send.append(processed)   # ¡solo una vez!

        # 4. Minutos usados
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(duration_seconds),0) FROM interactions WHERE email=%s",
                (email,)
            )
            total_used_seconds = cur.fetchone()[0]

        # 5. Enviamos respuesta
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
    # ... tu lógica actual es correcta aquí, ya que usa el token del decorador
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
    video_key        = data.get("video_object_key") or data.get("s3_object_key")
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
        

        # ───────── LANZA LA TAREA CELERY ─────────
        from celery_worker import process_session_transcript
        
      # ─── 2. construye el payload ───
        task_data = {
            "session_id":      session_id,
            "duration":        duration,          # opcional, para métricas
            "user_transcript": user_json          # ⬅️  solo el texto del usuario
            # (si quisieras seguir guardando la key del video –p. ej. para reproducirlo
            # en el panel RH– ponla en la BD como ya haces, pero NO hace falta enviarla)
        }

        # ─── 3. lanza la tarea ───
        result = process_session_transcript.delay(task_data)

        app.logger.info("🚀  Sesión %s ENCOLADA (task_id=%s)", session_id, result.id)
        # ─────────────────────────────────────────
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
