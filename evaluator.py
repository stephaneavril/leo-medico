# evaluator.py
import os
import re
import textwrap
import json
from datetime import datetime
from dotenv import load_dotenv

import cv2                    # pip install opencv-python-headless
import numpy as np            # pip install numpy
from openai import OpenAI, OpenAIError

# ───────────────────────────────────────────────
#  Inicialización
# ───────────────────────────────────────────────
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# ───────────────────────────────────────────────
#  Función principal
# ───────────────────────────────────────────────
def evaluate_interaction(user_text: str,
                         leo_text: str,
                         video_path: str | None = None) -> dict:
    """
    Analiza la transcripción (y video si existe) de una simulación
    Representante ↔ Médico.
    Devuelve:
        {
          "public":   <string para el usuario>,
          "internal": <dict para RH>,
          "level":    "alto" | "error"
        }
    """

    # ── Heurísticas rápidas (palabras clave, etc.) ─────────────────
    def basic_keywords_eval(text: str) -> int:
        keywords = [
            "beneficio", "estudio", "síntoma", "tratamiento",
            "reflujo", "mecanismo", "eficacia", "seguridad"
        ]
        return sum(1 for kw in keywords if kw in text.lower())

    def detect_closure_language(text: str) -> bool:
        patterns = ["compromiso", "siguiente paso", "acordamos", "puedo contar con"]
        return any(p in text.lower() for p in patterns)

    def detect_disqualifying_phrases(text: str) -> bool:
        bad = [
            "no sé", "no tengo idea", "lo invento",
            "no lo estudié", "no estudié bien", "no conozco", "no me acuerdo"
        ]
        return any(p in text.lower() for p in bad)

    # ── Análisis visual muy simple (rostro / visibilidad) ──────────
    def detect_visual_cues_from_video(path: str):
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return ("⚠️ No se pudo abrir el video.", "Error en video", "N/A")

            frontal, total = 0, 0
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )

            for _ in range(200):           # máx 200 frames
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
                return ("⚠️ Sin frames para analizar.", "Sin frames", "0.0%")

            ratio = frontal / total
            if ratio >= 0.7:
                return ("✅ Buena presencia frente a cámara.", "Correcta", f"{ratio*100:.1f}%")
            elif ratio > 0:
                return ("⚠️ Mejora la visibilidad.", "Mejorar visibilidad", f"{ratio*100:.1f}%")
            return ("❌ No se detectó rostro.", "No detectado", "0.0%")

        except Exception as e:
            return (f"⚠️ Error visual: {e}", "Error video", "N/A")

    # ── Métricas rápidas -------------------------------------------------------
    kw_score          = basic_keywords_eval(user_text)
    closure_ok        = detect_closure_language(user_text)
    disq_flag         = detect_disqualifying_phrases(user_text)
    vis_pub, vis_int, vis_pct = (
        detect_visual_cues_from_video(video_path)
        if video_path and os.path.exists(video_path)
        else ("⚠️ Sin video disponible.", "No evaluado", "N/A")
    )

    sales_model_score = {
        "diagnostico":   any(w in user_text.lower() for w in ["cómo", "qué", "cuándo", "por qué", "necesita"]),
        "argumentacion": any(w in user_text.lower() for w in ["beneficio", "eficaz", "estudio", "seguridad"]),
        "validacion":    any(w in user_text.lower() for w in ["entiendo", "comprendo", "veo que"]),
        "cierre":        closure_ok,
    }

    active_listening_keywords = [
        "entiendo", "comprendo", "veo que",
        "lo que dices", "si entiendo bien", "parafraseando"
    ]
    active_listening_score = sum(p in user_text.lower() for p in active_listening_keywords)

    # ────────────────────────────────────────────────────────────────────
    #  LLAMADA A GPT
    # ────────────────────────────────────────────────────────────────────
    try:
        system_prompt = textwrap.dedent("""
            Eres un **coach-evaluador senior** de la industria farmacéutica. Analizas simulaciones de visita médica entre un *Representante* (Participante) y un *Médico* (Avatar).

            ────────────────────────────────────────────
            📝 INSTRUCCIONES GENERALES
            • Analiza solo la transcripción proporcionada.  
            • Evalúa únicamente al Participante.  
            • Si detectas datos clínicos falsos → penaliza TODAS las fases.  
            • Escribe en español neutro y responde EXCLUSIVAMENTE con un bloque JSON.

            ────────────────────────────────────────────
            📊 CRITERIOS
            1️⃣ Modelo Da Vinci (Diagnóstico, Argumentación, Validación, Cierre).  
            2️⃣ Prioridad en uso del tiempo — Correcta | Mejorable | Deficiente  
            3️⃣ Adaptación al estilo del médico — Correcta | Mejorable | Deficiente  
            4️⃣ Control de la conversación — Correcto | Mejorable | Deficiente  
            5️⃣ Manejo de preguntas críticas — Correcto | Mejorable | Deficiente | No aplicable  
            Penaliza si el participante improvisa sin conocimiento.

            ────────────────────────────────────────────
            📦 FORMATO OBLIGATORIO (SOLO JSON)
            {
              "public_summary": "<máx 120 palabras, tono motivador>",
              "internal_analysis": {
                "overall_evaluation": "<2-3 frases>",
                "Modelo_DaVinci": {
                  "Diagnostico": "Cumplida / Necesita Mejora + Justificación",
                  "Argumentacion": "...",
                  "Validacion": "...",
                  "Cierre": "..."
                },
                "Prioridad_tiempo": "Correcta / Mejorable / Deficiente + Justificación",
                "Adaptacion_estilo": {
                  "nivel": "Correcta / Mejorable / Deficiente",
                  "comentarios": "<ejemplo rápido>"
                },
                "Control_conversacion": "Correcto / Mejorable / Deficiente + Justificación",
                "Manejo_preguntas": "Correcto / Mejorable / Deficiente / No aplicable + Justificación",
                "Active_Listening": "Alta / Moderada / Baja + Ejemplo",
                "Visual_presence": "Correcta / Mejorar visibilidad / No detectado",
                "Safety_flags": {
                  "info_falsa_detectada": true,
                  "frases_descalificadoras": false
                },
                "Areas_de_mejora": [
                  "Recomendación 1",
                  "Recomendación 2",
                  "Recomendación 3",
                  "Recomendación 4",
                  "Recomendación 5"
                ]
              }
            }
            """)

        # Prompt de la conversación
        if leo_text.strip():
            convo = f"--- Participante ---\n{user_text}\n--- Médico (Leo) ---\n{leo_text}"
        else:
            convo = f"--- Participante ---\n{user_text}\n--- Médico (Leo) ---\n(sin diálogo disponible)"

        completion = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": convo}
            ],
            temperature=0.4
        )

        gpt_raw = completion.choices[0].message.content.strip()
        try:
            gpt_data = json.loads(gpt_raw)
        except json.JSONDecodeError:
            gpt_data = {}

        gpt_public = gpt_data.get(
            "public_summary",
            "⚠️ GPT no generó 'public_summary' (formato incorrecto)."
        )
        gpt_internal = gpt_data.get(
            "internal_analysis",
            {"overall_evaluation": "Sin 'internal_analysis' (formato incorrecto)."}
        )

    except OpenAIError as e:
        gpt_public   = f"⚠️ GPT error: {e}"
        gpt_internal = {"error": f"OpenAIError: {e}"}
        feedback_level = "error"
    except Exception as e:
        gpt_public   = f"⚠️ Error inesperado llamando a GPT: {e}"
        gpt_internal = {"error": f"Exception: {e}"}
        feedback_level = "error"
    else:
        feedback_level = "alto"

    # ───────────────────────────────────────────────────────
    #  Normalización mínima para UI interna
    # ───────────────────────────────────────────────────────
    def _norm_keys(d: dict) -> dict:
        import unicodedata, collections
        def clean(k):
            k = unicodedata.normalize("NFD", k).encode("ascii", "ignore").decode()
            return k.lower()
        return {clean(k): v for k, v in d.items()}

    fb_norm = _norm_keys(gpt_internal)

    internal_summary = {
        "overall_rh_summary": gpt_internal.get("overall_evaluation", ""),
        "knowledge_score": f"{kw_score}/8",
        "visual_presence": vis_int,
        "visual_percentage": vis_pct,
        "sales_model_simple_detection": {
            k: ('✅' if v else '❌') for k, v in sales_model_score.items()
        } | {"steps_applied_count": f"{sum(sales_model_score.values())}/4"},
        "active_listening_simple_detection":
            "Alta" if active_listening_score >= 4 else
            "Moderada" if active_listening_score >= 2 else "Baja",
        "disqualifying_phrases_detected": disq_flag,
        "gpt_detailed_feedback": fb_norm,
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

    return {
        "public":   public_block,
        "internal": internal_summary,
        "level":    feedback_level
    }
