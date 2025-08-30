# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# Guarda SIEMPRE métricas en BD cuando se llama vía evaluate_and_persist().
# Retorna: {"public": str, "internal": dict, "level": "alto"|"error"}
# -------------------------------------------------------------------
import os, json, textwrap, unicodedata, re, difflib
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

# OpenCV opcional (presencia en video)
try:
    import cv2
except ImportError:
    cv2 = None

import psycopg2
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

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
            "combinado con ibp", "sinergia con inhibidores de la bomba de protones",
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

# ─────────── Visual express ───────────

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

# ─────────── Helpers de “compact” ───────────

def _risk_from_score(score_14: int) -> str:
    if score_14 <= 4:
        return "ALTO"
    if score_14 <= 9:
        return "MEDIO"
    return "BAJO"

def _build_compact(user_text: str, internal: dict) -> dict:
    """Arma el bloque compacto que pide el admin."""
    # 0–8 (conocimiento legacy) + 0–6 (fases promedio) = 14
    legacy_8 = internal.get("knowledge_score_legacy_num") or 0
    avg_phase_1_3 = (internal.get("kpis") or {}).get("avg_phase_score_1_3", 1.0)
    phase_0_6 = max(0, min(6, round((avg_phase_1_3 - 1) * (6 / 2))))
    score_14 = int(max(0, min(14, legacy_8 + phase_0_6)))

    iq = internal.get("interaction_quality", {}) or {}
    davinci = internal.get("da_vinci_points", {}) or {}

    strengths = []
    if (internal.get("product_claims") or {}).get("product_score_total", 0) >= 3:
        strengths.append("Conoce beneficios y mecanismo del producto")
    if iq.get("active_listening_level") in ("Alta", "Moderada"):
        strengths.append(f"Escucha activa {iq.get('active_listening_level').lower()}")
    if iq.get("closing_present"):
        strengths.append("Incluyó cierre con siguiente paso")

    opportunities = []
    if legacy_8 < 5:
        opportunities.append("Refuerza mensajes clave y evidencia clínica")
    if not iq.get("closing_present"):
        opportunities.append("Cierra la visita con un acuerdo concreto")
    if (davinci.get("apertura", 0) + davinci.get("persuasion", 0)) < 3:
        opportunities.append("Profundiza en apertura/persuasión con preguntas y beneficios")

    coaching_3 = []
    if opportunities:
        coaching_3.append("Prepara 1 objetivo SMART para la próxima visita")
    coaching_3.append("Usa 2–3 frases clínicas con respaldo de estudio")
    coaching_3.append("Formula 3 preguntas para explorar necesidades del médico")

    frase_guia = "Propón: 'Con base en lo conversado, ¿le parece iniciar ESOXX-ONE en un paciente candidato y revisamos en 2 semanas?'"
    kpis = [
        f"Score 0–14: {score_14}",
        f"Escucha activa: {iq.get('active_listening_level','N/D')}",
        f"Fases Da Vinci: {davinci.get('total',0)} señales",
    ]

    # Textos listos para “copiar” en admin
    rh_text = (
        f"Sesión: score {score_14}/14; riesgo { _risk_from_score(score_14) }. "
        f"Fortalezas: {', '.join(strengths) or '—'}. "
        f"Oportunidades: {', '.join(opportunities) or '—'}. "
        f"Coaching: {', '.join(coaching_3)}."
    )
    user_text_summary = (
        "Buen avance. Refuerza evidencia y acuerda un siguiente paso concreto. "
        "Prepara 3 preguntas de apertura para conocer mejor a tus pacientes."
    )

    return {
        "score_14": score_14,
        "risk": _risk_from_score(score_14),
        "strengths": strengths,
        "opportunities": opportunities,
        "coaching_3": coaching_3,
        "frase_guia": frase_guia,
        "kpis": kpis,
        "rh_text": rh_text,
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
    # Si no hay compacto, créalo mínimo
    if "compact" not in internal:
        internal["compact"] = _build_compact(user_text, internal)
    return internal

def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    # Visual
    vis_pub, vis_int, vis_pct = (
        visual_analysis(video_path)
        if video_path and cv2 and os.path.exists(video_path)
        else ("⚠️ Sin video disponible.", "No evaluado", "N/A")
    )

    # Señales Da Vinci (% aplicado)
    PHRASE_MAP = {
        "preparacion": ["objetivo de la visita", "propósito de la visita", "mensaje clave", "smart", "objetivo smart", "mi objetivo hoy es", "plan para hoy", "materiales"],
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
    weighted   = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)
    legacy_8   = kw_score(user_text)

    # Calidad + producto
    iq = interaction_quality(user_text)
    prod_detail, prod_total = product_compliance(user_text)

    # Señal de bajo diálogo
    nt = normalize(user_text)
    min_tokens = 25
    min_signals = (iq["question_rate"] > 0.15) or (steps_applied_count >= 2)
    low_dialogue_note = (len(nt.split()) < min_tokens) or not min_signals

    # GPT (resumen + valoración cualitativa)
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Actúa como coach-evaluador senior de la industria farmacéutica (Alfasigma).
        El representante presenta ESOXX ONE (puede aparecer como 'esoxx-one' por normalización).
        Evalúa por fases del Modelo Da Vinci y la calidad de la presentación
        (claridad, foco clínico, evidencia, posología, manejo de dudas), SOLO con el texto dado.
        Responde en JSON EXACTO con el FORMATO.
        """)
        FORMAT_GUIDE = textwrap.dedent("""
        {
          "public_summary": "<máx 120 palabras, tono amable y motivador>",
          "internal_analysis": {
            "overall_evaluation": "<2-3 frases objetivas para capacitación>",
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
        convo = f"--- Participante (representante) ---\n{user_text}\n--- Médico (Leo) ---\n{leo_text or '(no disponible)'}"
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
        gpt_public = ("Buen esfuerzo. Refuerza la estructura Da Vinci, usa evidencia clínica concreta de ESOXX ONE "
                      "y cierra con un siguiente paso claro; practica manejo de objeciones.")
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
        level = "error"

    # KPI promedio (1–3 a 0–10)
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

        "active_listening_simple_detection": iq["active_listening_level"],
        "disqualifying_phrases_detected": disq_flag(user_text),

        "gpt_detailed_feedback": {
            "overall_evaluation": gpt_internal.get("overall_evaluation", ""),
            "Modelo_DaVinci": md
        },

        "kpis": {
            "avg_score": avg_score_0_10,
            "avg_phase_score_1_3": avg_phase_score_1_3,
            "avg_steps_pct": steps_applied_pct,
            "legacy_count": legacy_8,
        }
    }

    # 👉 Compacto para el admin (antes no existía)
    internal_summary["compact"] = _build_compact(user_text, internal_summary)

    # Público (diplomático)
    extra_line = "• Refuerza preguntas y estructura." if (len(normalize(user_text).split()) < 25) else "• Mantén la estructura y refuerza evidencias."
    public_block = textwrap.dedent(f"""
        {gpt_public}

        {vis_pub}

        Áreas sugeridas:
        • Apoya la explicación de ESOXX ONE con evidencia y posología concreta.
        • Cierra con un siguiente paso acordado.
        {extra_line}
    """).strip()

    # Blindaje de esquema
    internal_summary = _validate_internal(internal_summary, user_text)

    return {"public": public_block, "internal": internal_summary, "level": level}

# ─────────── Persistencia ───────────

def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    result = evaluate_interaction(user_text, leo_text, video_path)
    internal = _validate_internal(result.get("internal"), user_text)

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
        result["level"] = "error"
        result["public"] += "\n\n⚠️ No se pudo registrar el análisis en BD."
    finally:
        if conn:
            conn.close()
    return result
