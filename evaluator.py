# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# Devuelve:
#   { "public": str, "internal": dict, "level": "alto" | "error" }
# -------------------------------------------------------------------
import os, json, textwrap, unicodedata, re, difflib
from typing import Optional, Dict, List

# ── OpenCV opcional ────────────────────────────────────────────────
try:
    import cv2  # pip install opencv-python-headless
except ImportError:
    cv2 = None  # ← evita crash en entornos sin OpenCV

from openai import OpenAI, OpenAIError
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# ────────────────────────────────────────────────────────────────────
#  Utilidades comunes
# ────────────────────────────────────────────────────────────────────

def normalize(txt: str) -> str:
    """Minúsculas + sin acentos + espacios compactados para comparaciones robustas."""
    if not txt:
        return ""
    t = unicodedata.normalize("NFD", txt)
    t = t.encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t

def canonicalize_products(nt: str) -> str:
    """
    Normaliza variantes del nombre de producto a un token canónico 'esoxx-one'.
    Soporta errores comunes del ASR: 'eso xx', 'esox one', 'esof one', 'ecox one', etc.
    """
    if not nt:
        return nt
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
    """
    True si 'needle' aparece en 'haystack' de forma exacta o aproximada (difflib).
    """
    if not needle:
        return False
    if needle in haystack:
        return True

    tokens = haystack.split()
    words_needle = max(3, len(needle.split()))
    win = min(max(words_needle + 4, 8), 40)

    for i in range(0, max(1, len(tokens) - win + 1)):
        segment = " ".join(tokens[i:i + win])
        ratio = difflib.SequenceMatcher(None, segment, needle).ratio()
        if ratio >= threshold:
            return True

    ratio_global = difflib.SequenceMatcher(None, haystack, needle).ratio()
    return ratio_global >= max(0.70, threshold - 0.1)

def count_fuzzy_any(nt: str, phrases: List[str], threshold: float = 0.82) -> int:
    return sum(1 for p in phrases if fuzzy_contains(nt, p, threshold))

# ────────────────────────────────────────────────────────────────────
#  Función principal
# ────────────────────────────────────────────────────────────────────

def evaluate_interaction(
    user_text: str,
    leo_text: str,
    video_path: Optional[str] = None,
) -> Dict[str, object]:
    """
    user_text : diálogo del participante (representante)
    leo_text  : diálogo del avatar / médico (puede ir vacío)
    video_path: ruta local del video .mp4 (opcional). Si es None o no hay OpenCV,
                se omite el análisis visual.
    Devuelve un dict con:
        - public  : bloque de texto para mostrar al usuario
        - internal: JSON detallado para Capacitación/Training
        - level   : "alto" | "error"
    """

    # ── Heurísticas de contenido ─────────────────────────────────────
    WEIGHTED_KWS = {
        # 3 puntos (claims fuertes / diferenciales clínicos)
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
        # 2 puntos (propiedades/mejores prácticas de uso y posología)
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
        # 1 punto (mensajes base / valor universal)
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

    # Frases guía para puntuar por fases (Modelo Da Vinci)
    DAVINCI_POINTS = {
        "apertura": {
            2: [
                "cuales son las mayores preocupaciones que tiene en sus pacientes con erge",
                "que caracteristicas tienen sus pacientes con reflujo gastroesofagico",
                "dando seguimiento a mi visita anterior",
                "me gustaria conocer que es lo que mas le preocupa",
            ],
            1: [
                "buenos dias dra",
                "mi nombre es",
                "como ha estado",
            ],
        },
        "persuasion": {
            2: [
                "que caracteristicas considera ideales en un producto para tratar la erge",
                "que es lo que busca cuando selecciona un producto",
                "cuales son los objetivos de tratamiento para tratar un paciente con reflujo gastroesofagico",
                "en comparacion con los antiacidos",
                "combinado con ibp",
            ]
        },
        "cierre": {
            2: [
                "con base a lo dialogado considera que esoxx-one pueden ser la mejor opcion de tratamiento",
                "que otras caracteristicas necesita para considerar a esoxx-one como primera opcion",
                "grupo de pacientes",
                "beneficie a sus pacientes",
            ],
            1: [
                "puedo contar con",
                "podemos acordar",
            ],
        },
        # Preparación: detector + recomendación SMART
        "preparacion": {
            2: ["objetivo smart", "mi objetivo hoy es"],
            1: ["objetivo de la visita", "mensaje clave", "materiales"],
        }
    }

    # Palabras base legacy (0–8)
    KW_LIST = [
        "beneficio", "estudio", "sintoma", "tratamiento",
        "reflujo", "mecanismo", "eficacia", "seguridad",
    ]
    BAD_PHRASES = [
        "no se", "no tengo idea", "lo invento", "no lo estudie",
        "no estudie bien", "no conozco", "no me acuerdo",
    ]
    # Escucha activa (incluye preguntas abiertas frecuentes)
    LISTEN_KW = [
        "entiendo", "comprendo", "veo que", "lo que dices",
        "si entiendo bien", "parafraseando",
        "que le preocupa", "me gustaria conocer", "que caracteristicas tienen",
        "podria contarme", "como describe a sus pacientes",
    ]

    # ── Helpers de puntuación (fuzzy) ───────────────────────────────
    def score_weighted_phrases(t: str) -> Dict[str, object]:
        nt = canonicalize_products(normalize(t))
        breakdown = []
        total = 0
        for pts_key, phrases in WEIGHTED_KWS.items():
            pts = int(re.sub(r"[^0-9]", "", pts_key) or "0")
            for p in phrases:
                if fuzzy_contains(nt, p, threshold=0.80):
                    total += pts
                    breakdown.append({"phrase": p, "points": pts})
        return {"total_points": total, "breakdown": breakdown}

    def score_davinci_points(t: str) -> Dict[str, object]:
        nt = canonicalize_products(normalize(t))
        stage_points: Dict[str, int] = {}
        for stage, rules in DAVINCI_POINTS.items():
            s = 0
            for pts, plist in rules.items():
                for p in plist:
                    if fuzzy_contains(nt, p, threshold=0.80):
                        s += int(pts)
            stage_points[stage] = s
        stage_points["total"] = sum(v for k, v in stage_points.items() if k != "total")
        # Reintento con umbral más laxo si todo quedó en 0 (ASR ruidoso)
        if stage_points["total"] == 0:
            stage_points = {}
            for stage, rules in DAVINCI_POINTS.items():
                s = 0
                for pts, plist in rules.items():
                    for p in plist:
                        if fuzzy_contains(nt, p, threshold=0.74):
                            s += int(pts)
                stage_points[stage] = s
            stage_points["total"] = sum(v for k, v in stage_points.items() if k != "total")
        return stage_points

    def kw_score(t: str) -> int:
        nt = canonicalize_products(normalize(t))
        return sum(1 for kw in KW_LIST if fuzzy_contains(nt, kw, threshold=0.84))

    def disq_flag(t: str) -> bool:
        nt = normalize(t)
        return any(fuzzy_contains(nt, w, threshold=0.88) for w in BAD_PHRASES)

    # ── Análisis visual express (solo si hay OpenCV) ────────────────
    def visual_analysis(path: str):
        MAX_FRAMES = int(os.getenv("MAX_FRAMES_TO_CHECK", 60))  # ← configurable
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return "⚠️ No se pudo abrir video.", "Error video", "N/A"

            frontal = total = 0
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
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

    # decide si ejecutar análisis visual
    vis_pub, vis_int, vis_pct = (
        visual_analysis(video_path)
        if video_path and cv2 and os.path.exists(video_path)
        else ("⚠️ Sin video disponible.", "No evaluado", "N/A")
    )

    # --- Detección de fases (marcadores simples para flags) ---------
    PHRASE_MAP: Dict[str, List[str]] = {
        "preparacion": ["objetivo de la visita", "materiales", "mensaje clave", "smart", "objetivo smart", "mi objetivo hoy es"],
        "apertura": ["buenos dias", "como ha estado", "pacientes", "necesidades", "visita anterior", "que le preocupa", "que caracteristicas tienen"],
        "persuasion": ["objetivos de tratamiento", "beneficio", "mecanismo", "estudio", "evidencia", "caracteristicas del producto", "combinado con ibp"],
        "cierre": ["siguiente paso", "podemos acordar", "cuento con usted", "promocion", "farmacias", "puedo contar con"],
        "analisis_post": ["auto-evaluacion", "objecciones", "objeciones", "proxima visita", "actualiza"],
    }

    def step_flag(step_kw: List[str]) -> bool:
        nt = canonicalize_products(normalize(user_text))
        return any(fuzzy_contains(nt, p, threshold=0.82) for p in step_kw)

    sales_model = {step: step_flag(kws) for step, kws in PHRASE_MAP.items()}

    # ────────────────────────────────────────────────────────────────
    #  Llamada a GPT (estructura de salida compatible)
    # ────────────────────────────────────────────────────────────────
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Eres un coach-evaluador senior de la industria farmacéutica.
        Debes calificar **cada fase del Modelo Da Vinci** según la evidencia
        en la conversación (Preparación, Apertura, Persuasión, Cierre,
        Análisis Posterior). Usa solo los datos dados y responde en JSON.
        Si la doctora pregunta por el objetivo, evalúa si el objetivo del
        representante es SMART (específico, medible, alcanzable, relevante,
        acotado en tiempo) y menciónalo en la evaluación general.
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

        convo = (
            f"--- Participante ---\n{user_text}\n"
            f"--- Médico (Leo) ---\n{leo_text or '(sin diálogo)'}"
        )

        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_GPT_MODEL", "gpt-4o-mini"),
            timeout=40,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + FORMAT_GUIDE},
                {"role": "user", "content": convo},
            ],
            temperature=0.4,
        )
        gpt_json = json.loads(completion.choices[0].message.content)
        gpt_public = gpt_json.get("public_summary", "")
        gpt_internal = gpt_json.get("internal_analysis", {})
        level = "alto"
    except (OpenAIError, json.JSONDecodeError, Exception) as e:
        gpt_public = f"⚠️ GPT error: {e}"
        gpt_internal = {"error": str(e)}
        level = "error"

    # ────────────────────────────────────────────────────────────────
    #  Construcción de internal summary para Capacitación
    # ────────────────────────────────────────────────────────────────
    def norm_keys(d: Dict[str, object]) -> Dict[str, object]:
        return {normalize(k): v for k, v in d.items()} if isinstance(d, dict) else {}

    weighted = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)

    # Fallback cualitativo si GPT no trae "Modelo_DaVinci"
    def qual_from_points(p: int) -> str:
        if p >= 2:
            return "Excelente"
        if p == 1:
            return "Bien"
        return "Necesita Mejora"

    if not isinstance(gpt_internal, dict):
        gpt_internal = {}

    modelo_gpt = gpt_internal.get("Modelo_DaVinci") if isinstance(gpt_internal, dict) else None
    if not modelo_gpt or not isinstance(modelo_gpt, dict):
        gpt_internal["Modelo_DaVinci"] = {
            "preparacion":   qual_from_points(int(davinci_pts.get("preparacion", 0))),
            "apertura":      qual_from_points(int(davinci_pts.get("apertura", 0))),
            "persuasion":    qual_from_points(int(davinci_pts.get("persuasion", 0))),
            "cierre":        qual_from_points(int(davinci_pts.get("cierre", 0))),
            "analisis_post": qual_from_points(int(davinci_pts.get("analisis_post", 0))),
        }

    internal_summary = {
        # Resumen general de capacitación
        "overall_training_summary": gpt_internal.get("overall_evaluation", ""),
        # Métrica original (conteo de 8 palabras)
        "knowledge_score_legacy": f"{kw_score(user_text)}/8",
        # Métrica ponderada solicitada
        "knowledge_weighted_total_points": weighted["total_points"],
        "knowledge_weighted_breakdown": weighted["breakdown"],

        "visual_presence": vis_int,
        "visual_percentage": vis_pct,

        # Señales de fases (flags verdaderos)
        "da_vinci_step_flags": {
            **{k: ("✅" if v else "❌") for k, v in sales_model.items()},
            "steps_applied_count": f"{sum(1 for v in sales_model.values() if v)}/5",
        },

        # Puntaje por fase (para tabla de admin)
        "da_vinci_points": davinci_pts,

        "active_listening_simple_detection": (
            "Alta" if count_fuzzy_any(normalize(user_text), LISTEN_KW, threshold=0.82) >= 4
            else "Moderada" if count_fuzzy_any(normalize(user_text), LISTEN_KW, threshold=0.82) >= 2
            else "Baja"
        ),
        "disqualifying_phrases_detected": disq_flag(user_text),
        "gpt_detailed_feedback": norm_keys(gpt_internal),
    }

    # Si no hubo tiempo para análisis posterior, sugerir follow-up operativo
    if not sales_model.get("analisis_post", False):
        internal_summary.setdefault("follow_up_suggestions", [])
        internal_summary["follow_up_suggestions"].append(
            "Antes de terminar tu jornada, registra una autoevaluación breve (qué funcionó, objeción clave, plan para próxima visita) y agenda tu próximo objetivo SMART."
        )

    # ── Bloque público (tono diplomático) ───────────────────────────
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
