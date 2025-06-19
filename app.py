import os
import psycopg2
from urllib.parse import urlparse
import json
import secrets
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, make_response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import openai
import boto3

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
    except Exception as e:
        print(f"[S3 ERROR] Falló la subida a S3: {e}")
        return None

# --- ALL FLASK ROUTES ---
@app.route("/", methods=["GET"])
def index():
    # This route serves the initial login page
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
                user_dialogue_parsed = json.loads(row[4]) if row[4] else []
                full_user_text = "\n".join(user_dialogue_parsed) if isinstance(user_dialogue_parsed, list) else str(user_dialogue_parsed)
            except (json.JSONDecodeError, TypeError):
                full_user_text = "Error al parsear transcripción del participante."

            try:
                avatar_dialogue_parsed = json.loads(row[5]) if row[5] else []
                full_avatar_text = "\n".join(avatar_dialogue_parsed) if isinstance(avatar_dialogue_parsed, list) else str(avatar_dialogue_parsed)
            except (json.JSONDecodeError, TypeError):
                full_avatar_text = "Error al parsear transcripción del avatar."

            processed_row = list(row)
            processed_row[4] = full_user_text

            if not processed_row[8]: # evaluation
                 processed_row[8] = "No hay análisis disponible."
            if not processed_row[9] or processed_row[9] == "null": # evaluation_rh
                 processed_row[9] = {"status": "No hay análisis de RH disponible."}
            else:
                 try:
                     processed_row[9] = json.loads(processed_row[9])
                 except (json.JSONDecodeError, TypeError):
                     processed_row[9] = {"status": "Error al cargar análisis de RH."}

            if not processed_row[10]: # tip
                 processed_row[10] = "No hay consejo disponible."
            if not processed_row[11]: # visual_feedback
                 processed_row[11] = "No hay feedback visual disponible."


            processed_data.append(processed_row)

        c.execute("SELECT id, name, email, start_date, end_date, active, token FROM users")
        users = c.fetchall()

        c.execute("""
            SELECT u.name, u.email, COALESCE(SUM(i.duration_seconds), 0) as used_secs
            FROM users u
            LEFT JOIN interactions i ON u.email = i.email
            GROUP BY u.name, u.email
        """)
        usage_rows = c.fetchall()

        usage_summaries = []
        total_minutes_all_users = 0
        for name_u, email_u, secs in usage_rows:
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

@app.route("/start-session", methods=["POST"])
def start_session():
    name = request.form.get("name")
    email = request.form.get("email")
    token = request.form.get("token")
    scenario = request.form.get("scenario")
    today = date.today().isoformat()

    if not name or not email or not token or not scenario:
        return "Error: Faltan datos en el formulario.", 400

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

        response = make_response(redirect(f"http://localhost:3000/interactive-session?name={name}&email={email}&scenario={scenario}&token={token}", code=302))
        
        response.set_cookie('user_name', name, max_age=3600*24*30)
        response.set_cookie('user_email', email, max_age=3600*24*30)
        response.set_cookie('user_token', token, max_age=3600*24*30)
        response.set_cookie('user_scenario', scenario, max_age=3600*24*30)

        return response

    except Exception as e:
        if conn:
            conn.rollback()
        return f"Error interno al iniciar sesión: {str(e)}", 500
    finally:
        if conn:
            conn.close()

@app.route("/validate_user", methods=["POST"])
def validate_user():
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
        if conn:
            conn.rollback()
        return f"Error interno al validar usuario: {str(e)}", 500
    finally:
        if conn:
            conn.close()

@app.route("/dashboard_data", methods=["POST"])
def dashboard_data():
    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    token = data.get("token")
    today = date.today().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute("SELECT start_date, end_date, active, token FROM users WHERE email = %s", (email,))
        user_row = c.fetchone()

        if not user_row or not user_row[2] or not (user_row[0] <= today <= user_row[1]) or user_row[3] != token:
            return "Unauthorized access to dashboard data.", 401

        now = datetime.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

        c.execute("SELECT SUM(duration_seconds) FROM interactions WHERE email = %s AND timestamp >= %s", (email, start_of_month))
        used_seconds = c.fetchone()[0] or 0

        c.execute("SELECT scenario, message, response, audio_path, timestamp, tip, visual_feedback FROM interactions WHERE name=%s AND email=%s ORDER BY timestamp DESC", (name, email))
        records_raw = c.fetchall()

        records_processed = []
        for r in records_raw:
            try:
                user_dialogue_parsed = json.loads(r[1]) if r[1] else []
                display_user_message = "\n".join(user_dialogue_parsed) if isinstance(user_dialogue_parsed, list) else str(user_dialogue_parsed)
            except (json.JSONDecodeError, TypeError):
                display_user_message = "Error al cargar transcripción del participante."

            evaluation_display = r[2] if r[2] and r[2] != "null" else "Transcripción del Avatar no disponible."
            public_summary_display = r[3] if r[3] else "Video no disponible."
            tip_display = r[5] if r[5] else "No hay consejo."
            visual_feedback_display = r[6] if r[6] else "No hay feedback visual."

            records_processed.append({
                "scenario": r[0],
                "message": display_user_message,
                "evaluation": public_summary_display,
                "audio_path": r[3],
                "timestamp": r[4],
                "tip": tip_display,
                "visual_feedback": visual_feedback_display
            })

        conn.close()
        return jsonify({"records": records_processed, "used_seconds": used_seconds}), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return f"Error interno al obtener datos del dashboard: {str(e)}", 500
    finally:
        if conn:
            conn.close()


@app.route("/video/<path:filename>")
def serve_video(filename):
    s3_video_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
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

        return jsonify({'status': 'ok', 'video_url': s3_url, 's3_object_key': s3_key})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


@app.route("/log_full_session", methods=["POST"])
def log_full_session():
    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    scenario = data.get("scenario")
    user_dialogue_frontend = data.get("conversation", [])
    avatar_dialogue_frontend = data.get("avatar_transcript", [])
    duration = int(data.get("duration", 0))
    video_object_key = data.get("video_object_key")

    timestamp = datetime.now().isoformat()

    public_summary = "No hay análisis disponible."
    internal_summary_db = json.dumps({"status": "No hay análisis de RH disponible."})
    tip_text = "No hay consejo disponible."
    posture_feedback = "No hay feedback visual disponible."
    final_video_s3_url = video_object_key

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO interactions (name, email, scenario, message, response, audio_path, timestamp, evaluation, evaluation_rh, duration_seconds, tip, visual_feedback)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                name,
                email,
                scenario,
                json.dumps(user_dialogue_frontend),
                json.dumps(avatar_dialogue_frontend),
                final_video_s3_url,
                timestamp,
                public_summary,
                internal_summary_db,
                duration,
                tip_text,
                posture_feedback
            ))
            session_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Error interno al guardar la sesión en la base de datos."
        }), 500

    print(f"[INFO] Session ID {session_id} for user {email} logged immediately to DB (No analysis).")

    return jsonify({
        "status": "success",
        "message": "Sesión registrada.",
        "session_id": session_id
    })

@app.route("/test_db")
def test_db_connection():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM interactions")
        count = c.fetchone()[0]
        c.execute("SELECT id, name, email, audio_path FROM interactions ORDER BY timestamp DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        return f"<h1>DB Test: {count} interactions</h1><p>First 5 rows: {rows}</p><p>DB_URL: {os.getenv('DATABASE_URL')}</p>", 200
    except Exception as e:
        return f"<h1>DB Test Error: {e}</h1><p>DB_URL: {os.getenv('DATABASE_URL')}</p>", 500

@app.route("/healthz")
def health_check():
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)