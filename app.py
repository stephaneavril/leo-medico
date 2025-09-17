import os
import re
import json
import secrets
from datetime import datetime, timedelta, date
from urllib.parse import urlparse
from typing import Union
from collections import defaultdict
from functools import wraps

import psycopg2
import psycopg2.extras
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash
)
from flask_cors import CORS
import jwt
from flask_cors import cross_origin

# 1) Carga variables de entorno
load_dotenv(override=True)

# 2) Carpeta temporal para procesar videos
TEMP_PROCESSING_FOLDER = os.getenv("TEMP_PROCESSING_FOLDER", "/tmp/leo_trainer_processing")
os.makedirs(TEMP_PROCESSING_FOLDER, exist_ok=True)

# 3) Define BASE_DIR para plantillas y estáticos
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 4) Instancia la app
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static'),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(16))
# cookies más seguras
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# 5) CORS sólo para tu frontend
CORS(
    app,
    resources={r"/*": {"origins": [os.getenv("FRONTEND_URL", "https://leo-api-ryzd.onrender.com")]}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=True,
)

print("🚀 Iniciando Leo Virtual Trainer (Modo Producción)…")

# ---------------- Constantes / JWT / Auth ----------------
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG    = "HS256"
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://leo-api-ryzd.onrender.com")

SENTINELS = [
    'Video_Not_Available_Error',
    'Video_Processing_Failed',
    'Video_Missing_Error'
]

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
        except Exception:
            return jsonify(error="token inválido o usuario no autorizado"), 401
        return f(*args, **kwargs)
    return _wrap

# ---------------- AWS ----------------
print(f"DEBUG: OPENAI_API_KEY (first 5 chars): {os.getenv('OPENAI_API_KEY', 'N/A')[:5]}...")
print(f"DEBUG: AWS_ACCESS_KEY_ID: {os.getenv('AWS_ACCESS_KEY_ID', 'N/A')}")
print(f"DEBUG: AWS_SECRET_ACCESS_KEY (last 5 chars): ...{os.getenv('AWS_SECRET_ACCESS_KEY', 'N/A')[-5:]}")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "").split("#",1)[0].strip().strip("'\"")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-west-2")
print(f"DEBUG: Flask sees AWS_S3_BUCKET_NAME as: '{AWS_S3_BUCKET_NAME}'")
if not AWS_ACCESS_KEY_ID: print("ERROR: AWS_ACCESS_KEY_ID is not set in .env")
if not AWS_SECRET_ACCESS_KEY: print("ERROR: AWS_SECRET_ACCESS_KEY is not set in .env")
if not AWS_S3_BUCKET_NAME: print("ERROR: AWS_S3_BUCKET_NAME is not set in .env")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION_NAME
)

@app.route("/get_presigned_url/<path:key>")
@jwt_required  # o valida session["admin"] si lo prefieres
def get_presigned_url(key):
    url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": key},
        ExpiresIn=3600
    )
    return jsonify({"url": url})

# ---------------- DB helpers ----------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

def get_db_connection():
    parsed_url = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=parsed_url.path[1:],
        user=parsed_url.username,
        password=parsed_url.password,
        host=parsed_url.hostname,
        port=parsed_url.port,
        sslmode='require'
    )

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

def _guess_video_mime(key: str) -> str:
    k = (key or "").lower()
    if k.endswith(".webm"): return "video/webm"
    if k.endswith(".mp4"):  return "video/mp4"
    if k.endswith(".mov"):  return "video/quicktime"
    return "video/mp4"

# ---------------- DB bootstrap ----------------
def init_db():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
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
        """)
        c.execute("""
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
        print("📃 Database initialized or already exists (PostgreSQL).")
    except Exception as e:
        print(f"🔥 Error initializing PostgreSQL database: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def patch_db_schema():
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'interactions' AND column_name = 'rh_comment';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN rh_comment TEXT;")
            print("Added 'rh_comment' to interactions table.")

        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'interactions' AND column_name = 'tip';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN tip TEXT;")
            print("Added 'tip' to interactions table.")

        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'interactions' AND column_name = 'visual_feedback';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN visual_feedback TEXT;")
            print("Added 'visual_feedback' to interactions table.")

        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'interactions' AND column_name = 'visible_to_user';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE interactions ADD COLUMN visible_to_user BOOLEAN DEFAULT FALSE;")
            print("Added 'visible_to_user' to interactions table.")

        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'token';
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN token TEXT UNIQUE;")
            print("Added 'token' to users table.")

        conn.commit()
        print("🛠️  Database schema patched (PostgreSQL).")
    except Exception as e:
        print(f"🔥 Error patching PostgreSQL database schema: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def ensure_db_indexes():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_interactions_email ON interactions(email);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_interactions_email_ts ON interactions(email, timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
        conn.commit()
    finally:
        conn.close()

def ensure_comments_table():
    sql_create = """
    CREATE TABLE IF NOT EXISTS interaction_comments (
      id SERIAL PRIMARY KEY,
      interaction_id INTEGER NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
      author VARCHAR(120) DEFAULT 'Capacitación',
      body TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """
    sql_seed = """
    INSERT INTO interaction_comments (interaction_id, body)
    SELECT id, rh_comment FROM interactions
    WHERE rh_comment IS NOT NULL AND rh_comment <> ''
      AND NOT EXISTS (
        SELECT 1 FROM interaction_comments ic WHERE ic.interaction_id = interactions.id
      );
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_create)
            cur.execute(sql_seed)
        conn.commit()
    finally:
        conn.close()

init_db()
patch_db_schema()
ensure_db_indexes()
ensure_comments_table()

# ---------------- Utilidades varias ----------------
def upload_file_to_s3(file_path, bucket, object_name=None):
    if object_name is None:
        object_name = os.path.basename(file_path)
    try:
        s3_client.upload_file(file_path, bucket, object_name)
        print(f"[S3 UPLOAD] Archivo {file_path} subido a s3://{bucket}/{object_name}")
        return f"https://{bucket}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{object_name}"
    except ClientError as e:
        print(f"[S3 ERROR] Falló la subida a S3: {e}")
        return None

def issue_jwt(payload: dict, days: int = 7) -> str:
    payload = payload.copy()
    payload.update({"iat": datetime.utcnow(), "exp": datetime.utcnow() + timedelta(days=days)})
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def _parse_training_json(raw: str):
    """
    Acepta el texto del 'Mensaje de Capacitación' (JSON o texto).
    Devuelve SIEMPRE un dict compacto con un bloque 'readable' seguro.
    """
    import re, json

    DEFAULT = {
        "is_json": False,
        "raw": "",
        "summary": "",
        "risk": None,
        "strengths": [],
        "opportunities": [],
        "kpi_avg": None,
        "product_score_total": None,
        "da_vinci": {"prep": None, "open": None, "persuasion": None, "close": None},
        "readable": {
            "score_14": None,
            "listening": None,
            "dv_signals": None,
            "coaching": [],
            "frase": "",
            "rh_text": "",
        },
    }

    if not raw:
        return DEFAULT

    def _as_list(x):
        if isinstance(x, list): return [str(i).strip() for i in x if str(i).strip()]
        if isinstance(x, str) and x.strip(): return [x.strip()]
        return []

    try:
        d = json.loads(raw)

        strengths     = d.get("compact", {}).get("strengths") or d.get("strengths") or []
        opportunities = d.get("opportunities") or d.get("compact", {}).get("opportunities") or []

        # Score 0–14
        score14 = d.get("compact", {}).get("score_14") or d.get("score_14")
        if score14 is None:
            kpis = d.get("kpis")
            if isinstance(kpis, list):
                for s in kpis:
                    m = re.search(r"Score\s*0\s*[-–—]\s*14\s*:\s*(\d+)", str(s))
                    if m: score14 = int(m.group(1)); break

        # Escucha activa
        listening = (d.get("interaction_quality") or {}).get("active_listening_level")
        if not listening:
            kpis = d.get("kpis")
            if isinstance(kpis, list):
                for s in kpis:
                    m = re.search(r"Escucha\s+activa\s*:\s*([A-Za-zÁÉÍÓÚáéíóú]+)", str(s))
                    if m: listening = m.group(1); break

        # Fases Da Vinci (nº de señales)
        dv_signals = None
        steps = (d.get("da_vinci_step_flags") or {}).get("steps_applied_count")  # "2/5"
        if isinstance(steps, str) and "/" in steps:
            try: dv_signals = int(steps.split("/")[0])
            except Exception: pass
        if dv_signals is None:
            kpis = d.get("kpis")
            if isinstance(kpis, list):
                for s in kpis:
                    m = re.search(r"Fases?\s+Da\s+Vinci\s*:\s*(\d+)", str(s))
                    if m: dv_signals = int(m.group(1)); break

        coaching = _as_list(d.get("coaching_3") or d.get("coaching") or [])
        frase    = d.get("frase_guia") or d.get("frase_guía") or d.get("suggestion") or ""
        rh_text  = d.get("rh_text") or ""

        return {
            "is_json": True,
            "raw": d,
            "summary": d.get("overall_training_summary") or "",
            "risk": d.get("compact", {}).get("risk") or d.get("risk"),
            "strengths": strengths,
            "opportunities": opportunities,
            "kpi_avg": (d.get("kpis") or {}).get("avg_score") if isinstance(d.get("kpis"), dict) else None,
            "product_score_total": d.get("product_score_total"),
            "da_vinci": {
                "prep": (d.get("da_vinci_points") or {}).get("preparacion") or (d.get("da_vinci_points") or {}).get("preparación"),
                "open": (d.get("da_vinci_points") or {}).get("apertura"),
                "persuasion": (d.get("da_vinci_points") or {}).get("persuasion"),
                "close": (d.get("da_vinci_points") or {}).get("cierre"),
            },
            "readable": {
                "score_14": score14,
                "listening": listening,
                "dv_signals": dv_signals,
                "coaching": coaching,
                "frase": frase,
                "rh_text": rh_text,
            },
        }
    except Exception:
        # Texto plano o JSON inválido → estructura segura por defecto con 'raw'
        out = DEFAULT.copy()
        out["raw"] = raw
        return out

def _is_recent(ts, hours=36):
    """Para marcar 'Nuevo': si la interacción es reciente."""
    if not ts: return False
    try:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z",""))
        return (datetime.utcnow() - ts) <= timedelta(hours=hours)
    except Exception:
        return False

# ---------------- Rutas de usuarios ----------------
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
                """,(name, email, date.today(), date.today()+timedelta(days=365), token))
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

def check_user_token(email: str, token: str) -> bool:
    today = date.today().isoformat()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT active, start_date, end_date, token
                FROM   users
                WHERE  email = %s
            """,(email.lower().strip(),))
            row = cur.fetchone()
        if not row: return False
        active, start, end, stored_token = row
        return (
            active
            and (start is None or start <= today)
            and (end   is None or end   >= today)
            and stored_token.strip() == token.strip()
        )
    finally:
        if conn: conn.close()

# ---------------- Páginas ----------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["password"] == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin-directory")
        return "Contraseña incorrecta", 403
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- Limpieza de textos ----------------
def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace('\r\n', ' ').replace('\n', ' ').strip()
    text = text.replace('\\303\\251', 'é').replace('\\303\\241', 'á').replace('\\303\\255', 'í')
    text = text.replace('\\303\\263', 'ó').replace('\\303\\272', 'ú').replace('\\303\\261', 'ñ')
    text = text.replace('\\302\\277', '¿').replace('\\302\\241', '¡')
    words = text.split(' ')
    cleaned_words_list, last_word = [], None
    for word in words:
        if word != last_word: cleaned_words_list.append(word)
        last_word = word
    text = ' '.join(cleaned_words_list)
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ---------------- KPIs por usuario (admin) ----------------
def _parse_frac(txt: str, default=(0, 1)) -> tuple[int, int]:
    try:
        a, b = str(txt).split("/")
        return int(a), max(1, int(b))
    except Exception:
        return default

def _safe_get(dic, path, default=None):
    try:
        cur = dic
        for key in path.split("."):
            cur = cur.get(key, {})
        return cur if cur != {} else default
    except Exception:
        return default

def build_performance_summaries(processed_data: list[list]) -> list[dict]:
    by_user = defaultdict(lambda: {
        "name": "", "email": "", "sessions": 0, "dv_points": [],
        "steps_pct": [], "legacy_k": [], "red_flags": 0, "last_date": None
    })
    for row in processed_data:
        try:
            name  = row[1] or ""
            email = row[2] or ""
            internal = row[9] if isinstance(row[9], dict) else {}
            ts = row[7] or ""

            dv = internal.get("da_vinci_points") or internal.get("abbott_points") or {}
            dv_total = int(dv.get("total", 0))

            steps_str = _safe_get(internal, "da_vinci_step_flags.steps_applied_count", "0/5")
            steps_num, steps_den = _parse_frac(steps_str, (0, 5))
            steps_pct = (steps_num / max(1, steps_den)) * 100.0

            legacy_str = internal.get("knowledge_score_legacy", "0/8")
            legacy_num, legacy_den = _parse_frac(legacy_str, (0, 8))
            legacy_pct = (legacy_num / max(1, legacy_den)) * 100.0

            dv_norm = min(dv_total, 8) / 8.0 * 100.0
            _ = 0.5 * dv_norm + 0.5 * legacy_pct

            red = 1 if internal.get("disqualifying_phrases_detected") else 0

            bucket = by_user[email]
            bucket["name"]   = name
            bucket["email"]  = email
            bucket["sessions"] += 1
            bucket["dv_points"].append(dv_total)
            bucket["steps_pct"].append(steps_pct)
            bucket["legacy_k"].append(legacy_num)
            bucket["red_flags"] += red

            if ts:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if not bucket["last_date"] or dt > bucket["last_date"]:
                        bucket["last_date"] = dt
                except Exception:
                    pass
        except Exception as e:
            print(f"[perf_summaries] fila omitida por error: {e}")

    summaries = []
    for email, agg in by_user.items():
        sessions = max(1, agg["sessions"])
        avg_dv = sum(agg["dv_points"]) / sessions
        avg_steps_pct = sum(agg["steps_pct"]) / sessions
        avg_legacy_num = sum(agg["legacy_k"]) / sessions
        dv_norm = min(avg_dv, 8) / 8.0 * 100.0
        legacy_pct = (avg_legacy_num / 8.0) * 100.0
        avg_score = 0.5 * dv_norm + 0.5 * legacy_pct
        last_date_str = agg["last_date"].strftime("%Y-%m-%d %H:%M") if agg["last_date"] else "—"
        summaries.append({
            "name": agg["name"], "email": agg["email"], "sessions_published": sessions,
            "avg_score": round(avg_score, 1), "avg_dv_points": round(avg_dv, 1),
            "avg_steps_pct": round(avg_steps_pct, 0), "avg_legacy": round(avg_legacy_num, 1),
            "red_flags": int(agg["red_flags"]), "last_date": last_date_str
        })
    summaries.sort(key=lambda x: x["avg_score"], reverse=True)
    return summaries

# ---------------- Admin Panel (legacy) ----------------
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
                name = request.form["name"]; email = request.form["email"]
                start = request.form["start_date"]; end = request.form["end_date"]
                token = secrets.token_hex(8)
                try:
                    c.execute("""
                        INSERT INTO users (name, email, start_date, end_date, active, token)
                        VALUES (%s, %s, %s, %s, 1, %s)
                        ON CONFLICT (email) DO UPDATE SET
                          name = EXCLUDED.name,
                          start_date = EXCLUDED.start_date,
                          end_date = EXCLUDED.end_date,
                          active = EXCLUDED.active,
                          token = EXCLUDED.token;
                    """,(name, email, start, end, token))
                    conn.commit()
                except Exception as e:
                    if conn: conn.rollback()
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
            SELECT
                i.id, i.name, i.email, i.scenario, i.message, i.response, i.audio_path,
                i.timestamp, i.evaluation, i.evaluation_rh, i.tip, i.visual_feedback,
                i.visible_to_user,
                i.rh_comment,
                COALESCE(
                  (
                    SELECT json_agg(
                      json_build_object(
                        'id', ic.id,
                        'author', COALESCE(ic.author,'Capacitación'),
                        'body', ic.body,
                        'created', to_char(ic.created_at,'YYYY-MM-DD HH24:MI')
                      )
                      ORDER BY ic.created_at DESC
                    )
                    FROM interaction_comments ic
                    WHERE ic.interaction_id = i.id
                  ),
                  '[]'::json
                ) AS comments_json
            FROM interactions i
            ORDER BY i.timestamp DESC
        """)
        raw_data = c.fetchall()

        processed_data = []
        for row in raw_data:
            try:
                user_dialogue_raw = json.loads(row[4]) if row[4] else []
                avatar_dialogue_raw = json.loads(row[5]) if row[5] else []
                if not isinstance(user_dialogue_raw, list):   user_dialogue_raw = [str(user_dialogue_raw)]
                if not isinstance(avatar_dialogue_raw, list): avatar_dialogue_raw = [str(avatar_dialogue_raw)]

                cleaned_user_segments   = [clean_display_text(str(s).strip()) for s in user_dialogue_raw if str(s).strip()]
                cleaned_avatar_segments = [clean_display_text(str(s).strip()) for s in avatar_dialogue_raw if str(s).strip()]

                cleaned_name     = clean_display_text(str(row[1])) if row[1] else ""
                cleaned_email    = clean_display_text(str(row[2])) if row[2] else ""
                cleaned_scenario = clean_display_text(str(row[3])) if row[3] else ""

                try:
                    parsed_rh_evaluation = json.loads(row[9]) if row[9] else {}
                    if not parsed_rh_evaluation:
                        parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}
                except (json.JSONDecodeError, TypeError):
                    parsed_rh_evaluation = {"status": "No hay análisis de RH disponible."}

                video_url_for_template = f"/video/{row[6]}" if row[6] else None

                comments_json = row[14]
                if isinstance(comments_json, str):
                    try: comments_json = json.loads(comments_json)
                    except Exception: comments_json = []

                current_processed_row = [
                    row[0],                 # 0: ID
                    cleaned_name,           # 1: Name
                    cleaned_email,          # 2: Email
                    cleaned_scenario,       # 3: Scenario
                    cleaned_user_segments,  # 4: User dialogue (list)
                    cleaned_avatar_segments,# 5: Avatar dialogue (list)
                    video_url_for_template, # 6: Video URL
                    row[7],                 # 7: Timestamp
                    row[8] or "Análisis IA pendiente.",       # 8: Public Summary
                    parsed_rh_evaluation,   # 9: Internal JSON
                    row[10] or "Consejo pendiente.",          # 10: Tip
                    row[11] or "Análisis visual pendiente.",  # 11: Visual feedback
                    row[12],                # 12: visible_to_user
                    row[13] or "",          # 13: rh_comment (último publicado)
                    comments_json or []     # 14: historial
                ]
                processed_data.append(current_processed_row)
            except Exception as e:
                print(f"Error processing row from database: {e}. Raw row: {row}")
                processed_data.append([
                    row[0] if len(row) > 0 else "N/A", "Error","Error","Error al cargar",
                    ["Error al cargar transcripción del participante."],
                    ["Error al cargar transcripción del avatar."],
                    None,"N/A",
                    f"Error de procesamiento: {str(e)}",
                    {"status": f"Error al cargar análisis de RH: {str(e)}"},
                    "Error al cargar consejo.", "Error al cargar feedback visual.",
                    False, "", []
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
            usage_summaries.append({"name": name_u, "email": email_u, "minutes": mins, "summary": summary})

        contracted_minutes = 1050
        performance_summaries = build_performance_summaries(processed_data)

    except Exception as e:
        print(f"Error en el panel de administración (PostgreSQL): {e}")
        if conn: conn.rollback()
        return f"Error en el panel de administración: {str(e)}", 500
    finally:
        if conn: conn.close()

    return render_template(
        "admin.html",
        data=processed_data,
        users=users,
        usage_summaries=usage_summaries,
        total_minutes=total_minutes_all_users,
        contracted_minutes=contracted_minutes,
        performance_summaries=performance_summaries
    )

# ---------------- Inicio de sesión del usuario ----------------
@app.route("/start-session", methods=["POST"])
def start_session():
    name     = (request.form.get("name") or "").strip()
    email    = (request.form.get("email") or "").strip().lower()
    scenario = (request.form.get("scenario") or "").strip()

    if scenario.count(".") >= 2 or len(scenario) > 80 or not scenario:
        scenario = "Entrevista con médico"

    if not all([name, email, scenario]):
        return "Faltan datos.", 400

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

    payload = {
        "name":     name,
        "email":    email,
        "scenario": scenario,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

    url = f"{FRONTEND_URL}/dashboard?auth={token}"
    print("DEBUG_REDIRECT ->", url, " | scenario:", scenario)
    return redirect(url, code=302)

@app.route("/validate_user", methods=["POST"])
def validate_user_endpoint():
    data = request.get_json()
    name = data.get("name"); email = data.get("email"); token = data.get("token")
    today = date.today().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT active, start_date, end_date, token FROM users WHERE email=%s", (email,))
        row = c.fetchone()
        conn.close()

        if not row: return "Usuario no registrado.", 403
        if not row[0]: return "Usuario inactivo. Contacta a RH.", 403
        if not (row[1] <= today <= row[2]): return "Acceso fuera de rango permitido.", 403
        if row[3] != token: return "Token inválido.", 403
        return jsonify({"status": "ok", "message": "Usuario validado correctamente."}), 200
    except Exception as e:
        print(f"ERROR: validate_user failed: {e}")
        if conn: conn.rollback()
        return f"Error interno al validar usuario: {str(e)}", 500
    finally:
        if conn: conn.close()

# ---------------- Re-evaluación ----------------
from evaluator import evaluate_and_persist

def _db_conn():
    p = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=p.path[1:], user=p.username, password=p.password,
        host=p.hostname, port=p.port, sslmode="require",
    )

@app.route("/admin/recompute/<int:session_id>", methods=["GET", "POST"])
def admin_recompute(session_id: int):
    user_t, leo_t = "", ""
    try:
        conn = _db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT message, response FROM interactions WHERE id = %s",(session_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            print(f"[recompute] sesión {session_id} no encontrada")
            return redirect("/admin")
        try:
            user_msgs = json.loads(row[0]) if row[0] else []
            leo_msgs  = json.loads(row[1]) if row[1] else []
        except Exception:
            user_msgs, leo_msgs = [row[0] or ""], [row[1] or ""]
        user_t = "\n".join(user_msgs); leo_t  = "\n".join(leo_msgs)
    except Exception as e:
        print(f"[recompute] error leyendo BD: {e}")
        return redirect("/admin")

    try:
        evaluate_and_persist(session_id, user_t, leo_t, video_path=None)
        print(f"[recompute] sesión {session_id} evaluada OK")
    except Exception as e:
        print(f"[recompute] error evaluando sesión {session_id}: {e}")

    return redirect("/admin")

# ---------------- Dashboard API ----------------
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
            cur.execute("""
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
            """,(email,))
            raw_rows = cur.fetchall()

        sessions_to_send = []
        for row in raw_rows:
            processed = dict(row)
            for field in ("user_transcript", "avatar_transcript"):
                raw = processed.get(field, "[]")
                try: processed[field] = "\n".join(json.loads(raw))
                except (json.JSONDecodeError, TypeError): processed[field] = raw

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
            cur.execute("SELECT COALESCE(SUM(duration_seconds),0) FROM interactions WHERE email=%s",(email,))
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
        if conn: conn.close()

# ---------------- Video ----------------
@app.route("/video/<path:filename>")
def serve_video(filename):
    presigned = s3_client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': AWS_S3_BUCKET_NAME, 'Key': filename},
        ExpiresIn=3600
    )
    print(f"[SERVE VIDEO] -> {presigned}")
    return redirect(presigned, code=302)

# ---------------- Upload ----------------
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
        if os.path.exists(local_path): os.remove(local_path)

# ---------------- Utils para guardar sesiones ----------------
def _as_json_list(txt: Union[str, list]) -> str:
    if isinstance(txt, list): return json.dumps(txt)
    if isinstance(txt, str):  return json.dumps([l for l in txt.splitlines() if l.strip()])
    return json.dumps([])

@app.route("/log_full_session", methods=["POST"])
def log_full_session():
    data = request.get_json() or {}
    name   = data.get("name");   email = data.get("email");   scenario = data.get("scenario")
    duration = int(data.get("duration", 0))
    video_key = data.get("video_object_key") or data.get("s3_object_key")
    user_json   = _as_json_list(data.get("conversation", ""))
    avatar_json = _as_json_list(data.get("avatar_transcript", ""))
    public_summary       = data.get("evaluation", "")
    internal_summary_db  = data.get("evaluation_rh", "")
    tip_text             = data.get("tip", "")
    posture_feedback     = data.get("visual_feedback", "")
    timestamp_iso = datetime.utcnow().isoformat()

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
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
            """,(name, email, scenario, user_json, avatar_json, video_key, timestamp_iso,
                 public_summary, internal_summary_db, duration, tip_text, posture_feedback))
            session_id = cur.fetchone()[0]
        conn.commit()
        print(f"[DB] Sesión #{session_id} registrada correctamente.")

        try:
            from celery_worker import process_session_transcript
            task_data = {
                "session_id": session_id,
                "duration": duration,
                "video_object_key": video_key,
                "user_transcript": user_json
            }
            result = process_session_transcript.delay(task_data)
            app.logger.info("🚀  Sesión %s ENCOLADA (task_id=%s)", session_id, result.id)
        except Exception as e:
            app.logger.warning(f"Celery no disponible o error encolando: {e}")

        return jsonify({"status":"success","session_id":session_id,"message":"Sesión registrada."}), 200
    except Exception as e:
        if conn: conn.rollback()
        print(f"[ERROR] log_full_session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# ---------------- Publicar / Notas (historial) ----------------
@app.post("/admin/publish_eval/<int:sid>")
def publish_eval(sid: int):
    comment = (request.form.get("comment_rh") or "").strip()
    if not comment:
        flash("Escribe un comentario antes de publicar.", "error")
        return redirect(url_for("admin_panel"))

    with get_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO interaction_comments (interaction_id, author, body) VALUES (%s, %s, %s)",
                    (sid, "Capacitación", comment)
                )
                cur.execute(
                    "UPDATE interactions SET rh_comment = %s, visible_to_user = TRUE WHERE id = %s;",
                    (comment, sid)
                )
            conn.commit()
            flash(f"Sesión {sid} publicada con comentario RH ✅", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error publicando comentario: {e}", "error")
    return redirect(url_for("admin_panel"))

@app.post("/admin/add_note/<int:sid>")
def add_note(sid: int):
    note = (request.form.get("note_body") or "").strip()
    if not note:
        flash("Escribe una nota antes de guardar.", "error")
        return redirect(url_for("admin_panel"))

    with get_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO interaction_comments (interaction_id, author, body) VALUES (%s, %s, %s)",
                    (sid, "Capacitación", note)
                )
            conn.commit()
            flash(f"Sesión {sid}: nota agregada al historial 📝", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error guardando nota: {e}", "error")
    return redirect(url_for("admin_panel"))

@app.post("/admin/publish_ai/<int:sid>")
def publish_ai(sid: int):
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE interactions SET rh_comment = NULL, visible_to_user = TRUE WHERE id = %s;", (sid,))
    flash(f"Sesión {sid} publicada con análisis IA ✅", "success")
    return redirect(url_for("admin_panel"))

# ─────────────────────────────────────────────────────────
# Nuevo: Directorio administrativo "ligero"
# ─────────────────────────────────────────────────────────
@app.route("/admin-directory", methods=["GET"])
def admin_directory():
    if not session.get("admin"):
        return redirect("/login")

    conn = get_db_connection()
    rows = []
    try:
        with conn.cursor() as cur:
            # Trae usuarios + conteos + última interacción + pendientes
            cur.execute("""
                WITH last_inter AS (
                  SELECT email,
                         MAX(timestamp) AS last_ts,
                         COUNT(*) FILTER (
                           WHERE COALESCE(evaluation_rh,'')='' OR visible_to_user IS NOT TRUE
                         ) AS pend
                  FROM interactions
                  GROUP BY email
                )
                SELECT
                  u.name,
                  u.email,
                  COALESCE(COUNT(i.*), 0) AS sesiones,
                  COALESCE(
                    COUNT(*) FILTER (
                      WHERE i.audio_path IS NOT NULL
                        AND i.audio_path <> ''
                        AND i.audio_path NOT IN (
                          'Video_Not_Available_Error',
                          'Video_Processing_Failed',
                          'Video_Missing_Error'
                        )
                    ), 0
                  ) AS videos,
                  COALESCE(l.last_ts, NULL) AS last_ts,
                  COALESCE(l.pend, 0) AS pending
                FROM users u
                LEFT JOIN interactions i ON i.email = u.email
                LEFT JOIN last_inter l ON l.email = u.email
                GROUP BY u.name, u.email, l.last_ts, l.pend
                ORDER BY u.name ASC, u.email ASC;
            """)
            base = cur.fetchall()

            # KPIs y flag de nuevos
            for name, email, sesiones, videos, last_ts, pending in base:
                cur.execute("""
                    SELECT evaluation_rh, timestamp
                    FROM interactions
                    WHERE email=%s
                    ORDER BY timestamp DESC
                    LIMIT 100
                """, (email,))
                evals = cur.fetchall()
                kpis = []
                recent_flag = False
                for ev_raw, ts in evals:
                    parsed = _parse_training_json(ev_raw)
                    if parsed.get("kpi_avg") is not None:
                        kpis.append(float(parsed["kpi_avg"]))
                    if _is_recent(ts):
                        recent_flag = True
                avg_kpi = round(sum(kpis)/len(kpis), 2) if kpis else None
                rows.append((name, email, sesiones, videos, last_ts, pending, recent_flag, avg_kpi))
    finally:
        conn.close()

    return render_template("admin_directory.html", rows=rows)

@app.route("/admin-user/<path:email>", methods=["GET"])
def admin_user(email):
    if not session.get("admin"):
        return redirect("/login")

    conn = get_db_connection()
    user_name = email
    sessions = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM users WHERE email=%s", (email,))
            r = cur.fetchone()
            if r and r[0]:
                user_name = r[0]

            # 👇 añadí i.rh_comment al SELECT
            cur.execute("""
                SELECT
                  i.id, i.scenario, i.timestamp, i.audio_path,
                  i.evaluation, i.evaluation_rh, i.tip, i.visual_feedback,
                  i.message, i.response, i.visible_to_user, i.rh_comment
                FROM interactions i
                WHERE i.email = %s
                ORDER BY i.timestamp DESC NULLS LAST;
            """, (email,))
            raw = cur.fetchall()

        def to_lines(s):
            if not s:
                return []
            try:
                v = json.loads(s)
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()]
                v = str(v).strip()
                return [v] if v else []
            except Exception:
                return [x.strip() for x in str(s).splitlines() if x.strip()]

        from os.path import basename

        for row in raw:
            training = _parse_training_json(row[5])  # evaluation_rh

            # --- URLs presignadas para ver/descargar
            key = (row[3] or "").strip()
            video_url = ""
            video_dl_url = ""
            if key and key not in SENTINELS:
                try:
                    mime = _guess_video_mime(key)
                    video_url = s3_client.generate_presigned_url(
                        ClientMethod='get_object',
                        Params={
                            'Bucket': AWS_S3_BUCKET_NAME,
                            'Key': key,
                            'ResponseContentType': mime,
                        },
                        ExpiresIn=3600
                    )
                    video_dl_url = s3_client.generate_presigned_url(
                        ClientMethod='get_object',
                        Params={
                            'Bucket': AWS_S3_BUCKET_NAME,
                            'Key': key,
                            'ResponseContentDisposition': f'attachment; filename="{basename(key)}"',
                        },
                        ExpiresIn=3600
                    )
                except Exception:
                    video_url = ""
                    video_dl_url = ""

            sessions.append({
                "id": row[0],
                "scenario": row[1],
                "timestamp": row[2],
                "audio_path": key,
                "video_url": video_url,
                "video_dl_url": video_dl_url,
                "evaluation": row[4] or "",
                "evaluation_rh_raw": row[5] or "",
                "training": training,
                "tip": row[6] or "",
                "visual_feedback": row[7] or "",
                "user_dialogue": to_lines(row[8]),
                "avatar_dialogue": to_lines(row[9]),
                "visible_to_user": bool(row[10]),
                "rh_comment": row[11] or "",   # 👈 ahora sí lo mandamos a la plantilla
            })
    finally:
        conn.close()

    # Oculta sesiones sin conversación
    sessions = [
        s for s in sessions
        if (len(s.get("user_dialogue", [])) + len(s.get("avatar_dialogue", []))) > 0
    ]

    return render_template("admin_user.html", user_name=user_name, email=email, sessions=sessions)


@app.route("/admin-user/<int:interaction_id>/save", methods=["POST"])
def admin_save_feedback(interaction_id):
    if not session.get("admin"):
        return ("Forbidden", 403)

    data = request.form
    # Opcional: editar el análisis (JSON o texto)
    evaluation_rh = data.get("evaluation_rh", None)  # None ⇒ no tocar, string ⇒ actualizar
    # Único campo de retroalimentación para enviar al usuario
    feedback = data.get("feedback", "").strip()
    send_to_user = (data.get("send_to_user") == "on")

    sets = []
    params = []

    if evaluation_rh is not None:
        sets.append("evaluation_rh=%s")
        params.append(evaluation_rh)

    # Unifica feedback en rh_comment
    sets.append("rh_comment=%s")
    params.append(feedback)

    sets.append("visible_to_user=%s")
    params.append(send_to_user)

    sql = f"UPDATE interactions SET {', '.join(sets)} WHERE id=%s"
    params.append(interaction_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
        conn.commit()
    finally:
        conn.close()

    return redirect(request.referrer or "/admin-directory")

# ---------------- Health ----------------
@app.route("/healthz")
def health_check():
    return "OK", 200
