// File: app/dashboard/page.tsx
'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Cookies from 'js-cookie';
import Link from 'next/link';

interface SessionRecord {
  id?: number;
  scenario: string;
  user_transcript: string;
  avatar_transcript: string;
  coach_advice: string; // Coincide con 'evaluation' en Flask
  video_s3: string | null; // URL prefirmada del video S3
  created_at: string;
  tip: string; // Coincide directamente con 'tip' en Flask
  visual_feedback: string;
  duration: number; // Duración en segundos
}

const SENTINELS = [
  'Video_Not_Available_Error',
  'Video_Processing_Failed',
  'Video_Missing_Error',
];

export default function DashboardPage() {
  const [records, setRecords] = useState<SessionRecord[]>([]);
  const [usedSeconds, setUsedSeconds] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [userName, setUserName] = useState<string | null>(null);

  const router = useRouter();

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs.toString().padStart(2, '0')}s`;
  };

  useEffect(() => {
    const nameFromCookie = Cookies.get('user_name');
    const emailFromCookie = Cookies.get('user_email');
    const tokenFromCookie = Cookies.get('user_token');

    if (!nameFromCookie || !emailFromCookie || !tokenFromCookie) {
      router.push('/');
      return;
    }

    setUserName(nameFromCookie);

    (async () => {
      try {
        const apiBase = (process.env.NEXT_PUBLIC_FLASK_API_URL || '').trim();
        console.log('[Dashboard] API base:', apiBase);

        const res = await fetch(`${apiBase}/dashboard_data`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: nameFromCookie, email: emailFromCookie, token: tokenFromCookie }),
        });

        if (!res.ok) {
          throw new Error(await res.text());
        }

        const data = await res.json();
        const sessions: SessionRecord[] = Array.isArray(data.sessions) ? data.sessions : [];
        const used = typeof data.used_seconds === 'number' ? data.used_seconds : 0;

        const mappedSessions = sessions.map((s) => ({
          ...s,
          // Flask ya debería enviar `video_s3` como la URL prefirmada
          // Si Flask envía la clave, entonces:
          // video_s3: s.video_s3 && !SENTINELS.includes(s.video_s3) ? `${apiBase}/video/${s.video_s3}` : null,
          // Si Flask ya envía la URL completa, simplemente la usamos
          video_s3: s.video_s3 && !SENTINELS.includes(s.video_s3) ? s.video_s3 : null,

          // Asegurar valores por defecto y consistencia de nombres
          scenario: s.scenario || 'N/A',
          user_transcript: s.user_transcript || 'No hay transcripción del usuario.',
          avatar_transcript: s.avatar_transcript || 'No hay transcripción del avatar.',
          coach_advice: s.coach_advice || 'Análisis IA pendiente.',
          tip: s.tip || 'Consejo pendiente.',
          visual_feedback: s.visual_feedback || 'Análisis visual pendiente.',
          created_at: s.created_at ? new Date(s.created_at).toLocaleString() : 'Fecha no disponible', // Formatear fecha
          duration: s.duration || 0,
        }));

        setRecords(mappedSessions);
        setUsedSeconds(used);

        console.log("Dashboard Data Loaded:");
        console.log("Mapped Sessions:", mappedSessions);
        console.log("Used Seconds:", used);

      } catch (err: any) {
        console.error('[Dashboard] fetch error', err);
        alert(`Error al cargar el dashboard: ${err.message || err}`);
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  const maxSeconds = 1800; // 30 minutos

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: '#0c0e2c', color: '#e6e8ef' }}>
        Cargando...
      </div>
    );
  }

  const defaultScenario = "Entrevista con el médico"; // Escenario por defecto para el botón

  return (
    <div className="dashboard-page-container">
      <style jsx>{`
        /* Estilos generales */
        .dashboard-page-container {
          background-color: #f4f6fa;
          color: #333;
          font-family: 'Open Sans', sans-serif;
        }
        
        /* HEADER */
        header {
          display: flex;
          align-items: center;
          gap: 16px;
          padding: 16px 32px;
          background: linear-gradient(90deg, #0c0e2c 0%, #003559 50%, #00bfff 100%);
          box-shadow: 0 2px 6px rgba(0,0,0,0.45);
          color: #fff; /* Asegura el color del texto */
        }
        header h1 {
          font-family: 'Montserrat', sans-serif;
          font-weight: 700;
          font-size: 28px;
          margin: 0; /* Elimina márgenes por defecto */
        }
        header p {
          margin: 0; /* Elimina márgenes por defecto */
        }
        .container-content {
          max-width: 1200px;
          margin: 0 auto;
          padding: 40px 32px;
        }

        /* SECTION TITLE */
        .section-title {
          font: 600 24px 'Montserrat', sans-serif;
          margin: 40px 0 24px;
          border-bottom: 2px solid #00bfff;
          padding-bottom: 10px;
          color: #0c0e2c; /* Color del texto */
        }

        /* INFO BOX */
        .info {
          background: #e9f0ff;
          padding: 15px;
          border-left: 4px solid #00bfff;
          margin-top: 20px;
          border-radius: 6px;
          color: #333; /* Color de texto para el info box */
        }
        .info h3 {
            color: #003559; /* Color de título en info box */
            margin-bottom: 10px;
        }
        .info ul {
            list-style-type: disc;
            margin-left: 20px;
            padding: 0;
        }
        .info li {
            margin-bottom: 5px;
        }


        /* CARD GRID */
        .card-grid {
          display: flex;
          flex-wrap: wrap;
          gap: 20px;
          margin-top: 20px;
        }
        .card {
          background: white;
          border-radius: 10px;
          padding: 20px;
          box-shadow: 0 4px 10px rgba(0,0,0,0.1);
          width: 250px;
          text-align: center;
          transition: transform 0.2s ease;
        }
        .card:hover {
          transform: translateY(-5px);
        }
        .card h3 {
          margin: 10px 0;
          color: #0c0e2c; /* Color de título en cards */
        }
        .card button {
          padding: 10px 20px;
          border: none;
          background: #00bfff;
          color: white;
          border-radius: 5px;
          cursor: pointer;
          font-weight: bold;
          transition: background 0.2s ease, transform 0.1s ease;
        }
        .card button:hover {
          background: #009acd;
          transform: translateY(-1px);
        }
        .card button:active {
          transform: translateY(0);
        }
        .card button:disabled {
          background: gray;
          cursor: not-allowed;
        }

        /* PROGRESS BAR */
        .progress-bar {
          background: #e9ecef;
          border-radius: 8px;
          overflow: hidden;
          height: 25px;
          margin-top: 15px;
          border: 1px solid #dee2e6;
        }
        .progress-fill {
          height: 100%;
          background: linear-gradient(to right, #00bfff, #007bff); /* Degradado de color */
          display: flex;
          align-items: center;
          justify-content: flex-end;
          padding-right: 10px;
          color: #fff;
          font-weight: bold;
          font-size: 0.9em;
          transition: width 0.4s ease-out;
        }
        /* Colores de la barra de progreso basados en el porcentaje */
        .progress-fill[style*="width:"] { /* Detecta el style para aplicar color condicional */
            /* Se maneja con style={{ background: ... }} en el JSX */
        }


        /* SESSION LOG / ENTRIES */
        .session-log {
          margin-top: 40px;
        }
        .session-entry {
          background: white;
          color: #333; /* Color de texto para entries */
          border-radius: 16px;
          box-shadow: 0 8px 24px rgba(0,0,0,.15); /* Sombra más fuerte */
          padding: 24px;
          margin-bottom: 40px;
          display: grid;
          grid-template-columns: 1fr;
          gap: 24px;
        }
        @media (min-width: 1024px) {
          .session-entry {
            grid-template-columns: 1fr 380px; /* Layout de 2 columnas para desktop */
          }
        }
        .session-entry h3 {
          font: 600 20px 'Montserrat', sans-serif;
          color: #0c0e2c; /* Azul oscuro para el título de la sesión */
          margin-bottom: 12px;
          border-bottom: 1px solid #eee;
          padding-bottom: 8px;
        }
        .session-info strong {
          color: #555;
        }

        /* Video dentro de session-entry */
        .session-entry video {
          width: 100%;
          border: 1px solid #00bfff;
          border-radius: 12px;
          object-fit: cover;
          box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        }

        /* CHAT BUBBLES */
        .chat-log ul { list-style: none; padding: 0; margin: 0; }
        .chat-log li { display: flex; margin-bottom: 12px; }
        .bubble {
          padding: 10px 14px;
          border-radius: 12px;
          font-size: 15px;
          max-width: 100%;
        }
        .user .bubble { background: rgba(128,90,213,.15); border-left: 6px solid #805ad5; } /* Violeta */
        .doctor .bubble { background: rgba(0,191,255,.15); border-left: 6px solid #00bfff; } /* Cian */

        /* EVALUATIONS */
        .evaluation-box { /* Renamed to avoid direct conflict with .evaluation in admin.html if not needed */
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            background: #e0f7fa;
            border-left: 5px solid #0099cc;
            color: #333;
        }
        .evaluation-box.tip-box {
            background: #f9fbff;
            border-left: 4px solid #00bfff;
        }
        .evaluation-box.visual-feedback-box {
            background: #f9fbff;
            border-left: 4px solid #00bfff;
        }
        .evaluation-box strong { /* For titles inside evaluation boxes */
            color: #003559;
            display: block; /* Make it a block element for better spacing */
            margin-bottom: 5px;
        }
        .evaluation-box p {
            margin: 0;
            font-size: 0.95em;
        }

        /* TRANSCRIPTS (details tag) */
        details {
          margin-top: 15px;
          background: #f0f4f7; /* Fondo más claro para transcripts */
          padding: 10px;
          border-radius: 6px;
          border: 1px solid #e0e0e0;
        }
        summary {
          cursor: pointer;
          font-weight: bold;
          color: #003559; /* Azul oscuro para el título del summary */
        }
        details pre {
          white-space: pre-wrap;
          line-height: 1.4;
          font-size: 0.9em;
          color: #555;
          margin-top: 10px;
          border-top: 1px solid #eee;
          padding-top: 10px;
        }
        details strong { /* For "Tú" and "Leo" inside transcripts */
            color: #00bfff; /* Cian */
            display: block;
            margin-bottom: 5px;
        }

        /* FOOTER */
        footer {
          text-align: center;
          padding: 32px;
          margin-top: 50px;
          font-size: 0.9em;
          color: #777;
          background: #ffffff;
        }
        footer a {
          color: #00bfff;
          font-weight: 600;
        }
      `}</style>

      <header>
        {/* Usamos el nombre del usuario desde el estado */}
        <h1>¡Bienvenido/a, {userName}!</h1>
        <p>Centro de entrenamiento virtual con Leo</p>
      </header>

      <div className="container-content">
        <h2 className="section-title">Selecciona tu entrenamiento</h2>

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '20px', marginTop: '20px', flexWrap: 'wrap' }}>
          <div className="info" style={{ flex: 1, minWidth: '300px' }}>
            <h3>📘 Instrucciones clave para tu sesión:</h3>
            <ul style={{ textAlign: 'left', lineHeight: '1.6', paddingLeft: '20px', listStyleType: 'disc' }}>
              <li>🖱️ Al hacer clic en <strong>"Iniciar"</strong>, serás conectado con el doctor virtual Leo.</li>
              <li>⏱️ El cronómetro comienza automáticamente (8 minutos por sesión).</li>
              <li>🎥 Autoriza el acceso a tu <strong>cámara</strong> y <strong>micrófono</strong> cuando se te pida.</li>
              <li>👨‍⚕️ Haz clic una vez en la ventana del doctor para activarlo. Haz clic en el micrófono y comienza la conversación médica.</li>
              <li>🗣️ Habla con claridad y presenta tu producto de forma profesional.</li>
              <li>🤫 Cuando termines de hablar, espera la respuesta del Dr. Leo, él sabe cuándo contestar</li>
              <li>🎤 Si quieres volver a hablar, haz clic otra vez en el micro de la ventana del doctor y continúa.</li>
              <li>🎯 Sigue el modelo de ventas <strong>Da Vinci</strong>: saludo, necesidad, propuesta, cierre.</li>
            </ul>
            <p style={{ marginTop: '10px' }}>Tu sesión será evaluada automáticamente por IA. ¡Aprovecha cada minuto!</p>
          </div>
          <video controls autoPlay muted className="info-video">
            <source src="/video_intro.mp4" type="video/mp4" /> {/* Asegúrate que este video está en tu carpeta public/ */}
            Tu navegador no soporta la reproducción de video.
          </video>
        </div>

        <div className="card-grid">
          <div className="card">
            <h3>Entrevista con médico</h3>
            <Link
              href={{
                pathname: '/interactive-session',
                query: {
                  name: Cookies.get('user_name'),
                  email: Cookies.get('user_email'),
                  scenario: defaultScenario,
                  token: Cookies.get('user_token'),
                },
              }}
              passHref
            >
              <button>Iniciar</button>
            </Link>
          </div>

          <div className="card">
            <h3>Coaching para representante</h3>
            <button disabled>Muy pronto</button>
          </div>

          <div className="card">
            <h3>Capacitación farmacéutico</h3>
            <button disabled>Muy pronto</button>
          </div>
        </div>

        <div className="info">
          <strong>⏱ Tiempo mensual utilizado:</strong><br />
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${(usedSeconds / maxSeconds) * 100}%`,
                background:
                  usedSeconds >= maxSeconds * 0.9
                    ? '#ff4d4d'
                    : usedSeconds >= maxSeconds * 0.7
                    ? 'orange'
                    : '#00bfff',
              }}
            ></div>
          </div>
          <p style={{ marginTop: '8px' }}>
            Usado: {formatTime(usedSeconds)} de {formatTime(maxSeconds)} minutos.
          </p>
        </div>

        <div className="session-log">
          <h2 className="section-title">Tus sesiones anteriores</h2>
          {records.length === 0 ? (
            <p style={{ color: 'gray' }}>No has realizado sesiones todavía. ¡Comienza una con Leo!</p>
          ) : (
            records.map((r, idx) => (
              <div key={idx} className="session-entry">
                {/* Contenido de la sesión */}
                <div>
                  <h3>{userName} <span style={{ fontWeight: 'normal', color: '#777' }}>({Cookies.get('user_email')})</span></h3>
                  <p className="session-info">
                    <strong>Escenario:</strong> {r.scenario}<br />
                    <strong>Fecha:</strong> {r.created_at}
                  </p>

                  {/* CHAT LOG */}
                  <div className="chat-log">
                    <p style={{ marginTop: '15px' }}><strong>👤 Usuario:</strong></p>
                    <ul>
                      {r.user_transcript.split('\n').filter(s => s.trim()).map((segment, segIdx) => (
                        <li key={`user-seg-${idx}-${segIdx}`} className="user">
                          <span className="bubble">{segment}</span>
                        </li>
                      ))}
                    </ul>

                    <p style={{ marginTop: '8px' }}><strong>🩺 Doctora:</strong></p>
                    <ul>
                      {r.avatar_transcript.split('\n').filter(s => s.trim()).map((segment, segIdx) => (
                        <li key={`doctor-seg-${idx}-${segIdx}`} className="doctor">
                          <span className="bubble">{segment}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* RESUMENES */}
                  <div className="evaluation-box">
                    <strong>Resumen para usuario:</strong><br />{r.coach_advice}
                  </div>

                  {r.tip && (
                    <div className="evaluation-box tip-box">
                      <strong>🧠 Consejo personalizado de Leo:</strong>
                      <p>{r.tip}</p>
                    </div>
                  )}

                  {r.visual_feedback && (
                    <div className="evaluation-box visual-feedback-box">
                      <strong>👁️ Retroalimentación visual:</strong>
                      <p>{r.visual_feedback}</p>
                    </div>
                  )}

                  {/* BOTÓN ELIMINAR (Solo si es aplicable al usuario final, si no, remover) */}
                  {/* <div style={{marginTop:'20px',textAlign:'right'}}>
                    <button type="button" style={{background:'#dc3545',color:'white',padding:'8px 15px',borderRadius:'5px',border:'none',cursor:'pointer'}}>
                      🗑️ Eliminar Sesión
                    </button>
                  </div> */}
                </div>

                {/* VIDEO */}
                <div>
                  {r.video_s3 ? (
                    <video controls className="session-entry-video">
                      <source src={r.video_s3} type="video/webm" />
                      Tu navegador no soporta video.
                    </video>
                  ) : (
                    <p style={{ color: 'gray', textAlign: 'center' }}>⏳ Esta sesión aún se está procesando o no tiene video.</p>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <footer>
        <p>Desarrollado por <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer">Teams</a> &copy; 2025</p>
      </footer>
    </div>
  );
}