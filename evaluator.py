# evaluator.py — versión dinámica con IA + fallback (drop-in)
# -------------------------------------------------------------------
# Analiza una simulación Representante (usuario) ↔ Médico (LEO, avatar)
# Guarda SIEMPRE métricas en BD cuando se llama vía evaluate_and_persist().
# Retorna: {"public": str, "internal": dict, "level": "alto"|"error"}
# -------------------------------------------------------------------

from __future__ import annotations
import os, json, textwrap, unicodedata, re, difflib, random
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

# OpenCV opcional (presencia en video)
try:
    import cv2
except ImportError:
    cv2 = None

import psycopg2
from dotenv import load_dotenv

# OpenAI opcional (resumen semántico)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_openai = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI) else None

EVAL_VERSION = "LEO-eval-v3.3"  # ↑ sube la versión para verificar en logs/JSON
print(f"[EVAL] Loaded evaluator version: {EVAL_VERSION}")

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
        r"\becox+\b", r"\beso\s*xx\b", r"\besoxxone\b", r"\besoft\s*one\b",
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
        "efecto nocturno reflujo nocturno",
        "evita agresion por pepsina",
    ],
    "2pt": [
        "protege y repara mucosa esofagica",
        "barrera bioadhesiva",
        "combinacion de 3 activos",
        "acido hialuronico", "sulfato de condroitina", "poloxamero 407",
        "recubre el epitelio esofagico",
        "liquido a temperatura ambiente y en estado gel a temperatura corporal",
        "un sobre despues de cada comida y antes de dormir",
        "esperar por lo menos 30min despues sin tomar alimentos o bebidas",
        "esperar 60min", "esperar 1hr",
        "bioadhesivo", "barrera bioadhesiva esofagica",
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
        1: ["objetivo de la visita", "proposito de la visita", "mensaje clave", "hoy quiero", "plan para hoy", "materiales"],
    },
    "apertura": {
        2: ["cuales son las mayores preocupaciones",  "cuales sintomas afectan mas la calidad de vida", "principal preocupacion", "que caracteristicas tienen sus pacientes", "visita anterior", "me gustaria conocer", "que es lo que mas le preocupa", "que es lo que mas le preocupa al tratar un paciente con reflujo"],
        1: ["buenos dias", "buen dia", "hola doctora", "mi nombre es", "como ha estado", "gracias por su tiempo"],
    },
    "persuasion": {
        2: ["que caracteristicas considera ideales", "objetivos de tratamiento", "combinado con ibp", "sinergia con inhibidores de la bomba de protones", "mecanismo", "beneficio", "evidencia", "estudio", "tres componentes", "acido hialuronico", "condroitin", "poloxamero",  "que esquema de tratamiento normalmente utiliza", "que busca en un tratamiento ideal"],
    },
    "cierre": {
        2: ["con base a lo dialogado considera que esoxx-one", "que fue lo que mas le atrajo de esoxx-one", "podria empezar con algun paciente", "le parece si iniciamos", "ya tiene en mente algun paciente para iniciar", "empezar a considerar algun paciente", "podemos acordar un siguiente paso", "puedo contar con su apoyo"],
        1: ["siguiente paso", "podemos acordar", "puedo contar con", "le parece si"],
    },
    "analisis_post": {
        2: ["auto-evaluacion", "plan para proxima visita", "que mejoraria para la proxima"],
        1: ["objeciones", "proxima visita", "actualiza", "que aprendi"],
    },
}

KW_LIST = ["beneficio", "estudio", "sintoma", "tratamiento", "reflujo", "mecanismo", "eficacia", "seguridad"]
BAD_PHRASES = ["no se", "no tengo idea", "lo invente", "no lo estudie", "no estudie bien", "no conozco", "no me acuerdo"]
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
            "evita agresion por pepsina", "bioadhesivo",
        ],
    },
    "eficacia": {
        "weight": 3,
        "phrases": [
            "mejora hasta 90% todos los sintomas", "reduce hasta 90% la frecuencia y severidad",
            "alivio en menor tiempo", "reduce la falla al tratamiento", "reduce significativamente los sintomas",
            "sinergia con inhibidores de la bomba de protones", "ibp mas esoxx-one",
            "2 semanas vs 4 semanas", "efecto nocturno reflujo nocturno",
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
            "un sobre despues de cada comida", "un sobre antes de dormir",
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

# ─────────── Da Vinci CHECKLIST (PASO 2–4) ───────────
# Cada sub-ítem se evalúa por presencia (1 punto) con fuzzy matching.
DA_VINCI_CHECKLIST = {
    "apertura": {  # PASO 2
        "vinculo_seguimiento": [
            "retomar la visita pasada", "dar seguimiento a la visita",
            "seguimiento de la visita anterior", "como le fue con", "desde la ultima vez"
        ],
        "perfil_paciente_preguntas": [
            "caracteristicas de sus pacientes", "que caracteristicas tienen sus pacientes",
            "necesidades del paciente", "perfil del paciente", "como describe a sus pacientes"
        ],
    },
    "persuasion": {  # PASO 3
        "preguntas_objetivos_tratamiento": [
            "objetivos de tratamiento", "que busca en un tratamiento",
            "criterios ideales", "metas clinicas", "que espera del tratamiento"
        ],
        "materiales_y_evidencia": [
            "materiales promocionales", "estudio", "evidencia", "datos clinicos",
            "argumento cientifico", "publicacion", "referencia clinica"
        ],
        "resolucion_objeciones": [
            "objecion", "preocupacion", "duda", "reserva", "entiendo su preocupacion",
            "si entiendo bien", "parafraseando"
        ],
    },
    "cierre": {  # PASO 4
        "parafraseo_y_resumen_beneficios": [
            "parafrase", "si entiendo bien", "lo que mas le interesa es",
            "resume beneficios", "con base a lo dialogado"
        ],
        "mensajes_clave": [
            "mensaje clave", "puntos clave", "en resumen", "lo mas importante"
        ],
        "solicitud_inclusion_y_pasos": [
            "le parece si iniciamos", "podemos acordar un siguiente paso",
            "puedo contar con", "paciente candidato", "proxima visita", "seguimiento en"
        ],
        "promociones_vigentes": [
            "promocion", "descuento", "oferta en farmacia", "condiciones comerciales"
        ],
    },
}

# ─────────── Scorers ───────────

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

def score_da_vinci_checklist(t: str) -> dict:
    """
    Devuelve un dict con:
      {
        'apertura': {'total': int, 'hits': {subitem: [frases_detectadas]}},
        'persuasion': {...}, 'cierre': {...},
        'totales': {'apertura': X, 'persuasion': Y, 'cierre': Z, 'global': X+Y+Z, 'max': MAX}
      }
    """
    nt = canonicalize_products(normalize(t))
    out = {}
    global_total = 0
    global_max = 0

    for paso, items in DA_VINCI_CHECKLIST.items():
        paso_total = 0
        paso_max = 0
        hits_map = {}
        for subitem, phrases in items.items():
            paso_max += 1
            sub_hits = [p for p in phrases if fuzzy_contains(nt, p, 0.80)]
            if sub_hits:
                paso_total += 1
            hits_map[subitem] = sub_hits
        out[paso] = {"total": paso_total, "hits": hits_map, "max": paso_max}
        global_total += paso_total
        global_max += paso_max

    out["totales"] = {
        "apertura": out["apertura"]["total"],
        "persuasion": out["persuasion"]["total"],
        "cierre": out["cierre"]["total"],
        "global": global_total,
        "max": global_max,
    }
    return out

# ─────────── Visual express ───────────

def visual_analysis(path: str):
    # Se conserva en interno; no se menciona en el resumen de usuario
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

# ─────────── Helpers dinámicos ───────────

def _risk_from_score(score_14: int) -> str:
    if score_14 <= 4:
        return "ALTO"
    if score_14 <= 9:
        return "MEDIO"
    return "BAJO"

def _top_hits(detail: Dict[str, dict], k: int = 2) -> List[str]:
    scored = [(cat, d.get("score", 0), d.get("hits", [])) for cat, d in detail.items()]
    scored = [x for x in scored if x[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    out = []
    for cat, _, hits in scored[:k]:
        if hits:
            out.append(f"{cat.replace('_',' ')}: " + ", ".join(hits[:2]))
    return out

def _phrase_guide(iq: dict, prod_total: int, closing_present: bool) -> str:
    pool = [
        "Propón: '¿Le parece iniciar ESOXX-ONE en 1 paciente candidato y revisamos en 2 semanas?'",
        "Cierra así: 'Acordemos un siguiente paso: pruebe ESOXX-ONE con un caso y validamos en la próxima visita.'",
        "Sugerencia: 'Si ve utilidad, empecemos con un paciente nocturno/refractario y damos seguimiento.'",
        "Alternativa: '¿Le parece dejar 3 muestras y programar retroalimentación en 14 días?'",
    ]
    if closing_present:
        return "Refuerza el seguimiento: fija fecha y solicita retro clínica (síntomas + adherencia)."
    if prod_total == 0:
        return "Cierra con acuerdo explícito (paciente candidato + fecha de seguimiento)."
    return random.choice(pool)

def _build_compact(user_text: str, internal: dict) -> dict:
    """Arma el bloque dinámico para el admin (RH)."""
    legacy_8 = internal.get("knowledge_score_legacy_num") or 0
    davinci = internal.get("da_vinci_points", {}) or {}
    iq = internal.get("interaction_quality", {}) or {}
    prod = (internal.get("product_claims") or {}).get("detail", {})
    prod_total = (internal.get("product_claims") or {}).get("product_score_total", 0)

    avg_phase_1_3 = (internal.get("kpis") or {}).get("avg_phase_score_1_3", 1.0)
    phase_0_6 = max(0, min(6, round((avg_phase_1_3 - 1) * (6 / 2))))
    score_14 = int(max(0, min(14, legacy_8 + phase_0_6)))
    risk = _risk_from_score(score_14)

    strengths = []
    if prod_total >= 2:
        strengths.append("Mecanismo/beneficios explicados con señales de producto")
    if iq.get("active_listening_level") in ("Alta", "Moderada"):
        strengths.append(f"Escucha activa {iq['active_listening_level'].lower()}")
    if davinci.get("persuasion", 0) >= 2:
        strengths.append("Persuasión con enfoque clínico")
    if iq.get("closing_present"):
        strengths.append("Cierre con siguiente paso")
    strengths += _top_hits(prod, 1)

    opportunities = []
    if legacy_8 < 5:
        opportunities.append("Refuerza mensajes clave y evidencia clínica")
    if not iq.get("closing_present"):
        opportunities.append("Asegura un cierre con acuerdo concreto")
    if (davinci.get("apertura", 0) + davinci.get("persuasion", 0)) < 3:
        opportunities.append("Mayor exploración clínica y beneficio para el caso del médico")
    if davinci.get("preparacion", 0) < 2:
        opportunities.append("Define un objetivo SMART antes de la visita")

    coaching_3 = []
    if "Asegura un cierre con acuerdo concreto" in opportunities and not iq.get("closing_present"):
        coaching_3.append("Ensaya 2 cierres distintos y elige uno según la conversación")
    if legacy_8 < 5:
        coaching_3.append("Integra 2–3 frases clínicas con respaldo de estudio")
    if (davinci.get("apertura", 0) < 2):
        coaching_3.append("Prepara 3 preguntas de apertura para perfilar al paciente")
    if not coaching_3:
        coaching_3 = ["Mantén objetivo SMART", "Sostén 2 frases clínicas", "Cierra con acuerdo fechado"]

    frase_guia = _phrase_guide(iq, prod_total, iq.get("closing_present", False))

    kpis = [
        f"Score 0–14: {score_14}",
        f"Escucha activa: {iq.get('active_listening_level','N/D')}",
        f"Fases Da Vinci: {davinci.get('total',0)} señales",
    ]

    rh_text = (
        f"Sesión: score {score_14}/14; riesgo {risk}. "
        f"Fortalezas: {', '.join(strengths) or '—'}. "
        f"Oportunidades: {', '.join(opportunities) or '—'}. "
        f"Coaching: {', '.join(coaching_3)}."
    )

    analysis_ia = internal.get("compact", {}).get("analysis_ia", "")

    user_text_summary = (
        "Buen avance. Refuerza evidencia concreta y asegura un siguiente paso con fecha. "
        "Lleva 3 preguntas de apertura para perfilar mejor al paciente."
    )

    return {
        "score_14": score_14,
        "risk": risk,
        "strengths": strengths,
        "opportunities": opportunities,
        "coaching_3": coaching_3,
        "frase_guia": frase_guia,
        "kpis": kpis,
        "rh_text": rh_text,
        "analysis_ia": analysis_ia,
        "user_text": user_text_summary,
    }

# ─────────── Evaluador principal ───────────

def _validate_internal(internal: dict, user_text: str) -> dict:
    """Blinda el JSON interno para el admin (si algo falló)."""
    internal = internal or {}
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
    if "compact" not in internal:
        internal["compact"] = _build_compact(user_text, internal)
    return internal

def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    # Análisis visual (solo para interno; NO se menciona en el público)
    vis_pub, vis_int, vis_pct = (
        visual_analysis(video_path)
        if video_path and cv2 and os.path.exists(video_path)
        else ("", "No evaluado", "N/A")
    )

    # Señales Da Vinci (% aplicado)
    PHRASE_MAP = {
        "preparacion": ["objetivo de la visita", "proposito de la visita", "mensaje clave", "smart", "objetivo smart", "mi objetivo hoy es", "plan para hoy", "materiales"],
        "apertura": ["buenos dias", "buen dia", "hola doctora", "como ha estado", "pacientes", "necesidades", "visita anterior", "principal preocupacion", "que le preocupa", "que caracteristicas tienen"],
        "persuasion": ["objetivos de tratamiento", "beneficio", "mecanismo", "estudio", "evidencia", "caracteristicas del producto", "combinado con ibp", "inhibidores de la bomba de protones"],
        "cierre": ["siguiente paso", "podemos acordar", "cuento con usted", "puedo contar con", "le parece si", "empezar a considerar"],
        "analisis_post": ["auto-evaluacion", "objeciones", "proxima visita", "actualiza", "que aprendi"],
    }
    def step_flag(step_kw: List[str]) -> bool:
        nt = canonicalize_products(normalize(user_text))
        return any(fuzzy_contains(nt, p, 0.80) for p in step_kw)

    sales_model_flags = {step: step_flag(kws) for step, kws in PHRASE_MAP.items()}
    steps_applied_count = sum(sales_model_flags.values())
    steps_applied_pct = round(100.0 * steps_applied_count / 5.0, 1)

    # Puntuaciones propias
    weighted    = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)
    legacy_8    = kw_score(user_text)

    # Checklist Da Vinci PASO 2–4 (Apertura, Persuasión, Cierre)
    davinci_check = score_da_vinci_checklist(user_text)

    # KPI de fases (0–10)
    if davinci_check["totales"]["max"] > 0:
        kpi_fases_0_10 = round(
            10.0 * davinci_check["totales"]["global"] / davinci_check["totales"]["max"], 1
        )
    else:
        kpi_fases_0_10 = 0.0

    # Calidad + producto
    iq = interaction_quality(user_text)
    prod_detail, prod_total = product_compliance(user_text)

    # Señal de bajo diálogo
    nt = normalize(user_text)
    min_tokens = 25
    min_signals = (iq["question_rate"] > 0.15) or (steps_applied_count >= 2)
    low_dialogue_note = (len(nt.split()) < min_tokens) or not min_signals

    # ===== IA: resumen para USUARIO + análisis_ia para RH =====
    gpt_public = ""
    analysis_ia = ""
    level = "alto"

    if _openai:
        try:
            SYSTEM_PROMPT = textwrap.dedent("""
            Actúas como coach-evaluador senior en una simulación de visita médica.
            El avatar LEO representa al MÉDICO. El PARTICIPANTE es el representante.
            Devuelve JSON EXACTO con:
            {
              "public_summary": "<máx 100 palabras, tono diplomático, explica a la PERSONA qué hizo bien, qué faltó y cómo mejorar. Evita frases genéricas.>",
              "analysis_ia": "<1 frase objetiva para Capacitación (RH) sobre el desempeño global>"
            }
            Foco: claridad clínica, evidencia, posología, escucha activa, cierre con acuerdo.
            """)
            convo = f"--- Representante (tú) ---\n{user_text}\n--- Médico (LEO) ---\n{leo_text or '(no disponible)'}"
            completion = _openai.chat.completions.create(
                model=os.getenv("OPENAI_GPT_MODEL", "gpt-4o-mini"),
                response_format={"type": "json_object"},
                timeout=40,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({
                        "dialogue": convo,
                        "signals": {
                            "active_listening": iq.get("active_listening_level"),
                            "closing_present": iq.get("closing_present", False),
                            "low_dialogue": low_dialogue_note
                        }
                    })},
                ],
                temperature=float(os.getenv("GPT_TEMPERATURE", "0.6")),
            )
            j = json.loads(completion.choices[0].message.content)
            gpt_public = j.get("public_summary", "").strip()
            analysis_ia = j.get("analysis_ia", "").strip()
        except Exception:
            level = "error"

    # Fallbacks si la IA no respondió
    if not gpt_public:
        tail = "Asegura un cierre con acuerdo y fecha." if not iq.get("closing_present") else "Buen cierre; agenda seguimiento específico."
        gpt_public = (
            "Buen ejercicio: mantuviste el hilo clínico y explicaste el mecanismo con claridad. "
            "Refuerza evidencia con 2–3 frases de estudio y vincula el beneficio al caso del médico. "
            + tail
        )
    if not analysis_ia:
        approx_total = davinci_pts.get("total", 0) + (2 if iq.get("closing_present") else 0)
        if approx_total <= 4:
            analysis_ia = "Desempeño limitado: baja evidencia y ausencia de cierre; requiere trabajar estructura y mensajes clave."
        elif approx_total <= 8:
            analysis_ia = "Desempeño intermedio: transmite ideas clave pero falta sustento clínico y cierre consistente."
        else:
            analysis_ia = "Desempeño sólido: buen hilo clínico y señales de cierre; mantener consistencia."

    # KPIs cualitativos simples
    MAP_Q2N = {"Excelente": 3, "Bien": 2, "Necesita Mejora": 1}
    qualitative = {
        "preparacion": "Bien" if sales_model_flags["preparacion"] else "Necesita Mejora",
        "apertura": "Bien" if sales_model_flags["apertura"] else "Necesita Mejora",
        "persuasion": "Bien" if davinci_pts.get("persuasion",0)>=2 else "Necesita Mejora",
        "cierre": "Bien" if iq.get("closing_present") else "Necesita Mejora",
        "analisis_post": "Bien" if sales_model_flags["analisis_post"] else "Necesita Mejora",
    }
    md_scores = [MAP_Q2N.get(v,1) for v in qualitative.values()]
    avg_phase_score_1_3 = round(sum(md_scores)/5.0, 2)
    avg_score_0_10      = round((avg_phase_score_1_3 - 1) * (10 / 2), 1)

    internal_summary = {
        "overall_training_summary": "Síntesis automática basada en señales del diálogo.",
        "knowledge_score_legacy": f"{legacy_8}/8",
        "knowledge_score_legacy_num": legacy_8,
        "knowledge_weighted_total_points": weighted["total_points"],
        "knowledge_weighted_breakdown": weighted["breakdown"],

        "visual_presence": vis_int,
        "visual_percentage": vis_pct,

        "da_vinci_step_flags": {
            **{k: ("✅" if v else "❌") for k, v in sales_model_flags.items() },
            "steps_applied_count": f"{steps_applied_count}/5",
            "steps_applied_pct": steps_applied_pct,
        },
        "da_vinci_points": davinci_pts,

        "product_claims": {"detail": prod_detail, "product_score_total": prod_total},
        "interaction_quality": iq,

        # Checklist Da Vinci PASO 2–4
        "davinci_checklist": davinci_check,

        "active_listening_simple_detection": iq["active_listening_level"],
        "disqualifying_phrases_detected": disq_flag(user_text),

        "gpt_detailed_feedback": {"Modelo_DaVinci": qualitative},
        "kpis": {
            "avg_score": avg_score_0_10,
            "avg_phase_score_1_3": avg_phase_score_1_3,
            "avg_steps_pct": steps_applied_pct,
            "legacy_count": legacy_8,

            # KPIs nuevos del checklist
            "fases_checklist_0_10": kpi_fases_0_10,
            "fases_checklist_detalle": {
                "apertura": f"{davinci_check['apertura']['total']}/{davinci_check['apertura']['max']}",
                "persuasion": f"{davinci_check['persuasion']['total']}/{davinci_check['persuasion']['max']}",
                "cierre": f"{davinci_check['cierre']['total']}/{davinci_check['cierre']['max']}",
            },
        }
    }

    # Compacto dinámico para Admin/RH + frase IA
    internal_summary["compact"] = _build_compact(user_text, internal_summary)
    internal_summary["compact"]["analysis_ia"] = analysis_ia
    internal_summary["eval_version"] = EVAL_VERSION

    # Público (feedback motivador y específico para el PARTICIPANTE)
    compact = internal_summary.get("compact", {})
    dyn_strength = "; ".join(compact.get("strengths", [])[:2]) or "buen manejo de la relación"
    dyn_opps     = "; ".join(compact.get("opportunities", [])[:2]) or "reforzar evidencia y cerrar con acuerdo"
    coaching     = "; ".join(compact.get("coaching_3", [])[:2]) or "prepara 3 preguntas de apertura y define un cierre concreto"
    kpi_line     = " · ".join(compact.get("kpis", [])[:2])

    paso_line = (
        f"Apertura {davinci_check['apertura']['total']}/{davinci_check['apertura']['max']} · "
        f"Persuasión {davinci_check['persuasion']['total']}/{davinci_check['persuasion']['max']} · "
        f"Cierre {davinci_check['cierre']['total']}/{davinci_check['cierre']['max']}"
    )

    public_block = textwrap.dedent(f"""
        {gpt_public}

        Fortalezas: {dyn_strength}
        Oportunidades: {dyn_opps}
        Recomendación: {coaching}
        KPI: {kpi_line} · Fases: {paso_line}
    """).strip()

    return {"public": public_block, "internal": internal_summary, "level": level}

# ─────────── Persistencia ───────────

def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    result = evaluate_interaction(user_text, leo_text, video_path)
    internal = _validate_internal(result.get("internal"), user_text)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Guardamos RH (JSON) y el resumen público (para el cuadro azul)
            cur.execute(
                "UPDATE interactions SET evaluation_rh = %s, evaluation = %s WHERE id = %s",
                (json.dumps(internal, ensure_ascii=False), result.get("public", ""), int(session_id))
            )
        conn.commit()
    except Exception:
        result["level"] = "error"
        result["public"] += "\n\n⚠️ No se pudo registrar el análisis en BD."
    finally:
        if conn:
            conn.close()
    return result
