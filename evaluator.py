# evaluator.py
# -------------------------------------------------------------------
# Analiza una simulación Representante ↔ Médico (texto + video opc.)
# Devuelve:
#   { "public": str, "internal": dict, "level": "alto" | "error" }
# -------------------------------------------------------------------
import os, json, textwrap
from datetime import datetime
from typing import Optional

import cv2                      # pip install opencv-python-headless
from openai import OpenAI, OpenAIError
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# ────────────────────────────────────────────────────────────────────
#  Función principal
# ────────────────────────────────────────────────────────────────────
def evaluate_interaction(
    user_text: str,
    leo_text: str,
    video_path: Optional[str] = None,
) -> dict:

    # ── Heurísticas rápidas ─────────────────────────────────────────
    KW_LIST = [
        "beneficio", "estudio", "síntoma", "tratamiento",
        "reflujo", "mecanismo", "eficacia", "seguridad",
    ]
    CLOSURE_WORDS = ["compromiso", "siguiente paso", "acordamos", "puedo contar con"]
    BAD_PHRASES = [
        "no sé", "no tengo idea", "lo invento", "no lo estudié",
        "no estudié bien", "no conozco", "no me acuerdo",
    ]
    LISTEN_KW = [
        "entiendo", "comprendo", "veo que", "lo que dices",
        "si entiendo bien", "parafraseando",
    ]

    def kw_score(t: str) -> int:
        return sum(kw in t.lower() for kw in KW_LIST)

    def has_closure(t: str) -> bool:
        return any(w in t.lower() for w in CLOSURE_WORDS)

    def disq_flag(t: str) -> bool:
        return any(w in t.lower() for w in BAD_PHRASES)

    # ── Análisis visual express ─────────────────────────────────────
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

    vis_pub, vis_int, vis_pct = (
        visual_analysis(video_path)
        if video_path and os.path.exists(video_path)
        else ("⚠️ Sin video disponible.", "No evaluado", "N/A")
    )

    sales_model = {
        "diagnostico":   any(w in user_text.lower() for w in ["cómo", "qué", "cuándo", "por qué", "necesita"]),
        "argumentacion": any(w in user_text.lower() for w in ["beneficio", "eficaz", "estudio", "seguridad"]),
        "validacion":    any(w in user_text.lower() for w in ["entiendo", "comprendo", "veo que"]),
        "cierre":        has_closure(user_text),
    }

    # ────────────────────────────────────────────────────────────────
    #  Llamada a GPT
    # ────────────────────────────────────────────────────────────────
    try:
        SYSTEM_PROMPT = textwrap.dedent("""
        Eres un coach-evaluador senior de la industria farmacéutica.
        Evalúa solo al Participante (representante) y responde EXCLUSIVAMENTE
        con un bloque JSON que cumpla el formato indicado.

        Si detectas información clínica falsa o improvisada → todas
        las fases se marcan "Necesita Mejora".
        """)

        FORMAT_GUIDE = textwrap.dedent("""
        {
          "public_summary": "<máx 120 palabras>",
          "internal_analysis": {
            "overall_evaluation": "<2-3 frases>",
            "Modelo_DaVinci": { ... },
            "Prioridad_tiempo": "...",
            "Adaptacion_estilo": { ... },
            "Control_conversacion": "...",
            "Manejo_preguntas": "...",
            "Active_Listening": "...",
            "Visual_presence": "...",
            "Safety_flags": { ... },
            "Areas_de_mejora": [ ... ]
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
                {"role": "user",   "content": convo},
            ],
            temperature=0.4,
        )
        gpt_json = json.loads(completion.choices[0].message.content)

        gpt_public   = gpt_json.get("public_summary", "")
        gpt_internal = gpt_json.get("internal_analysis", {})
        level        = "alto"

    except (OpenAIError, json.JSONDecodeError, Exception) as e:
        gpt_public   = f"⚠️ GPT error: {e}"
        gpt_internal = {"error": str(e)}
        level        = "error"

    # ────────────────────────────────────────────────────────────────
    #  Build internal summary for RH
    # ────────────────────────────────────────────────────────────────
    def norm_keys(d: dict) -> dict:
        import unicodedata
        return {
            unicodedata.normalize("NFD", k).encode("ascii", "ignore").decode().lower(): v
            for k, v in d.items()
        }

    internal_summary = {
        "overall_rh_summary": gpt_internal.get("overall_evaluation", ""),
        "knowledge_score": f"{kw_score(user_text)}/8",
        "visual_presence": vis_int,
        "visual_percentage": vis_pct,
        "sales_model_simple_detection": {
            **{k: ("✅" if v else "❌") for k, v in sales_model.items()},
            "steps_applied_count": f"{sum(sales_model.values())}/4",
        },
        "active_listening_simple_detection": (
            "Alta" if sum(p in user_text.lower() for p in LISTEN_KW) >= 4
            else "Moderada" if sum(p in user_text.lower() for p in LISTEN_KW) >= 2
            else "Baja"
        ),
        "disqualifying_phrases_detected": disq_flag(user_text),
        "gpt_detailed_feedback": norm_keys(gpt_internal),
    }

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
