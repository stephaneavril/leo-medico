# === evaluator.py — Evaluación por CONCEPTOS + salida dual (Usuario / RH) ===
# - Robusto a transcripción ruidosa: usa embeddings (si hay OPENAI_API_KEY)
# - Genera resumen diplomático para el usuario y tarjeta “dura” para Capacitación.
# - Sin dependencias de video (opcional, desactivado por defecto).
# -----------------------------------------------------------------------------

from __future__ import annotations
import os, json, re, textwrap, unicodedata, difflib
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse
from functools import lru_cache

# ---- Dependencias externas
import psycopg2

# OpenAI (chat + embeddings)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
try:
    from openai import OpenAI
    _openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    _openai_client = None

# NumPy para cosenos (si no, fallback simple)
try:
    import numpy as np
except Exception:
    np = None

# Video opcional (desactivado por defecto)
try:
    import cv2  # noqa
except Exception:
    cv2 = None

EVAL_ENABLE_VIDEO = os.getenv("EVAL_ENABLE_VIDEO", "0") == "1"
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
CHAT_MODEL  = os.getenv("OPENAI_GPT_MODEL", "gpt-4o-mini")

# ───────────────────── Utilidades de texto ─────────────────────

def normalize(txt: str) -> str:
    if not txt:
        return ""
    t = unicodedata.normalize("NFD", txt)
    t = t.encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t

def canonicalize_products(nt: str) -> str:
    """Normaliza variantes del producto a 'esoxx one' (tolerante a ASR)."""
    variants = [
        r"\beso\s*xx\s*one\b", r"\besox+\s*one\b", r"\besoxx[-\s]*one\b",
        r"\besof+\s*one\b", r"\becox+\s*one\b", r"\besox+\b", r"\besof+\b",
        r"\becox+\b", r"\beso\s*xx\b", r"\besoxxone\b", r"\besoks?\b",
        r"\bes oks?\s*one\b", r"\bes ok\s*one\b",
    ]
    canon = nt
    for pat in variants:
        canon = re.sub(pat, "esoxx one", canon)
    return canon

def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.82) -> bool:
    if not needle:
        return False
    if needle in haystack:
        return True
    toks = haystack.split()
    win = min(max(len(needle.split()) + 4, 8), 40)
    for i in range(0, max(1, len(toks) - win + 1)):
        segment = " ".join(toks[i:i+win])
        if difflib.SequenceMatcher(None, segment, needle).ratio() >= threshold:
            return True
    return difflib.SequenceMatcher(None, haystack, needle).ratio() >= max(0.70, threshold - 0.1)

# ───────────────────── Conceptos semánticos ─────────────────────

SEM_CONCEPTS = {
    "apertura_descubrimiento":
        "Saludo breve y exploración de necesidades del médico: preguntas sobre pacientes, preocupaciones y contexto clínico.",
    "posologia_completa":
        "Posología exacta de ESOXX ONE: un stick después de cada comida y uno antes de dormir; esperar 30 a 60 minutos sin ingerir alimentos o bebidas.",
    "evidencia_trazable":
        "Evidencia clínica trazable: menciona autor y año o población y endpoint con resultado claro; evita porcentajes no sustentados.",
    "mecanismo_correcto":
        "Mecanismo correcto de ESOXX ONE: dispositivo tópico esofágico que forma una barrera bioadhesiva con ácido hialurónico, condroitina y poloxámero 407.",
    "sinergia_ibp":
        "Uso como adyuvante con inhibidores de bomba de protones (IBP), con beneficio frente a monoterapia con IBP.",
    "cierre_con_acuerdo":
        "Propone un siguiente paso clínico concreto: prueba con pacientes indicados y acuerda seguimiento con fecha.",
    "manejo_objeciones":
        "Escucha objeciones y las aborda con información clínica y tono empático.",
    "escucha_activa_reflejo":
        "Parafrasea o valida lo dicho por el médico antes de argumentar; demuestra escucha activa.",
    "seguridad_moderada":
        "Seguridad con lenguaje clínico moderado: bien tolerado, acción local, evita absolutos y promesas.",
}

ABSOLUTES   = ["el mejor", "completamente seguro", "totalmente seguro", "no tiene efectos", "para todos"]
BAD_PHRASES = ["no se", "no tengo idea", "lo invento", "no estudie", "no me acuerdo"]
SENSITIVE   = ["embarazad", "niños", "pediatr", "descuento", "promocion", "3x4", "precio en clinica"]
LISTEN_KW   = ["entiendo", "comprendo", "veo que", "si entiendo bien", "parafrase", "que le preocupa"]
CLOSE_KW    = ["siguiente paso", "podemos acordar", "puedo contar con", "le parece si", "empezar a considerar"]

# ───────── Embeddings helpers (con degradación si no hay API) ─────────

@lru_cache(maxsize=256)
def _embed(text: str):
    """Devuelve vector de embedding o lista vacía si no hay soporte."""
    if not text or not _openai_client or not np:
        return None
    try:
        text = text[:6000]
        v = _openai_client.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
        return np.array(v, dtype=float)
    except Exception:
        return None

def _cos(a, b) -> float:
    if not np or a is None or b is None:
        return 0.0
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

def semantic_concepts(text: str, thr: float = 0.75) -> Dict[str, float]:
    """Devuelve {concepto: score_coseno} para conceptos presentes."""
    if not text:
        return {}
    t_emb = _embed(text)
    if t_emb is None:
        return {}  # sin embeddings disponibles
    hits = {}
    for name, desc in SEM_CONCEPTS.items():
        c_emb = _embed(f"[CONCEPTO] {name}: {desc}")
        cos = _cos(t_emb, c_emb)
        if cos >= thr:
            hits[name] = round(cos, 3)
    return hits

def estimate_transcript_confidence(text: str) -> str:
    nt = normalize(text)
    toks = nt.split()
    if len(toks) < 25:
        return "Baja"
    nonalpha = sum(1 for ch in text if not (ch.isalpha() or ch.isspace()))
    ratio = nonalpha / max(1, len(text))
    if ratio > 0.18:
        return "Baja"
    if ratio > 0.12:
        return "Media"
    return "Alta"

# ───────────────── Señales simples + score conceptual ─────────────────

def simple_signals(t: str) -> Dict[str, object]:
    nt = canonicalize_products(normalize(t))
    sem_hits = semantic_concepts(t)  # {concept: cos}
    sem_count = len(sem_hits)

    q_rate = t.count("?") / max(1, len(nt.split()))
    listening_hits = sum(1 for k in LISTEN_KW if fuzzy_contains(nt, k, 0.82))
    if "escucha_activa_reflejo" in sem_hits:
        listening_hits = max(listening_hits, 2)

    closing_flag = ("cierre_con_acuerdo" in sem_hits) or any(fuzzy_contains(nt, k, 0.82) for k in CLOSE_KW)

    legacy_kw = sum(
        1 for k in ["beneficio", "estudio", "mecanismo", "posologia", "reflujo", "erge", "ibp", "seguridad"]
        if fuzzy_contains(nt, k, 0.84)
    )
    legacy_kw = min(8, legacy_kw)

    red_abs = any(fuzzy_contains(nt, w, 0.88) for w in ABSOLUTES)
    red_bad = any(fuzzy_contains(nt, w, 0.88) for w in BAD_PHRASES)
    red_sens= any(fuzzy_contains(nt, w, 0.86) for w in SENSITIVE)

    confidence = estimate_transcript_confidence(t)

    return {
        "q_rate": round(q_rate, 3),
        "listening_level": "Alta" if listening_hits >= 4 else "Moderada" if listening_hits >= 2 else "Baja",
        "closing": closing_flag,
        "concept_hits": sem_hits,
        "concept_count": sem_count,
        "legacy_kw": legacy_kw,
        "red_flags": {"absolutes": red_abs, "ignorance": red_bad, "sensitive": red_sens},
        "input_confidence": confidence
    }

def score_and_risk(sig: Dict[str, object]) -> Tuple[int, str]:
    score = 0
    score += min(6, sig["concept_count"])                         # 0–6 por conceptos presentes
    score += int((sig["legacy_kw"] / 8.0) * 4.0)                  # 0–4 por “señales de contenido”
    score += 2 if sig["closing"] else 0                           # 0–2 si propone siguiente paso
    score += 2 if sig["listening_level"] == "Alta" else (1 if sig["listening_level"] == "Moderada" else 0)  # 0–2

    if sig["red_flags"]["absolutes"] or sig["red_flags"]["ignorance"]:
        score = max(0, score - 2)
    if sig["input_confidence"] == "Baja":
        score = max(0, score - 1)

    score = max(0, min(14, score))
    if sig["red_flags"]["absolutes"] or sig["red_flags"]["ignorance"] or score <= 4:
        risk = "ALTO"
    elif score <= 9:
        risk = "MEDIO"
    else:
        risk = "BAJO"
    return score, risk

def md_status_from_concepts(sem: Dict[str, float]) -> Dict[str, str]:
    # Mapeo a “Excelente | Bien | Necesita Mejora”
    def level(has: bool, strong: bool = False) -> str:
        if strong:
            return "Excelente" if has else "Necesita Mejora"
        return "Bien" if has else "Necesita Mejora"

    prep  = level("apertura_descubrimiento" in sem)
    apertura = prep
    pers  = "Excelente" if sum(k in sem for k in ["mecanismo_correcto","posologia_completa","evidencia_trazable","sinergia_ibp"]) >= 3 \
            else "Bien" if sum(k in sem for k in ["mecanismo_correcto","posologia_completa","evidencia_trazable","sinergia_ibp"]) >= 1 \
            else "Necesita Mejora"
    cierre = "Excelente" if "cierre_con_acuerdo" in sem else "Necesita Mejora"
    post   = "Bien" if "manejo_objeciones" in sem else "Necesita Mejora"
    return {
        "preparacion": prep,
        "apertura": apertura,
        "persuasion": pers,
        "cierre": cierre,
        "analisis_post": post,
    }

# ──────────────────── OpenAI helpers (safe) ────────────────────

def chat_json(prompt_system: str, prompt_user: str) -> dict:
    if not _openai_client:
        return {}
    try:
        rsp = _openai_client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            temperature=0.4,
            timeout=45,
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user}
            ],
        )
        return json.loads(rsp.choices[0].message.content)
    except Exception:
        return {}

# ───────────────────── Persistencia en BD ─────────────────────

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set!")
    parsed = urlparse(database_url)
    return psycopg2.connect(
        database=parsed.path[1:], user=parsed.username, password=parsed.password,
        host=parsed.hostname, port=parsed.port, sslmode="require",
    )

# ───────────────────── Visual (opcional) ─────────────────────

def visual_stub():
    if not EVAL_ENABLE_VIDEO:
        return ("⚠️ Sin video evaluado por configuración.", "Sin evaluación de video.", "N/A")
    return ("⚠️ Evaluación de video no disponible.", "No evaluado", "N/A")

# ───────────────────── Evaluación principal ─────────────────────

def evaluate_interaction(user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    nt = canonicalize_products(normalize(user_text or ""))

    # Señales y puntaje
    sig = simple_signals(user_text or "")
    score14, risk = score_and_risk(sig)
    md_status = md_status_from_concepts(sig.get("concept_hits", {}))

    # Visual (stub por defecto)
    vis_pub, vis_int, vis_pct = visual_stub()

    # --- PROMPTS (Usuario diplomático + RH directo)
    SYSTEM = (
        "Eres un coach farmacéutico senior. Evalúa SOLO lo que aparece en el texto; "
        "usa tono profesional. Devuelve JSON con dos bloques: "
        '{"public_summary": "...", '
        '"rh": {"strengths":[],"opportunities":[],"coaching_3":[],"guide_phrase":"", "kpis_next":[]}}'
    )

    # Prepara contexto para el LLM (sin inventar, usa señales detectadas)
    context = {
        "score": score14,
        "risk": risk,
        "input_confidence": sig.get("input_confidence"),
        "concepts_present": list(sig.get("concept_hits", {}).keys()),
        "red_flags": sig.get("red_flags"),
        "da_vinci_status": md_status
    }

    USER = textwrap.dedent(f"""
    TRANSCRIPCIÓN (representante):
    {user_text or "[vacío]"}

    CONTEXTO (no inventes):
    {json.dumps(context, ensure_ascii=False)}

    INSTRUCCIONES:
    1) "public_summary": ≤120 palabras, tono amable y motivador para el usuario.
    2) "rh": escribe:
       - "strengths": 2-4 fortalezas factuales.
       - "opportunities": 3-5 oportunidades específicas (evita absolutos, exige posología completa, pide evidencia trazable).
       - "coaching_3": exactamente 3 bullets accionables.
       - "guide_phrase": una frase clínica guía (1 línea) sobre ESOXX ONE.
       - "kpis_next": 2-4 KPIs concretos para la próxima visita.
    No inventes datos; si falta info, sugiere prácticas seguras.
    """)

    gpt = chat_json(SYSTEM, USER)

    # Fallbacks si no hay LLM o contenido insuficiente
    public_summary = (
        gpt.get("public_summary")
        if isinstance(gpt, dict) and gpt.get("public_summary")
        else "Gracias por entrenar con Leo. Refuerza evidencia y posología completa; cierra con un siguiente paso acordado."
    )

    rh = gpt.get("rh") if isinstance(gpt, dict) else {}
    strengths     = rh.get("strengths")     or ["Trato cordial.", "Buena disposición al diálogo."]
    opportunities = rh.get("opportunities") or ["Estructura Da Vinci incompleta.", "Posología no declarada con precisión.", "Falta evidencia trazable."]
    coaching_3    = rh.get("coaching_3")    or [
        "Decir posología completa: 1 stick post-comida + 1 antes de dormir; esperar 30–60 min.",
        "Sustituir absolutos por lenguaje clínico moderado.",
        "Citar un estudio con autor/año y resultado principal."
    ]
    guide_phrase  = rh.get("guide_phrase")  or "ESOXX ONE: barrera bioadhesiva tópica; protege mucosa y complementa IBP con posología clara."
    kpis_next     = rh.get("kpis_next")     or ["% pacientes con alivio temprano", "Adherencia a ventana 30–60 min", "Uso combinado con IBP"]

    # Tarjeta RH en el formato solicitado
    now_iso = os.getenv("NOW_ISO_OVERRIDE") or ""
    rh_card_text = textwrap.dedent(f"""
    Sesión: {now_iso or "—"} · Score: {score14}/14 · Riesgo: {risk}
    Fortalezas: {("; ".join(strengths)).rstrip('.')}
    Oportunidades clave: {("; ".join(opportunities)).rstrip('.')}
    Coaching inmediato (3):
    - {coaching_3[0]}
    - {coaching_3[1]}
    - {coaching_3[2]}
    Frase guía sugerida:
    {guide_phrase}
    KPI próxima visita: {("; ".join(kpis_next)).rstrip('.')}
    Confianza del análisis (calidad de transcripción): {sig.get("input_confidence")}
    """).strip()

    # Bloque público breve (con recordatorio amable)
    public_block = textwrap.dedent(f"""
    {public_summary}

    {vis_pub}

    Áreas sugeridas:
    • Apoya con evidencia trazable y posología concreta.
    • Cierra con un siguiente paso acordado.
    • Refuerza preguntas y estructura.
    """).strip()

    internal_summary = {
        # Texto completo para mostrar en Admin (RH)
        "rh_card_text": rh_card_text,

        # Métricas clave (sin “numeritis”)
        "score14": score14,
        "risk": risk,
        "input_confidence": sig.get("input_confidence"),
        "da_vinci_status": md_status,

        # Para compatibilidad con vistas antiguas (si las usas)
        "overall_training_summary": rh.get("overall_evaluation") or "Resumen enfocado a capacitación disponible en 'rh_card_text'.",
        "gpt_detailed_feedback": {
            "overall_evaluation": rh.get("overall_evaluation", ""),
            "Modelo_DaVinci": {
                "preparacion": md_status["preparacion"],
                "apertura": md_status["apertura"],
                "persuasion": md_status["persuasion"],
                "cierre": md_status["cierre"],
                "analisis_post": md_status["analisis_post"],
            }
        },
        # Guardamos también los bulletes
        "strengths": strengths,
        "opportunities": opportunities,
        "coaching_3": coaching_3,
        "guide_phrase": guide_phrase,
        "kpis_next": kpis_next,

        # Por si en el futuro quieres auditar conceptos detectados
        "concept_hits": sig.get("concept_hits", {}),
        "red_flags": sig.get("red_flags", {}),
    }

    return {"public": public_block, "internal": internal_summary, "level": "ok"}

# ───────────────────── Persistencia (evaluate_and_persist) ─────────────────────

def evaluate_and_persist(session_id: int, user_text: str, leo_text: str, video_path: Optional[str] = None) -> Dict[str, object]:
    result = evaluate_interaction(user_text, leo_text, video_path)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE interactions SET evaluation_rh = %s WHERE id = %s",
                (json.dumps(result.get("internal", {}), ensure_ascii=False), int(session_id))
            )
        conn.commit()
    except Exception:
        # No abortamos; devolvemos public igualmente
        result["level"] = "error"
        result["public"] += "\n\n⚠️ No se pudo registrar el análisis en BD."
    finally:
        if conn:
            conn.close()
    return result
