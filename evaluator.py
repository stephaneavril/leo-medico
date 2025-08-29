# evaluator.py — Transcript-first, salida compacta para RH + resumen diplomático para usuario
# -----------------------------------------------------------------------------
# Retorna:
#   {
#     "public": "<resumen amable (usuario)>",
#     "internal": {
#        ... (métricas existentes para no romper Admin) ...
#        "compact": {
#           "session": {"id": <int>, "name": "<str|None>", "email": "<str|None>", "timestamp": "<iso|None>"},
#           "score_14": <int>,
#           "risk": "ALTO|MEDIO|BAJO",
#           "strengths": [str, ...],
#           "opportunities": [str, ...],
#           "coaching_3": [str, str, str],
#           "frase_guia": "<str>",
#           "kpis": [str, str, str],
#           "rh_text": "<bloque listo para pegar>",
#           "user_text": "<versión amable>"
#        }
#     },
#     "level": "alto|error"
#   }
# Guarda en BD: `evaluation_rh` (JSON) sin tocar `evaluation` (lo actualiza el worker).
# -----------------------------------------------------------------------------

from __future__ import annotations
import os, json, textwrap, unicodedata, re, difflib, logging
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

# OpenCV es opcional
try:
    import cv2
except ImportError:
    cv2 = None

import psycopg2
from openai import OpenAI

# ───────────────────────── Config ─────────────────────────
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# Habilitar o no evaluación visual (por defecto DESACTIVADA)
EVAL_ENABLE_VIDEO = os.getenv("EVAL_ENABLE_VIDEO", "0").lower() in ("1", "true", "yes")

# Modelo GPT (ligero y barato por default)
GPT_MODEL = os.getenv("OPENAI_GPT_MODEL", "gpt-4o-mini")

# Logger sencillo
logging.basicConfig(level=os.getenv("EVAL_LOG_LEVEL", "INFO").upper())
log = logging.getLogger("evaluator")

# ───────────────────────── Utils ─────────────────────────

def normalize(txt: str) -> str:
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
        r"\beso\s*xx\s*one\b", r"\besox+\s*one\b", r"\besoxx-one\b",
        r"\besof+\s*one\b", r"\becox+\s*one\b", r"\besox+\b", r"\besof+\b",
        r"\becox+\b", r"\beso\s*xx\b", r"\besoxxone\b",
        r"\bays?oks?\b", r"\bays?oks?\s*one\b", r"\besok+\b",
    ]
    canon = nt
    for pat in variants:
        canon = re.sub(pat, "esoxx-one", canon)
    return canon

def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.82) -> bool:
    if not needle:
        return False
    if needle in haystack:
        return True
    tokens = haystack.split()
    win = min(max(len(needle.split()) + 4, 8), 40)
    for i in range(0, max(1, len(tokens) - win + 1)):
        segment = " ".join(tokens[i:i+win])
        if difflib.SequenceMatcher(None, segment, needle).ratio() >= threshold:
            return True
    return difflib.SequenceMatcher(None, haystack, needle).ratio() >= max(0.70, threshold - 0.1)

def count_fuzzy_any(nt: str, phrases: List[str], thr: float = 0.82) -> int:
    return sum(1 for p in phrases if fuzzy_contains(nt, p, thr))

# ─────────── Scoring config (reutiliza tu lógica previa) ───────────

WEIGHTED_KWS = {
    "3pt": [
        "esoxx-one reduce hasta 90% la frecuencia y severidad de los sintomas de erge",
        "alivio en menor tiempo (2 semanas vs 4 semanas)",
        "reduce la falla al tratamiento",
        "reduce significativamente los sintomas",
        "reduccion del uso de antiacidos",
    ],
    "2pt": [
        "protege y repara mucosa esofagica",
        "barrera bioadhesiva",
        "combinacion de 3 activos",
        "acido hialuronico",
        "sulfato de condroitina",
        "poloxamero 407",
        "un sobre despues de cada comida y antes de dormir",
        "esperar 30", "esperar 60", "30-60",
    ],
    "1pt": [
        "mecanismo de proteccion",
        "actua como barrera mecanica",
        "esoxx-one",
        "mejora la calidad de vida",
    ],
}

DAVINCI_POINTS = {
    "preparacion": {2: ["objetivo smart", "mi objetivo hoy es", "mensaje clave"], 1: ["objetivo de la visita", "plan para hoy"]},
    "apertura":    {2: ["cuales son las mayores preocupaciones", "que caracteristicas tienen sus pacientes"], 1: ["hola doctora", "gracias por su tiempo"]},
    "persuasion":  {2: ["beneficio", "mecanismo", "evidencia", "estudio", "combinado con ibp"],},
    "cierre":      {2: ["siguiente paso", "puedo contar con", "le parece si"], 1: ["acordar un siguiente paso"]},
    "analisis_post": {1: ["auto-evaluacion", "proxima visita", "que aprendi"]},
}

KW_LIST = ["beneficio", "estudio", "sintoma", "tratamiento", "reflujo", "mecanismo", "eficacia", "seguridad"]
BAD_PHRASES = ["no se", "no tengo idea", "lo invento", "no lo estudie", "no estudie bien", "no conozco", "no me acuerdo"]

LISTEN_KW  = [
    "entiendo", "comprendo", "veo que", "lo que dices", "si entiendo bien", "parafraseando",
    "que le preocupa", "me gustaria conocer", "que caracteristicas tienen", "podria contarme", "como describe a sus pacientes"
]

PRODUCT_RUBRIC: Dict[str, Dict[str, List[str] | int]] = {
    "mecanismo": {"weight": 2, "phrases": ["barrera bioadhesiva", "recubre el epitelio esofagico", "poloxamero 407", "acido hialuronico", "sulfato de condroitina"]},
    "eficacia": {"weight": 3, "phrases": ["alivio en menor tiempo", "reduce significativamente", "sinergia con inhibidores de la bomba de protones", "ibp mas esoxx-one"]},
    "evidencia": {"weight": 2, "phrases": ["estudio", "evidencia", "mejoria significativa", "reflujo nocturno"]},
    "uso_posologia": {"weight": 2, "phrases": ["un sobre despues de cada comida y antes de dormir", "30", "60", "agitar"]},
    "diferenciales": {"weight": 1, "phrases": ["no se absorbe", "bien tolerado", "dispositivo medico"]},
    "mensajes_base": {"weight": 1, "phrases": ["esoxx-one", "reflujo", "erge"]},
}

# ─────────── Scorers básicos ───────────

def score_weighted_phrases(t: str) -> Dict[str, object]:
    nt = canonicalize_products(normalize(t))
    breakdown, total = [], 0
    for pts_key, phrases in WEIGHTED_KWS.items():
        pts = int(re.sub(r"[^0-9]", "", pts_key) or "0")
        for p in phrases:
            if fuzzy_contains(nt, p, 0.80):
                total += pts
                breakdown.append({"phrase": p, "points": pts})
    return {"total_points": total, "breakdown": breakdown}

def score_davinci_points(t: str) -> Dict[str, int]:
    nt = canonicalize_products(normalize(t))
    out: Dict[str, int] = {}
    for stage, rules in DAVINCI_POINTS.items():
        s = 0
        for pts, plist in rules.items():
            for p in plist:
                if fuzzy_contains(nt, p, 0.79):
                    s += int(pts)
        out[stage] = s
    out["total"] = sum(out.values())
    return out

def kw_score(t: str) -> int:
    nt = canonicalize_products(normalize(t))
    return sum(1 for kw in KW_LIST if fuzzy_contains(nt, kw, 0.84))

def disq_flag(t: str) -> bool:
    nt = normalize(t)
    return any(fuzzy_contains(nt, w, 0.88) for w in BAD_PHRASES)

def product_compliance(t: str) -> Tuple[Dict[str, dict], int]:
    nt = canonicalize_products(normalize(t))
    detail: Dict[str, dict] = {}
    total = 0
    for cat, cfg in PRODUCT_RUBRIC.items():
        weight: int = int(cfg["weight"])
        hits = [p for p in cfg["phrases"] if fuzzy_contains(nt, p, 0.80)]
        score = weight if hits else 0
        total += score
        detail[cat] = {"weight": weight, "hits": hits, "score": score}
    return detail, total

def interaction_quality(t: str) -> Dict[str, object]:
    nt = normalize(t)
    tokens = nt.split()
    length = len(tokens)
    qmarks = t.count("?")
    question_rate = round(qmarks / max(1, length) * 100, 2)
    closing = any(fuzzy_contains(nt, k, 0.80) for k in [
        "siguiente paso", "podemos acordar", "puedo contar con", "le parece si", "empezar a considerar"
    ])
    objections = any(fuzzy_contains(nt, k, 0.82) for k in ["objecion", "preocupacion", "duda", "reserva"])
    listen_hits = count_fuzzy_any(nt, LISTEN_KW, 0.82)
    listen_level = "Alta" if listen_hits >= 4 else "Moderada" if listen_hits >= 2 else "Baja"
    return {
        "length_tokens": length,
        "question_rate": question_rate,
        "closing_present": closing,
        "objection_handling_signal": objections,
        "active_listening_level": listen_level,
    }

# ─────────── Visual opcional ───────────

def visual_analysis(path: str):
    """Devuelve (pub_msg, int_msg, pct_num, ratio_num)."""
    if not (path and cv2 and os.path.exists(path)):
        return "⚠️ Sin video evaluado por configuración.", "Sin evaluación de video.", 0.0, 0.0
    try:
        MAX_FRAMES = int(os.getenv("MAX_FRAMES_TO_CHECK", 60))
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return "⚠️ No se pudo abrir video.", "Error video", 0.0, 0.0
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
            return "⚠️ Sin frames para analizar.", "Sin frames", 0.0, 0.0
        ratio = frontal / total
        pct = ratio * 100.0
        if ratio >= 0.7:
            return "✅ Buena presencia frente a cámara.", "Correcta", pct, ratio
        if ratio > 0:
            return "⚠️ Mejora la visibilidad.", "Mejorar visibilidad", pct, ratio
        return "❌ No se detectó rostro.", "No detectado", 0.0, 0.0
    except Exception as e:
        return f"⚠️ Error visual: {e}", "Error video", 0.0, 0.0

# ─────────── BD ───────────

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set!")
    parsed = urlparse(database_url)
    return psycopg2.connect(
        database=parsed.path[1:], user=parsed.username, password=parsed.password,
        host=parsed.hostname, port=parsed.port, sslmode="require",
    )

def _get_session_info(session_id: int) -> dict:
    out = {"id": int(session_id), "name": None, "email": None, "timestamp": None, "scenario": None}
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT name, email, timestamp, scenario FROM interactions WHERE id=%s;", (int(session_id),))
            row = cur.fetchone()
        conn.close()
        if row:
            out["name"], out["email"], out["timestamp"], out["scenario"] = row
    except Exception as e:
        log.warning("No se pudo leer session info (%s): %s", session_id, e)
    return out

# ─────────── Ensamblado de salida compacta ───────────

POSO_PATTERNS = [
    "un sobre despues de cada comida y antes de dormir",
    "despues de cada comida", "antes de dormir", "30-60", "30 min", "60 min", "agitar"
]
EVID_PATTERNS = ["estudio", "evidencia", "savarino", "n=", "random", "p<", "significativa"]
NOCT_PATTERNS = ["nocturn", "noche", "sintomas nocturnos"]
IBP_PATTERNS  = ["ibp", "inhibidores de la bomba de protones", "omeprazol", "esomeprazol"]

ABSOLUTE_FLAGS_HI = [
    "90%", "noventa por ciento", "100%", "cualquier paciente", "para todos",
    "totalmente seguro", "no tiene efectos secundarios", "sin interacciones"
]
ABSOLUTE_FLAGS_MED = ["unico", "inmediato", "desde la primera toma", "garantiza"]

def _bool_hit(nt: str, pats: List[str], thr: float = 0.80) -> bool:
    return any(fuzzy_contains(nt, p, thr) for p in pats)

def _risk_level(nt: str) -> str:
    if _bool_hit(nt, ABSOLUTE_FLAGS_HI, 0.88):
        return "ALTO"
    if _bool_hit(nt, ABSOLUTE_FLAGS_MED, 0.86):
        return "MEDIO"
    return "BAJO"

def _phase_grade_to_points(grade: str) -> int:
    # Excelente=2, Bien=1, Necesita Mejora=0
    g = (grade or "").strip().lower()
    if g.startswith("excel"): return 2
    if g.startswith("bien"): return 1
    return 0

def _make_compact_brief(session_id: int, user_text: str, md_labels: dict, iq: dict) -> dict:
    nt = canonicalize_products(normalize(user_text))

    # Señales clave
    has_poso = _bool_hit(nt, POSO_PATTERNS)
    has_evid = _bool_hit(nt, EVID_PATTERNS)
    has_close = bool(iq.get("closing_present"))
    mentions_noct = _bool_hit(nt, NOCT_PATTERNS)
    mentions_ibp = _bool_hit(nt, IBP_PATTERNS)
    risk = _risk_level(nt)

    # Score 14 = 5 fases * (0–2) + posología(1) + evidencia(1) + cierre(1)
    phase_points = sum(_phase_grade_to_points(md_labels.get(k, "Necesita Mejora"))
                       for k in ["preparacion", "apertura", "persuasion", "cierre", "analisis_post"])
    score_14 = int(phase_points + (1 if has_poso else 0) + (1 if has_evid else 0) + (1 if has_close else 0))
    score_14 = max(0, min(14, score_14))

    # Fortalezas / Oportunidades
    strengths = []
    if mentions_noct: strengths.append("Conecta con reflujo nocturno (relevancia clínica).")
    if mentions_ibp:  strengths.append("Integra sinergia con IBP de forma adecuada.")
    if has_poso:      strengths.append("Explica posología correctamente (stick post-comida + nocturno; 30–60 min).")
    if iq.get("active_listening_level") in ("Alta", "Moderada"):
        strengths.append(f"Escucha activa {iq['active_listening_level'].lower()}.")

    opportunities = []
    if not has_evid:  opportunities.append("Aterrizar evidencias con dato breve (autor/año, n, resultado).")
    if not has_close: opportunities.append("Cerrar con un siguiente paso medible (p.ej., 2 casos y fecha de revisión).")
    if iq.get("active_listening_level") == "Baja":
        opportunities.append("Incrementar preguntas de descubrimiento y parafraseo.")
    # Evita mencionar pronunciación del producto (pedido explícito del cliente)

    # Coaching (3)
    coaching_3 = [
        "Practicar pitch breve: mecanismo + posología + evidencia (30–45 s).",
        ("Reforzar ejemplos de paciente con reflujo nocturno y uso adyuvante con IBP."
         if mentions_noct else
         "Agregar ejemplo concreto de reflujo nocturno con uso adyuvante con IBP."),
        "Preparar cierre con propuesta de seguimiento (2 pacientes; control en 2 semanas)."
    ]

    # Frase guía
    frase = ("“ESOXX ONE: protege mucosa y mejora sueño en reflujo nocturno; "
             "con IBP acelera respuesta (2 vs 4 semanas) y favorece adherencia.”")

    # KPIs sugeridos
    kpis = [
        "% pacientes con mejoría nocturna reportada",
        "Reducción de falla terapéutica vs monoterapia",
        "Apego a posología (1 post-comida + 1 nocturno; 30–60 min sin ingerir)",
    ]

    # Encabezado con mínimos datos de sesión
    sess = _get_session_info(session_id)
    encabezado = f"Sesión: {sess.get('timestamp') or 'N/D'} · Score: {score_14}/14 · Riesgo: {risk}"

    rh_text = textwrap.dedent(f"""\
        {encabezado}
        Fortalezas: {("; ".join(strengths) or "—")}
        Oportunidades: {("; ".join(opportunities) or "—")}
        Coaching (3): {("; ".join(coaching_3))}
        Frase guía: {frase}
        KPI: {("; ".join(kpis))}
    """).strip()

    # Versión amable (usuario)
    user_text_block = textwrap.dedent("""\
        ¡Gracias por tu tiempo! Tu explicación de ESOXX ONE va por buen camino. 
        Te sugerimos reforzar brevemente la evidencia clínica y dejar un siguiente paso concreto 
        (por ejemplo, iniciar en 1–2 pacientes con reflujo nocturno). Recuerda la posología: 
        1 stick después de cada comida y 1 antes de dormir, manteniendo 30–60 minutos sin ingerir alimentos o bebidas. 
        Estamos aquí para acompañarte en lo que necesites.
    """).strip()

    return {
        "session": {"id": sess["id"], "name": sess["name"], "email": sess["email"], "timestamp": sess["timestamp"]},
        "score_14": score_14,
        "risk": risk,
        "strengths": strengths,
        "opportunities": opportunities,
        "coaching_3": coaching_3[:3],
        "frase_guia": frase,
        "kpis": kpis,
        "rh_text": rh_text,
        "user_text": user_text_block,
    }

# ─────────── Blindaje y defaults ───────────

def _validate_internal(internal: dict, user_text: str) -> dict:
    internal = internal or {}
    # Campos legacy que tu Admin ya consume
    internal.setdefault("da_vinci_points", score_davinci_points(user_text))
    internal.setdefault("knowledge_score_legacy", f"{kw_score(user_text)}/8")
    internal.setdefault("knowledge_score_legacy_num", kw_score(user_text))
    internal.setdefault("product_claims", {"detail": {}, "product_score_total": 0})
    internal.setdefault("interaction_quality", {})
    if "kpis" not in internal:
        internal["kpis"] = {
            "avg_score": 0.0,
            "avg_phase_score_1_3": 1.0,
            "avg_steps_pct": 0.0,
            "legacy_count": kw_score(user_text),
        }
    return internal

# ─────────── Evaluador principal ───────────

def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    # Visual (opcional por bandera)
    if EVAL_ENABLE_VIDEO:
        vis_pub, vis_int, vis_pct, vis_ratio = visual_analysis(video_path)
    else:
        vis_pub, vis_int, vis_pct, vis_ratio = ('⚠️ Sin video evaluado por configuración.', 'Sin evaluación de video.', 0.0, 0.0)

    # Flags de pasos para KPI simple
    PHRASE_MAP = {
        "preparacion": ["objetivo de la visita", "propósito de la visita", "mensaje clave", "smart"],
        "apertura": ["buenos dias", "hola doctora", "principal preocupacion", "que le preocupa"],
        "persuasion": ["beneficio", "mecanismo", "estudio", "evidencia", "ibp"],
        "cierre": ["siguiente paso", "podemos acordar", "puedo contar con", "le parece si"],
        "analisis_post": ["auto-evaluacion", "proxima visita", "que aprendi"],
    }
    def step_flag(step_kw: List[str]) -> bool:
        nt = canonicalize_products(normalize(user_text))
        return any(fuzzy_contains(nt, p, 0.80) for p in step_kw)

    sales_model_flags = {step: step_flag(kws) for step, kws in PHRASE_MAP.items()}
    steps_applied_count = sum(sales_model_flags.values())
    steps_applied_pct = round(100.0 * steps_applied_count / 5.0, 1)

    # Puntuaciones propias
    weighted   = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)
    legacy_8   = kw_score(user_text)

    # Calidad + producto
    iq = interaction_quality(user_text)
    prod_detail, prod_total = product_compliance(user_text)

    # GPT (resumen + etiquetas de fases)
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Actúa como coach-evaluador senior de la industria farmacéutica (Alfasigma).
        Evalúa SOLO el texto transcrito. Clasifica cada fase del Modelo Da Vinci como
        "Excelente", "Bien" o "Necesita Mejora". Entrega JSON ESTRICTO con este formato:
        {
          "public_summary": "<máx 120 palabras, tono amable y motivador>",
          "internal_analysis": {
            "overall_evaluation": "<2-3 frases objetivas para capacitación>",
            "Modelo_DaVinci": {
              "preparacion": "Excelente|Bien|Necesita Mejora",
              "apertura": "Excelente|Bien|Necesita Mejora",
              "persuasion": "Excelente|Bien|Necesita Mejora",
              "cierre": "Excelente|Bien|Necesita Mejora",
              "analisis_post": "Excelente|Bien|Necesita Mejora"
            }
          }
        }
        """).strip()
        convo = f"--- Participante ---\n{user_text}\n--- Médico (Leo) ---\n{leo_text or '(no disponible)'}"
        completion = client.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            timeout=40,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": convo},
            ],
            temperature=0.4,
        )
        gpt_json     = json.loads(completion.choices[0].message.content or "{}")
        gpt_public   = gpt_json.get("public_summary", "")
        gpt_internal = gpt_json.get("internal_analysis", {}) or {}
        md_labels    = gpt_internal.get("Modelo_DaVinci", {}) or {}
        level = "alto"
    except Exception as e:
        log.warning("GPT fallback por error: %s", e)
        gpt_public = ("Gracias por la presentación. Refuerza la estructura Da Vinci, usa evidencia clínica puntual "
                      "y cierra con un siguiente paso claro; práctica enfocada en reflujo nocturno.")
        gpt_internal = {
            "overall_evaluation": "Evaluación limitada por conectividad; se generan métricas internas objetivas.",
            "Modelo_DaVinci": {
                "preparacion": "Necesita Mejora",
                "apertura": "Necesita Mejora",
                "persuasion": "Necesita Mejora",
                "cierre": "Necesita Mejora",
                "analisis_post": "Necesita Mejora",
            }
        }
        md_labels = gpt_internal["Modelo_DaVinci"]
        level = "error"

    # KPI promedio (1–3 → 0–10)
    MAP_Q2N = {"Excelente": 3, "Bien": 2, "Necesita Mejora": 1}
    md_scores = [MAP_Q2N.get(md_labels.get(k, "Necesita Mejora"), 1) for k in ["preparacion","apertura","persuasion","cierre","analisis_post"]]
    avg_phase_score_1_3 = round(sum(md_scores) / 5.0, 2)
    avg_score_0_10      = round((avg_phase_score_1_3 - 1) * (10 / 2), 1)

    # Compact brief (lo que RH quiere ver)
    compact = _make_compact_brief(session_id=0, user_text=user_text, md_labels=md_labels, iq=iq)  # session_id real se setea en evaluate_and_persist

    # Armado interno completo (mantiene legacy + añade compact)
    internal_summary = {
        "overall_training_summary": gpt_internal.get("overall_evaluation", ""),
        "knowledge_score_legacy": f"{legacy_8}/8",
        "knowledge_score_legacy_num": legacy_8,
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

        "product_claims": {"detail": prod_detail, "product_score_total": prod_total},
        "interaction_quality": iq,

        "active_listening_simple_detection": iq["active_listening_level"],
        "disqualifying_phrases_detected": disq_flag(user_text),

        "gpt_detailed_feedback": {
            "overall_evaluation": gpt_internal.get("overall_evaluation", ""),
            "Modelo_DaVinci": md_labels
        },

        "kpis": {
            "avg_score": avg_score_0_10,
            "avg_phase_score_1_3": avg_phase_score_1_3,
            "avg_steps_pct": steps_applied_pct,
            "legacy_count": legacy_8,
        },

        # NUEVO bloque compacto para RH
        "compact": compact
    }

    # Público (diplomático) → usa la versión amable del compacto si existe
    public_block = textwrap.dedent(f"""
        {compact['user_text'] if compact and compact.get('user_text') else gpt_public}
    """).strip()

    # Blindaje de esquema por si algo se pierde
    internal_summary = _validate_internal(internal_summary, user_text)

    return {"public": public_block, "internal": internal_summary, "level": level}

# ─────────── Persistencia ───────────

def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    res = evaluate_interaction(user_text or "", leo_text or "", video_path)
    internal = _validate_internal(res.get("internal"), user_text)

    # Rellena datos de sesión en el bloque compacto (id, name, email, timestamp)
    try:
        sess = _get_session_info(session_id)
        if "compact" in internal and isinstance(internal["compact"], dict):
            internal["compact"]["session"] = {
                "id": int(session_id),
                "name": sess.get("name"),
                "email": sess.get("email"),
                "timestamp": sess.get("timestamp"),
            }
            # Reempaqueta encabezado con fecha si hiciera falta
            # (el texto ya trae un encabezado; lo dejamos tal cual para no duplicar)
    except Exception as e:
        log.warning("No se pudo enriquecer compact.session: %s", e)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE interactions SET evaluation_rh = %s WHERE id = %s",
                (json.dumps(internal), int(session_id))
            )
        conn.commit()
    except Exception as e:
        res["level"] = "error"
        res["public"] += "\n\n⚠️ No se pudo registrar el análisis en BD."
        log.error("Persistencia evaluation_rh falló: %s", e)
    finally:
        if conn:
            conn.close()
    return res
