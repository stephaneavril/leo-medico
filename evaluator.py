# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# Guarda SIEMPRE métricas en BD cuando se llama vía evaluate_and_persist().
# Retorna: {"public": str, "internal": dict, "level": "alto"|"medio"|"bajo"|"error"}
# -------------------------------------------------------------------
import os, json, textwrap, unicodedata, re, difflib, logging
from typing import Optional, Dict, List, Tuple, Any
from urllib.parse import urlparse

# OpenCV opcional (presencia en video)
try:
    import cv2
except ImportError:
    cv2 = None

import psycopg2
from dotenv import load_dotenv

# OpenAI opcional (feedback semántico)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# ───────────────────────── Carga ENV y logging ─────────────────────────
load_dotenv()
LOG_LEVEL = os.getenv("EVAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")

OPENAI_MODEL = os.getenv("EVAL_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

client = None
if OpenAI and OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logging.warning("OpenAI client no disponible: %s", e)
        client = None

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
        r"\becox+\b", r"\beso\s*xx\b", r"\besoft\s*one\b", r"\besoxxone\b",
        r"\bays?oks?\b", r"\bays?oks?\s*one\b", r"\besok+\b",
        r"\besoxx one\b",
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

# ─────────── Scoring config (claims + Da Vinci) ───────────
WEIGHTED_KWS = {
    "3pt": [
        "esoxx-one mejora hasta 90% todos los sintomas de la erge",
        "esoxx-one reduce hasta 90% la frecuencia y severidad de los sintomas de erge",
        "esoxx-one demostro mejoria significativa de los sintomas esofagicos y extraesofagicos de la erge",
        "esoxx-one mas ibp es significativamente mas eficaz que la monoterapia con ibp para la epitelizacion esofagica",
        "reduce significativamente los sintomas de la erge vs monoterapia ibps",
        "alivio en menor tiempo (2 semanas vs 4 semanas)",
        "reduccion del uso de antiacidos",
        "demostrado en ninos y adolescentes",
        "reduce la falla al tratamiento",
    ],
    "2pt": [
        "protege y repara mucosa esofagica",
        "protege y promueve la reparacion de la mucosa esofagica",
        "barrera bioadhesiva",
        "combinacion de 3 activos",
        "acido hialuronico",
        "sulfato de condroitina",
        "poloxamero 407",
        "recubre el epitelio esofagico",
        "liquido a temperatura ambiente y en estado gel a temperatura corporal",
        "un sobre despues de cada comida y antes de dormir",
        "esperar por lo menos 30min despues sin tomar alimentos o bebidas",
        "esperar 60min",
        "esperar 1hr",
    ],
    "1pt": [
        "mecanismo de proteccion original e innovador para el manejo de la erge",
        "alivia los sintomas del erge",
        "esoxx-one",
        "forma un complejo macromolecular que recubre la mucosa esofagica",
        "actua como barrera mecanica contra componentes nocivos del reflujo",
        "mejora la calidad de vida de los pacientes",
        "unico",
    ],
}

DAVINCI_POINTS = {
    "preparacion": {
        2: ["objetivo smart", "mi objetivo hoy es", "metas smart"],
        1: [
            "objetivo de la visita", "propósito de la visita", "mensaje clave",
            "hoy quiero", "plan para hoy", "materiales", "presentacion preparada"
        ],
    },
    "apertura": {
        2: [
            "cuales son las mayores preocupaciones", "principal preocupacion",
            "que caracteristicas tienen sus pacientes", "visita anterior",
            "me gustaria conocer", "que es lo que mas le preocupa"
        ],
        1: [
            "buenos dias", "buen dia", "hola doctora", "mi nombre es",
            "como ha estado", "gracias por su tiempo"
        ],
    },
    "persuasion": {
        2: [
            "que caracteristicas considera ideales", "objetivos de tratamiento",
        #   "combinado con ibp",              # se cubre abajo también
            "sinergia con inhibidores de la bomba de protones",
            "mecanismo", "beneficio", "evidencia", "estudio",
            "tres componentes", "acido hialuronico", "condroitin", "poloxamero",
        ]
    },
    "cierre": {
        2: [
            "con base a lo dialogado considera que esoxx-one", "podria empezar con algun paciente",
            "le parece si iniciamos", "empezar a considerar algun paciente",
            "podemos acordar un siguiente paso", "puedo contar con su apoyo",
        ],
        1: ["siguiente paso", "podemos acordar", "puedo contar con", "le parece si"],
    },
    "analisis_post": {
        2: ["auto-evaluacion", "plan para proxima visita", "que mejoraria para la proxima"],
        1: ["objeciones", "proxima visita", "actualiza", "que aprendi"],
    },
}

KW_LIST = ["beneficio", "estudio", "sintoma", "tratamiento", "reflujo", "mecanismo", "eficacia", "seguridad"]
BAD_PHRASES = ["no se", "no tengo idea", "lo invento", "no lo estudie", "no estudie bien", "no conozco", "no me acuerdo"]
LISTEN_KW  = [
    "entiendo", "comprendo", "veo que", "lo que dices", "si entiendo bien", "parafraseando",
    "que le preocupa", "me gustaria conocer", "que caracteristicas tienen", "podria contarme", "como describe a sus pacientes"
]

PRODUCT_RUBRIC: Dict[str, Dict[str, List[str] | int]] = {
    "mecanismo": {
        "weight": 2,
        "phrases": [
            "barrera bioadhesiva", "recubre el epitelio esofagico", "actua como barrera mecanica",
            "poloxamero 407", "acido hialuronico", "sulfato de condroitina", "tres componentes",
            "dispositivo medico", "sin interacciones con medicamentos", "actua en el esofago",
        ],
    },
    "eficacia": {
        "weight": 3,
        "phrases": [
            "mejora hasta 90% todos los sintomas", "reduce hasta 90% la frecuencia y severidad",
            "alivio en menor tiempo", "reduce la falla al tratamiento", "reduce significativamente los sintomas",
            "sinergia con inhibidores de la bomba de protones", "ibp mas esoxx-one",
        ],
    },
    "evidencia": {
        "weight": 2,
        "phrases": [
            "demostrado en ninos y adolescentes", "estudio", "evidencia", "mejoria significativa",
            "reduccion del uso de antiacidos", "epitelizacion esofagica",
        ],
    },
    "uso_posologia": {
        "weight": 2,
        "phrases": [
            "un sobre despues de cada comida y antes de dormir",
            "esperar por lo menos 30min", "esperar 60min", "esperar 1hr",
            "liquido a temperatura ambiente y en estado gel a temperatura corporal",
            "formar un gel en el esofago",
        ],
    },
    "diferenciales": {
        "weight": 1,
        "phrases": [
            "combinacion de 3 activos", "mecanismo de proteccion original", "unico",
            "mejora la calidad de vida", "sin eventos adversos", "no se absorbe sistemicamente",
        ],
    },
    "mensajes_base": {"weight": 1, "phrases": ["esoxx-one", "reflujo", "erge", "sintomas"]},
}

# ─────────── Scorers (reglas) ───────────
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
    # Señal simple de pasos (para compatibilidad con admin summaries)
    PHRASE_MAP = {
        "preparacion": ["objetivo de la visita", "propósito", "mensaje clave", "smart"],
        "apertura": ["buenos dias", "pacientes", "necesidades", "que le preocupa"],
        "persuasion": ["beneficio", "mecanismo", "estudio", "evidencia", "combinado con ibp"],
        "cierre": ["siguiente paso", "podemos acordar", "puedo contar con", "le parece si"],
        "analisis_post": ["auto-evaluacion", "proxima visita", "que aprendi"],
    }
    step_flags = {k: any(fuzzy_contains(nt, p, 0.80) for p in v) for k, v in PHRASE_MAP.items()}
    steps_applied_count = sum(step_flags.values())
    return {
        "length_tokens": length,
        "question_rate": question_rate,
        "closing_present": closing,
        "objection_handling_signal": objections,
        "active_listening_level": listen_level,
        "da_vinci_step_flags": {
            "flags": step_flags,
            "steps_applied_count": f"{steps_applied_count}/5"
        }
    }

# ─────────── Visual express ───────────
def visual_analysis(path: Optional[str]):
    if not (path and cv2 and os.path.exists(path)):
        return "⚠️ Sin video disponible.", "No evaluado", "N/A", None
    MAX_FRAMES = int(os.getenv("MAX_FRAMES_TO_CHECK", 60))
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return "⚠️ No se pudo abrir video.", "Error video", "N/A", None
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
            return "⚠️ Sin frames para analizar.", "Sin frames", "0.0%", 0.0
        ratio = frontal / total
        pct = f"{ratio*100:.1f}%"
        if ratio >= 0.7:
            msg = "✅ Buena presencia frente a cámara."
            tag = "Correcta"
        elif ratio > 0:
            msg = "⚠️ Mejora la visibilidad."
            tag = "Mejorar visibilidad"
        else:
            msg = "❌ No se detectó rostro."
            tag = "No detectado"
        return msg, tag, pct, ratio
    except Exception as e:
        return f"⚠️ Error visual: {e}", "Error video", "N/A", None

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

# ─────────── Helpers de puntuación compuesta ───────────
def pct_label(p: float) -> str:
    if p >= 71: return "Alto"
    if p >= 31: return "Medio"
    return "Bajo"

def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0

# ─────────── OpenAI (opcional) ───────────
def gpt_semantic_feedback(user_text: str) -> Optional[dict]:
    """Devuelve un dict con evaluación por fases (0–5) y comentarios.
       Si no hay OpenAI o falla, devuelve None.
    """
    if not client:
        return None
    prompt = f"""
Eres un evaluador de ventas médicas. Analiza la transcripción (español) de un representante que visita a un médico.
Devuelve JSON compacto con esta forma (sin texto extra):
{{
  "Modelo_DaVinci": {{
    "preparacion": {{"score": 0-5, "comment": "..." }},
    "apertura":    {{"score": 0-5, "comment": "..." }},
    "persuasion":  {{"score": 0-5, "comment": "..." }},
    "cierre":      {{"score": 0-5, "comment": "..." }},
    "analisis_post": {{"score": 0-5, "comment": "..." }}
  }},
  "Areas_de_mejora": ["...", "..."],
  "Siguientes_pasos": ["...", "..."],
  "overall_evaluation": "1-2 frases para RH resumiendo desempeño"
}}
Texto:
\"\"\"{user_text[:24000]}\"\"\"
"""
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        content = resp.choices[0].message.content.strip()
        # Extrae JSON
        data = json.loads(content)
        return data
    except Exception as e:
        logging.warning("GPT feedback no disponible: %s", e)
        return None

# ─────────── Evaluador principal ───────────
def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, Any]:
    # 1) Visual
    vis_pub, vis_int, vis_pct, vis_ratio = visual_analysis(video_path)

    # 2) Reglas
    weighted    = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)  # total típico ~0–8 aprox (según tus reglas)
    legacy_8    = kw_score(user_text)              # 0–8
    iq          = interaction_quality(user_text)
    prod_detail, prod_total = product_compliance(user_text)

    # 3) OpenAI semántico (opcional)
    gpt_fb = gpt_semantic_feedback(user_text)

    # 4) Normalizaciones (0–100)
    #   - Modelo de ventas:
    #       a) Reglas DaVinci total normalizado a 0–100 (techo 8)
    dv_norm_rules = min(davinci_pts.get("total", 0), 8) / 8.0 * 100.0
    #       b) Si hay GPT, usa promedio de scores (0–5) → 0–100
    if gpt_fb and "Modelo_DaVinci" in gpt_fb:
        md = gpt_fb["Modelo_DaVinci"]
        scores = [safe_div(md.get(k, {}).get("score", 0), 5) * 100.0 for k in ["preparacion","apertura","persuasion","cierre","analisis_post"]]
        dv_norm_gpt = sum(scores) / max(1, len(scores))
        modelo_ventas_pct = 0.7 * dv_norm_gpt + 0.3 * dv_norm_rules
    else:
        modelo_ventas_pct = dv_norm_rules

    #   - Conocimiento producto: mezcla legacy y weighted/product rubric
    legacy_pct = (legacy_8 / 8.0) * 100.0
    # product rubric total máx: suma de weights
    max_prod = sum(int(cfg["weight"]) for cfg in PRODUCT_RUBRIC.values())
    prod_pct = safe_div(prod_total, max_prod) * 100.0
    # weighted_kws: no tiene techo natural; hacemos un cap razonable 24 puntos = 100%
    weighted_cap = min(int(weighted["total_points"]), 24)
    weighted_pct = (weighted_cap / 24.0) * 100.0
    conocimiento_pct = 0.5 * legacy_pct + 0.3 * prod_pct + 0.2 * weighted_pct

    #   - Interacción (escucha, preguntas, cierre)
    listen_map = {"Baja": 30, "Moderada": 65, "Alta": 90}
    listen_pct = listen_map.get(iq.get("active_listening_level","Baja"), 30)
    closing_pct = 85 if iq.get("closing_present") else 40
    question_rate = iq.get("question_rate", 0.0)  # % de tokens con "?"
    # si pregunta entre 0.8% y 3% de los tokens → óptimo; fuera penaliza
    if question_rate <= 0.2:
        qrate_pct = 40
    elif question_rate <= 0.8:
        qrate_pct = 70
    elif question_rate <= 3.0:
        qrate_pct = 90
    else:
        qrate_pct = 60
    interaccion_pct = round(0.5 * listen_pct + 0.3 * closing_pct + 0.2 * qrate_pct, 1)

    #   - Visual
    if vis_ratio is None:
        visual_pct = 50  # desconocido → neutro
    else:
        # lineal: 0.0 → 20 ; 0.7 → 95 ; >0.9 clamp 100
        base = 20 + (max(0.0, min(1.0, vis_ratio)) * 100)
        visual_pct = max(20, min(100, base if vis_ratio <= 0.9 else 100))

    # 5) Composite (40/30/20/10)
    composite = round(0.4 * modelo_ventas_pct + 0.3 * conocimiento_pct + 0.2 * interaccion_pct + 0.1 * visual_pct, 1)
    level_lbl = pct_label(composite)

    # 6) Flags y notas
    red_flag = disq_flag(user_text)

    # 7) Resumen narrativo + Tips
    fortalezas = []
    debilidades = []
    if modelo_ventas_pct >= 70: fortalezas.append("Aplicación sólida del modelo de ventas (flujo ordenado).")
    else: debilidades.append("Estructura de la visita mejorable (preparación/apertura/cierre incompletos).")

    if conocimiento_pct >= 70: fortalezas.append("Buen manejo de mensaje de producto y evidencia clave.")
    else: debilidades.append("Profundizar en evidencia y posología para dar mayor confianza clínica.")

    if interaccion_pct >= 70: fortalezas.append("Escucha activa y ritmo de preguntas adecuado.")
    else: debilidades.append("Mejorar escucha/parafraseo y plantear un siguiente paso claro.")

    if visual_pct >= 70: fortalezas.append("Presencia correcta frente a cámara.")
    else: debilidades.append("Ajustar encuadre/iluminación para mayor presencia visual.")

    if red_flag: debilidades.append("Evitar expresiones de desconocimiento no profesional (e.g., 'no sé').")

    def join_bullets(items: List[str]) -> str:
        return " • " + " • ".join(items) if items else " • Sin observaciones destacadas."

    overall_training_summary = (
        f"Nivel general **{level_lbl}** ({composite}/100). "
        f"Fortalezas:{join_bullets(fortalezas)} "
        f"Áreas de mejora:{join_bullets(debilidades)}"
    )

    # Tip breve y accionable
    if debilidades:
        tip = debilidades[0].replace("Áreas de mejora: ", "")
    else:
        tip = "Mantén el enfoque: cierra con un siguiente paso claro y medible."

    # 8) Bloque público breve (lo sobreescribe el worker en DB)
    public = f"Desempeño {level_lbl}. Recomendación: {tip}"

    # 9) Empaque de métricas para admin (compatibles con tu UI)
    internal: Dict[str, Any] = {
        "overall_training_summary": overall_training_summary,
        "gpt_detailed_feedback": None,  # se llena si hay GPT
        "da_vinci_points": davinci_pts,  # reglas
        "knowledge_score_legacy": f"{legacy_8}/8",
        "knowledge_score_legacy_num": legacy_8,
        "knowledge_weighted_total_points": weighted["total_points"],
        "knowledge_weighted_breakdown": weighted["breakdown"],
        "product_claims": {
            "detail": prod_detail,
            "product_score_total": prod_total
        },
        "interaction_quality": iq,
        "active_listening_simple_detection": iq.get("active_listening_level"),
        "visual_presence": vis_int,
        "visual_percentage": vis_pct,
        "disqualifying_phrases_detected": red_flag,
        "kpis": {
            # KPIs compuestos útiles para ranking
            "modelo_ventas_pct": round(modelo_ventas_pct, 1),
            "conocimiento_pct": round(conocimiento_pct, 1),
            "interaccion_pct": round(interaccion_pct, 1),
            "visual_pct": round(visual_pct, 1),
            "avg_score": composite,              # <- usado en tu admin
            "avg_phase_score_1_3": round((legacy_pct + prod_pct + weighted_pct) / 3.0, 1),
            "avg_steps_pct": round(safe_div(
                int(iq["da_vinci_step_flags"]["steps_applied_count"].split("/")[0]), 5
            ) * 100.0, 1),
            "legacy_count": legacy_8
        }
    }

    if gpt_fb:
        # Ensamblar bloques que tu template espera
        md = gpt_fb.get("Modelo_DaVinci", {})
        internal["gpt_detailed_feedback"] = {
            "Modelo_DaVinci": {
                "preparacion": f'{md.get("preparacion",{}).get("score",0)}/5 – {md.get("preparacion",{}).get("comment","")}',
                "apertura":    f'{md.get("apertura",{}).get("score",0)}/5 – {md.get("apertura",{}).get("comment","")}',
                "persuasion":  f'{md.get("persuasion",{}).get("score",0)}/5 – {md.get("persuasion",{}).get("comment","")}',
                "cierre":      f'{md.get("cierre",{}).get("score",0)}/5 – {md.get("cierre",{}).get("comment","")}',
                "analisis_post": f'{md.get("analisis_post",{}).get("score",0)}/5 – {md.get("analisis_post",{}).get("comment","")}',
            },
            "Areas_de_mejora": gpt_fb.get("Areas_de_mejora", []),
            "overall_evaluation": gpt_fb.get("overall_evaluation", "")
        }
        # Siguientes pasos para lista en admin (si lo usas)
        internal["follow_up_suggestions"] = gpt_fb.get("Siguientes_pasos", [])

    # 10) Nivel para retorno
    level = level_lbl.lower()

    return {
        "public": public,
        "internal": internal,
        "level": level
    }

# ─────────── Persistencia ───────────
def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str]) -> Dict[str, Any]:
    """Evalúa y guarda en BD el bloque interno (RH). El worker actualizará el público."""
    try:
        res = evaluate_interaction(user_text or "", leo_text or "", video_path)
    except Exception as e:
        logging.exception("Error evaluando interacción: %s", e)
        return {"public": "⚠️ Error en evaluación.", "internal": {"error": str(e)}, "level": "error"}

    internal = res.get("internal", {})
    public = res.get("public", "")
    tip = "Consejo pendiente."
    visual_feedback = internal.get("visual_presence", "")

    # Tip breve (si existe recomendación más concreta)
    if internal.get("follow_up_suggestions"):
        tip = str(internal["follow_up_suggestions"][0])[:240]
    else:
        # Usa el tip generado arriba (derivado de áreas de mejora)
        tip = public.replace("Desempeño", "").strip()
        if len(tip) > 240:
            tip = tip[:240]

    # Persistir en BD
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE interactions
               SET evaluation_rh = %s,
                   tip = %s,
                   visual_feedback = %s,
                   visible_to_user = FALSE
             WHERE id = %s;
            """,
            (json.dumps(internal, ensure_ascii=False), tip, visual_feedback, session_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.exception("No se pudo guardar evaluation_rh en BD: %s", e)

    return res
