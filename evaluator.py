# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# GUARDA SIEMPRE métricas en la BD (interactions.evaluation_rh) cuando
# se llama vía evaluate_and_persist(). También devuelve un bloque público.
#
# Estructura de retorno:
#   { "public": str, "internal": dict, "level": "alto" | "error" }
# -------------------------------------------------------------------
import os, json, textwrap, unicodedata, re, difflib
from typing import Optional, Dict, List
from urllib.parse import urlparse

# OpenCV opcional (para análisis visual express si hay video)
try:
    import cv2
except ImportError:
    cv2 = None

import psycopg2
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# ────────────────────────────────────────────────────────────────────
#  Utilidades
# ────────────────────────────────────────────────────────────────────

def normalize(txt: str) -> str:
    """Minúsculas + sin acentos + espacios compactados."""
    if not txt:
        return ""
    t = unicodedata.normalize("NFD", txt)
    t = t.encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t

def canonicalize_products(nt: str) -> str:
    """Normaliza variantes del producto a 'esoxx-one' (tolerante a ASR)."""
    variants = [
        r"\beso\s*xx\s*one\b",
        r"\besox+\s*one\b",
        r"\besoxx-one\b",
        r"\besof+\s*one\b",
        r"\becox+\s*one\b",
        r"\besox+\b",
        r"\besof+\b",
        r"\becox+\b",
        r"\beso\s*xx\b",
        r"\besoft\s*one\b",
        r"\besoxxone\b",
    ]
    canon = nt
    for pat in variants:
        canon = re.sub(pat, "esoxx-one", canon)
    return canon

def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.82) -> bool:
    """True si 'needle' aparece en 'haystack' exacta o aproximadamente."""
    if not needle:
        return False
    if needle in haystack:
        return True
    tokens = haystack.split()
    win = min(max(len(needle.split()) + 4, 8), 40)
    for i in range(0, max(1, len(tokens) - win + 1)):
        segment = " ".join(tokens[i:i+win])
        ratio = difflib.SequenceMatcher(None, segment, needle).ratio()
        if ratio >= threshold:
            return True
    ratio_global = difflib.SequenceMatcher(None, haystack, needle).ratio()
    return ratio_global >= max(0.70, threshold - 0.1)

def count_fuzzy_any(nt: str, phrases: List[str], threshold: float = 0.82) -> int:
    return sum(1 for p in phrases if fuzzy_contains(nt, p, threshold))

# ────────────────────────────────────────────────────────────────────
#  Scoring config
# ────────────────────────────────────────────────────────────────────

WEIGHTED_KWS = {
    "3pt": [
        "esoxx-one mejora hasta 90% todos los sintomas de la erge",
        "reduccion del uso de antiacidos",
        "demostrado en ninos y adolescentes",
        "esoxx-one reduce hasta 90% la frecuencia y severidad de los sintomas de erge",
        "esoxx-one demostro mejoria significativa de los sintomas esofagicos y extraesofagicos de la erge",
        "esoxx-one mas ibp es significativamente mas eficaz que la monoterapia con ibp para la epitelizacion esofagica",
        "reduce significativamente los sintomas de la erge vs monoterapia ibps",
        "alivio en menor tiempo (2 semanas vs 4 semanas)",
        "reduce la falla al tratamiento",
    ],
    "2pt": [
        "protege y repara mucosa esofagica",
        "protege y promueve la reparacion de la mucosa esofagica",
        "barrera bioadhesiva",
        "combinacion de 3 activos",
        "acido hialuronico",
        "accion reparadora",
        "sulfato de condroitina",
        "accion protectora",
        "poloxamero 407",
        "agente bioadhesivo",
        "liquido a temperatura ambiente y en estado gel a temperatura corporal",
        "recubre el epitelio esofagico",
        "portador bioadhesivo de los componentes",
        "un sobre despues de cada comida y antes de dormir",
        "esperar por lo menos 30min despues sin tomar alimentos o bebidas",
        "esperar 60min",
        "esperar 1hr",
    ],
    "1pt": [
        "unico",
        "mecanismo de proteccion original e innovador para el manejo de la erge",
        "alivia los sintomas del erge",
        "esoxx-one",
        "forma un complejo macromolecular que recubre la mucosa esofagica",
        "actua como barrera mecanica contra componentes nocivos del reflujo",
        "mejora la calidad de vida de los pacientes",
    ],
}

DAVINCI_POINTS = {
    "preparacion": {2: ["objetivo smart", "mi objetivo hoy es"],
                    1: ["objetivo de la visita", "mensaje clave", "materiales"]},
    "apertura":    {2: ["cuales son las mayores preocupaciones que tiene en sus pacientes con erge",
                        "que caracteristicas tienen sus pacientes con reflujo gastroesofagico",
                        "dando seguimiento a mi visita anterior",
                        "me gustaria conocer que es lo que mas le preocupa"],
                    1: ["buenos dias dra", "mi nombre es", "como ha estado"]},
    "persuasion":  {2: ["que caracteristicas considera ideales en un producto para tratar la erge",
                        "que es lo que busca cuando selecciona un producto",
                        "cuales son los objetivos de tratamiento para tratar un paciente con reflujo gastroesofagico",
                        "en comparacion con los antiacidos",
                        "combinado con ibp"]},
    "cierre":      {2: ["con base a lo dialogado considera que esoxx-one pueden ser la mejor opcion de tratamiento",
                        "que otras caracteristicas necesita para considerar a esoxx-one como primera opcion",
                        "grupo de pacientes",
                        "beneficie a sus pacientes"],
                    1: ["puedo contar con", "podemos acordar"]},
    "analisis_post": {2: ["auto-evaluacion", "plan para proxima visita"],
                      1: ["objeciones", "proxima visita", "actualiza"]},
}

KW_LIST   = ["beneficio","estudio","sintoma","tratamiento","reflujo","mecanismo","eficacia","seguridad"]
BAD_PHRASES = ["no se","no tengo idea","lo invento","no lo estudie","no estudie bien","no conozco","no me acuerdo"]
LISTEN_KW  = ["entiendo","comprendo","veo que","lo que dices","si entiendo bien","parafraseando",
              "que le preocupa","me gustaria conocer","que caracteristicas tienen","podria contarme","como describe a sus pacientes"]

# ── Helpers de puntuación ───────────────────────────────────────────

def score_weighted_phrases(t: str) -> Dict[str, object]:
    nt = canonicalize_products(normalize(t))
    breakdown, total = [], 0
    for pts_key, phrases in WEIGHTED_KWS.items():
        pts = int(re.sub(r"[^0-9]", "", pts_key) or "0")
        for p in phrases:
            if fuzzy_contains(nt, p, threshold=0.80):
                total += pts
                breakdown.append({"phrase": p, "points": pts})
    return {"total_points": total, "breakdown": breakdown}

def score_davinci_points(t: str) -> Dict[str, int]:
    nt = canonicalize_products(normalize(t))
    out = {}
    for stage, rules in DAVINCI_POINTS.items():
        s = 0
        for pts, plist in rules.items():
            for p in plist:
                if fuzzy_contains(nt, p, threshold=0.80):
                    s += int(pts)
        out[stage] = s
    out["total"] = sum(v for v in out.values())
    return out

def kw_score(t: str) -> int:
    nt = canonicalize_products(normalize(t))
    return sum(1 for kw in KW_LIST if fuzzy_contains(nt, kw, threshold=0.84))

def disq_flag(t: str) -> bool:
    nt = normalize(t)
    return any(fuzzy_contains(nt, w, threshold=0.88) for w in BAD_PHRASES)

# ── Análisis visual express (si hay OpenCV y archivo) ───────────────
def visual_analysis(path: str):
    MAX_FRAMES = int(os.getenv("MAX_FRAMES_TO_CHECK", 60))
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return "⚠️ No se pudo abrir video.", "Error video", "N/A"
        frontal = total = 0
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        for _ in range(MAX_FRAMES):
            ok, frame = cap.read()
            if not ok:
                break
            total += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)
            if len(faces):
                frontal += 1
        cap.release()
        if not total:
            return "⚠️ Sin frames para analizar.", "Sin frames", "0.0%"
        ratio = frontal / total
        pct = f"{ratio*100:.1f}%"
        if ratio >= 0.7:
            return "✅ Buena presencia frente a cámara.", "Correcta", pct
        if ratio > 0:
            return "⚠️ Mejora la visibilidad.", "Mejorar visibilidad", pct
        return "❌ No se detectó rostro.", "No detectado", pct
    except Exception as e:
        return f"⚠️ Error visual: {e}", "Error video", "N/A"

# ── BD helper ───────────────────────────────────────────────────────
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set!")
    parsed = urlparse(database_url)
    return psycopg2.connect(
        database=parsed.path[1:], user=parsed.username, password=parsed.password,
        host=parsed.hostname, port=parsed.port, sslmode="require",
    )

# ────────────────────────────────────────────────────────────────────
#  Core evaluator
# ────────────────────────────────────────────────────────────────────
def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    # Visual
    vis_pub, vis_int, vis_pct = (
        visual_analysis(video_path)
        if video_path and cv2 and os.path.exists(video_path)
        else ("⚠️ Sin video disponible.", "No evaluado", "N/A")
    )

    # Señales por fase (para % fases aplicadas)
    PHRASE_MAP = {
        "preparacion":   ["objetivo de la visita","materiales","mensaje clave","smart","objetivo smart","mi objetivo hoy es"],
        "apertura":      ["buenos dias","como ha estado","pacientes","necesidades","visita anterior","que le preocupa","que caracteristicas tienen"],
        "persuasion":    ["objetivos de tratamiento","beneficio","mecanismo","estudio","evidencia","caracteristicas del producto","combinado con ibp"],
        "cierre":        ["siguiente paso","podemos acordar","cuento con usted","promocion","farmacias","puedo contar con"],
        "analisis_post": ["auto-evaluacion","objeciones","proxima visita","actualiza"],
    }
    def step_flag(step_kw: List[str]) -> bool:
        nt = canonicalize_products(normalize(user_text))
        return any(fuzzy_contains(nt, p, threshold=0.82) for p in step_kw)

    sales_model_flags = {step: step_flag(kws) for step, kws in PHRASE_MAP.items()}
    steps_applied_count = sum(sales_model_flags.values())
    steps_applied_pct = round(100.0 * steps_applied_count / 5.0, 1)

    # Puntuaciones propias (no-GPT)
    weighted   = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)
    legacy_8   = kw_score(user_text)
    listen_hits = count_fuzzy_any(normalize(user_text), LISTEN_KW, threshold=0.82)
    listen_level = "Alta" if listen_hits >= 4 else "Moderada" if listen_hits >= 2 else "Baja"

    # GPT (opcional; se cae con gracia)
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Eres un coach-evaluador senior de la industria farmacéutica.
        Debes calificar cada fase del Modelo Da Vinci según la evidencia
        en la conversación (Preparación, Apertura, Persuasión, Cierre,
        Análisis Posterior). Usa solo los datos dados y responde en JSON.
        Si el objetivo es SMART, menciónalo en la evaluación general.
        """)
        FORMAT_GUIDE = textwrap.dedent("""
        {
          "public_summary": "<máx 120 palabras>",
          "internal_analysis": {
            "overall_evaluation": "<2-3 frases>",
            "Modelo_DaVinci": {
              "preparacion": "Excelente | Bien | Necesita Mejora",
              "apertura": "Excelente | Bien | Necesita Mejora",
              "persuasion": "Excelente | Bien | Necesita Mejora",
              "cierre": "Excelente | Bien | Necesita Mejora",
              "analisis_post": "Excelente | Bien | Necesita Mejora"
            }
          }
        }
        """)
        convo = f"--- Participante ---\n{user_text}\n--- Médico (Leo) ---\n{leo_text or '(sin diálogo)'}"
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_GPT_MODEL", "gpt-4o-mini"),
            response_format={"type": "json_object"},
            timeout=40,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + FORMAT_GUIDE},
                {"role": "user", "content": convo},
            ],
            temperature=0.4,
        )
        gpt_json     = json.loads(completion.choices[0].message.content)
        gpt_public   = gpt_json.get("public_summary", "")
        gpt_internal = gpt_json.get("internal_analysis", {})
        level = "alto"
    except Exception:
        gpt_public = ("Tuviste una interacción clara. Refuerza el Modelo Da Vinci en cada fase, "
                      "usa evidencia específica y concreta, y cierra con un siguiente paso acordado.")
        gpt_internal = {
            "overall_evaluation": "Evaluación automática limitada. Se generaron métricas base.",
            "Modelo_DaVinci": {
                "preparacion": "Necesita Mejora",
                "apertura": "Necesita Mejora",
                "persuasion": "Necesita Mejora",
                "cierre": "Necesita Mejora",
                "analisis_post": "Necesita Mejora",
            }
        }
        level = "error"

    # Map cualitativo → numérico para KPI promedio
    MAP_Q2N = {"Excelente": 3, "Bien": 2, "Necesita Mejora": 1}
    md = gpt_internal.get("Modelo_DaVinci", {}) or {}
    md_scores = [
        MAP_Q2N.get(md.get("preparacion",   "Necesita Mejora"), 1),
        MAP_Q2N.get(md.get("apertura",      "Necesita Mejora"), 1),
        MAP_Q2N.get(md.get("persuasion",    "Necesita Mejora"), 1),
        MAP_Q2N.get(md.get("cierre",        "Necesita Mejora"), 1),
        MAP_Q2N.get(md.get("analisis_post", "Necesita Mejora"), 1),
    ]
    avg_phase_score_1_3 = round(sum(md_scores) / 5.0, 2)
    avg_score_0_10      = round((avg_phase_score_1_3 - 1) * (10 / 2), 1)

    internal_summary = {
        "overall_training_summary": gpt_internal.get("overall_evaluation", ""),
        "knowledge_score_legacy": f"{legacy_8}/8",
        "knowledge_score_legacy_num": legacy_8,            # ← numérico para SQL
        "knowledge_weighted_total_points": weighted["total_points"],
        "knowledge_weighted_breakdown": weighted["breakdown"],

        "visual_presence": vis_int,
        "visual_percentage": vis_pct,

        "da_vinci_step_flags": {
            **{k: ("✅" if v else "❌") for k, v in sales_model_flags.items()},
            "steps_applied_count": f"{steps_applied_count}/5",
            "steps_applied_pct": steps_applied_pct,
        },
        "da_vinci_points": davinci_pts,

        "active_listening_simple_detection": listen_level,
        "disqualifying_phrases_detected": disq_flag(user_text),

        "gpt_detailed_feedback": {
            "overall_evaluation": gpt_internal.get("overall_evaluation", ""),
            "Modelo_DaVinci": md
        },

        "kpis": {
            "avg_score": avg_score_0_10,              # 0–10
            "avg_phase_score_1_3": avg_phase_score_1_3,
            "avg_steps_pct": steps_applied_pct,       # 0–100
            "legacy_count": legacy_8,                 # 0–8
        }
    }

    public_block = textwrap.dedent(f"""
        {gpt_public}

        {vis_pub}

        Áreas sugeridas extra:
        • Refuerza el Modelo Da Vinci en cada interacción.
        • Usa evidencia clínica concreta al responder.
        • Practica manejo de objeciones (método APACT).
        • Mantén buena presencia y contacto visual.
    """).strip()

    return {"public": public_block, "internal": internal_summary, "level": level}

# ────────────────────────────────────────────────────────────────────
#  Persistencia en BD
# ────────────────────────────────────────────────────────────────────
def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    """
    Evalúa y GUARDA SIEMPRE en interactions.evaluation_rh (JSON).
    Además devuelve el bloque público por si se quiere mostrar/guardar.
    """
    result = evaluate_interaction(user_text, leo_text, video_path)

    internal = result.get("internal") or {}
    # Refuerzos por si el caller vino con textos pobres o GPT falló:
    internal.setdefault("da_vinci_points", score_davinci_points(user_text))
    internal.setdefault("knowledge_score_legacy", f"{kw_score(user_text)}/8")
    internal.setdefault("knowledge_score_legacy_num", kw_score(user_text))
    if "kpis" not in internal:
        internal["kpis"] = {
            "avg_score": 0.0,
            "avg_phase_score_1_3": 1.0,
            "avg_steps_pct": 0.0,
            "legacy_count": kw_score(user_text),
        }

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE interactions SET evaluation_rh = %s WHERE id = %s",
                (json.dumps(internal), int(session_id))
            )
        conn.commit()
    except Exception:
        # No lanzamos excepción al caller; marcamos como error y dejamos constancia en public.
        result["level"] = "error"
        result["public"] += "\n\n⚠️ No se pudo registrar el análisis en BD."
    finally:
        if conn:
            conn.close()

    return result
