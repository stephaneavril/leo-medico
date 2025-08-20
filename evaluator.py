# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# Devuelve:
#   { "public": str, "internal": dict, "level": "alto" | "error" }
# -------------------------------------------------------------------
import os, json, textwrap, unicodedata
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
    """Minúsculas + sin acentos para comparaciones robustas."""
    return unicodedata.normalize("NFD", txt).encode("ascii", "ignore").decode().lower()

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

    # ── Heurísticas rápidas ─────────────────────────────────────────
    # Configuración de scoring ponderado específico (editable por Training)
    WEIGHTED_KWS = {
        # 3 puntos (claims fuertes / diferenciales clínicos)
        "3pt": [
            "esoxx-one mejora hasta 90% todos los sintomas de la erge",
            "reduccion del uso de antiacidos",
            "demostrado en niños y adolescentes",
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

    # Frases guía para puntuar dentro de fases del Modelo Da Vinci
    DAVINCI_POINTS = {
        "apertura": {
            2: [
                "cuales son las mayores preocupaciones que tiene en sus pacientes con erge",
                "que caracteristicas tienen sus pacientes con reflujo gastroesofagico",
                "dando seguimiento a mi visita anterior",
            ],
            1: [
                "buenos dias dra",
                "mi nombre es",
            ],
        },
        "persuasion": {
            2: [
                "que caracteristicas considera ideales en un producto para tratar la erge",
                "que es lo que busca cuando selecciona un producto",
                "cuales son los objetivos de tratamiento para tratar un paciente con reflujo gastroesofagico",
            ]
        },
        "cierre": {
            2: [
                "con base a lo dialogado considera que esoxx-one pueden ser la mejor opcion de tratamiento para sus pacientes con erge",
                "que otras caracteristicas necesita para considerar a esoxx-one como primera opcion",
                "grupo de pacientes",
                "beneficie a sus pacientes",
            ],
            1: [
                "puedo contar con",
                "recetas",  # p. ej., "¿Puedo contar con X pacientes o recetas?"
            ],
        },
        # Preparación: detector + recomendación SMART
        "preparacion": {
            2: [
                # cuando la doctora pregunta objetivo y el rep formula objetivo SMART
                "objetivo smart",
            ],
            1: [
                "objetivo de la visita",
                "mensaje clave",
            ]
        }
    }

    KW_LIST = [
        "beneficio", "estudio", "sintoma", "tratamiento",
        "reflujo", "mecanismo", "eficacia", "seguridad",
    ]
    BAD_PHRASES = [
        "no se", "no tengo idea", "lo invento", "no lo estudie",
        "no estudie bien", "no conozco", "no me acuerdo",
    ]
    LISTEN_KW = [
        "entiendo", "comprendo", "veo que", "lo que dices",
        "si entiendo bien", "parafraseando",
    ]

    def score_weighted_phrases(t: str) -> Dict[str, object]:
        nt = normalize(t)
        breakdown = []
        total = 0
        for pts_key, phrases in WEIGHTED_KWS.items():
            pts = int(pts_key.replace("pt", "").replace("s", ""))
            for p in phrases:
                if p in nt:
                    total += pts
                    breakdown.append({"phrase": p, "points": pts})
        return {"total_points": total, "breakdown": breakdown}

    def score_davinci_points(t: str) -> Dict[str, object]:
        nt = normalize(t)
        stage_points: Dict[str, int] = {}
        for stage, rules in DAVINCI_POINTS.items():
            s = 0
            for pts, plist in rules.items():
                for p in plist:
                    if p in nt:
                        s += pts
            stage_points[stage] = s
        stage_points["total"] = sum(v for k, v in stage_points.items() if k != "total")
        return stage_points

    def kw_score(t: str) -> int:
        nt = normalize(t)
        return sum(kw in nt for kw in KW_LIST)

    def disq_flag(t: str) -> bool:
        nt = normalize(t)
        return any(w in nt for w in BAD_PHRASES)

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

    # --- Detección Modelo Da Vinci (5 pasos) -----------------------
    PHRASE_MAP: Dict[str, List[str]] = {
        "preparacion": [
            "objetivo de la visita", "materiales", "mensaje clave", "smart",
            "matriz de target", "escalon",
        ],
        "apertura": [
            "buenos dias", "como ha estado", "pacientes", "necesidades",
            "visita anterior",
        ],
        "persuasion": [
            "objetivos de tratamiento", "beneficio", "mecanismo",
            "estudio", "evidencia", "caracteristicas del producto",
        ],
        "cierre": [
            "siguiente paso", "podemos acordar", "cuento con usted",
            "promocion", "farmacias",
        ],
        "analisis_post": [
            "auto-evaluacion", "objeciones", "objecciones", "proxima visita", "actualiza",
        ],
    }

    def step_flag(step_kw: List[str]) -> bool:
        nt = normalize(user_text)
        return any(p in nt for p in step_kw)

    sales_model = {step: step_flag(kws) for step, kws in PHRASE_MAP.items()}

    # ────────────────────────────────────────────────────────────────
    #  Llamada a GPT
    # ────────────────────────────────────────────────────────────────
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Eres un coach-evaluador senior de la industria farmacéutica.
        Debes calificar **cada fase del Modelo Da Vinci** según la evidencia
        en la conversación (Preparación, Apertura, Persuasión, Cierre,
        Análisis Posterior). Usa solo los datos dados y responde en JSON.
        Si la doctora pregunta por el objetivo, evalúa si el objetivo del representante es SMART
        (específico, medible, alcanzable, relevante, acotado en tiempo) y menciónalo en la evaluación general.
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
    #  Build internal summary for Training (antes "RH")
    # ────────────────────────────────────────────────────────────────
    def norm_keys(d: Dict[str, object]) -> Dict[str, object]:
        return {normalize(k): v for k, v in d.items()}

    weighted = score_weighted_phrases(user_text)
    davinci_pts = score_davinci_points(user_text)

    internal_summary = {
        # CAMBIO DE NOMBRE: de RH a Training/Capacitación
        "overall_training_summary": gpt_internal.get("overall_evaluation", ""),
        # Métrica original (conteo de 8 palabras)
        "knowledge_score_legacy": f"{kw_score(user_text)}/8",
        # Nueva métrica ponderada solicitada
        "knowledge_weighted_total_points": weighted["total_points"],
        "knowledge_weighted_breakdown": weighted["breakdown"],

        "visual_presence": vis_int,
        "visual_percentage": vis_pct,

        "da_vinci_step_flags": {
            **{k: ("✅" if v else "❌") for k, v in sales_model.items()},
            "steps_applied_count": f"{sum(sales_model.values())}/5",
        },
        # Puntaje por fase (Apertura/Persuasión/Cierre/Preparación)
        "da_vinci_points": davinci_pts,

        "active_listening_simple_detection": (
            "Alta" if sum(p in normalize(user_text) for p in LISTEN_KW) >= 4
            else "Moderada" if sum(p in normalize(user_text) for p in LISTEN_KW) >= 2
            else "Baja"
        ),
        "disqualifying_phrases_detected": disq_flag(user_text),
        "gpt_detailed_feedback": norm_keys(gpt_internal),
    }

    # Si no hubo tiempo para análisis posterior, sugerir follow-up operativo
    if not sales_model.get("analisis_post", False):
        internal_summary.setdefault("follow_up_suggestions", [])
        internal_summary["follow_up_suggestions"].append(
            "Registrar una autoevaluación breve (3 ítems: qué funcionó, objeción clave, plan para próxima visita) y programar recordatorio."
        )

    public_block = textwrap.dedent(f"""
        {gpt_public}

        {vis_pub}

        Áreas sugeridas extra:
        • Refuerza el modelo Da Vinci en cada interacción.
        • Usa evidencia clínica concreta al responder.
        • Practica manejo de objeciones (método APACT).
        • Mantén buena presencia y contacto visual.
    """).strip()

    return {"public": public_block, "internal": internal_summary, "level": level}
