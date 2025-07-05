'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

// ──────────────────────────────────────────────
// 1.  Tipos
// ──────────────────────────────────────────────
interface SessionRecord {
  id?: number;
  scenario: string;
  user_transcript: string;
  avatar_transcript: string;
  coach_advice: string;
  rh_evaluation?: string;              // ⬅️ nuevo
  video_s3: string | null;
  created_at: string;
  tip: string;
  visual_feedback: string;
  duration: number;
}

interface DashboardData {
  name: string;
  email: string;
  user_token: string;
  sessions: SessionRecord[];
  used_seconds: number;
}

// Valores especiales que indican que el video no está listo
const SENTINELS = [
  'Video_Not_Available_Error',
  'Video_Processing_Failed',
  'Video_Missing_Error',
];

// ──────────────────────────────────────────────
// 2.  Componente principal
// ──────────────────────────────────────────────
export default function DashboardClient({
  initialData,
  error,
}: {
  initialData: DashboardData | null;
  error: string | null;
}) {
  const router = useRouter();

  /*  Pantalla de error  */
  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center">
        <style jsx global>{`
          html,
          body {
            margin: 0;
            padding: 0;
            background: #0c0e2c;
            color: #fff;
            font-family: 'Open Sans', sans-serif;
          }
        `}</style>
        <h2 className="text-2xl text-red-500 mb-4">Error al cargar datos</h2>
        <p>{error}</p>
      </div>
    );
  }

  if (!initialData) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <style jsx global>{`
          html,
          body {
            margin: 0;
            padding: 0;
            background: #0c0e2c;
            color: #fff;
            font-family: 'Open Sans', sans-serif;
          }
        `}</style>
        Cargando…
      </div>
    );
  }

  // ─── Destructuración segura ─────────────────
  const {
    name: userName,
    email,
    user_token,
    sessions = [],
    used_seconds: usedSeconds = 0,
  } = initialData;

  const records: SessionRecord[] = sessions.map((s) => ({
    ...s,
    video_s3: s.video_s3 && !SENTINELS.includes(s.video_s3) ? s.video_s3 : null,
    created_at: s.created_at ? new Date(s.created_at).toLocaleString() : '',
  }));

  // Utilidades
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs.toString().padStart(2, '0')}s`;
  };

  const maxSeconds = 1_800; // 30 min
  const defaultScenario = 'Coaching con gerente';

  // ────────────────────────────────────────────
  // 3.  Render
  // ────────────────────────────────────────────
  return (
    <>
      {/* =========== ESTILOS GLOBAL ============ */}
      <style jsx global>{`
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&family=Open+Sans:wght@400;600&display=swap');
html,
  body,
  .dashboard-page-container {
    background: #f4f6fa !important;   /* gris claro corporativo   */
    color: #222 !important;           /* texto oscuro legible    */
  }
        :root {
          --primary-dark: #0c0e2c;
          --primary-mid: #003559;
          --primary-light: #00bfff;
          --secondary-red: #cc0000;
          --text-dark: #222;
          --bg-gray: #f4f6fa;
          --bg-white: #ffffff;
          --shadow-lg: rgba(0, 0, 0, 0.15);
        }

        html,
        body {
          margin: 0;
          padding: 0;
          background: var(--bg-gray);
          color: var(--text-dark);
          font-family: 'Open Sans', sans-serif;
        }

        /* Header */
        header {
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 4px;
          padding: 16px 32px;
          background: linear-gradient(
            90deg,
            var(--primary-dark) 0%,
            var(--primary-mid) 50%,
            var(--primary-light) 100%
          );
          box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
          color: #fff;
        }
        header h1 {
          font-family: 'Montserrat', sans-serif;
          font-weight: 700;
          font-size: 28px;
          margin: 0;
        }

        /* General containers */
        .container-content {
          max-width: 1200px;
          margin: 0 auto;
          padding: 40px 32px;
        }
        .section-title {
          font: 600 24px 'Montserrat', sans-serif;
          margin: 40px 0 24px;
          border-bottom: 2px solid var(--primary-light);
          padding-bottom: 10px;
          color: var(--primary-dark);
        }

        /* Cards */
        .card-grid {
          display: flex;
          flex-wrap: wrap;
          gap: 20px;
          margin-top: 20px;
        }
        .card {
          background: var(--bg-white);
          border-radius: 10px;
          padding: 20px;
          box-shadow: 0 4px 10px var(--shadow-lg);
          width: 260px;
          text-align: center;
          transition: transform 0.2s ease;
        }
        .card:hover {
          transform: translateY(-5px);
        }
        .card h3 {
          margin: 10px 0;
          color: var(--primary-dark);
        }
        .card button {
          padding: 10px 20px;
          border: none;
          background: var(--primary-light);
          color: #000;
          border-radius: 5px;
          cursor: pointer;
          font-weight: 600;
          transition: background 0.2s ease, transform 0.1s ease;
        }
        .card button:hover {
          background: #009acd;
          transform: translateY(-1px);
        }

        /* Progress bar */
        .progress-bar {
          background: #e9ecef;
          border-radius: 8px;
          overflow: hidden;
          height: 25px;
          margin-top: 12px;
          border: 1px solid #dee2e6;
        }
        .progress-fill {
          height: 100%;
          background: linear-gradient(to right, #00bfff, #007bff);
          display: flex;
          align-items: center;
          justify-content: flex-end;
          padding-right: 10px;
          color: #fff;
          font-weight: bold;
          font-size: 0.9em;
          transition: width 0.4s ease-out;
        }

        /* Session cards */
        .session-entry {
          background: var(--bg-white);
          border-radius: 16px;
          box-shadow: 0 8px 24px var(--shadow-lg);
          padding: 24px;
          margin-bottom: 40px;
          display: grid;
          grid-template-columns: 1fr;
          gap: 24px;
        }
        @media (min-width: 1024px) {
          .session-entry {
            grid-template-columns: 1fr 380px;
          }
        }
        .session-entry h3 {
          font: 600 20px 'Montserrat', sans-serif;
          color: var(--primary-dark);
          margin-bottom: 12px;
          border-bottom: 1px solid #eee;
          padding-bottom: 8px;
        }
        .session-entry video {
          width: 100%;
          border: 1px solid var(--primary-light);
          border-radius: 12px;
          object-fit: cover;
          box-shadow: 0 4px 10px var(--shadow-lg);
        }

        .evaluation-box {
          margin-top: 15px;
          padding: 15px;
          border-radius: 8px;
          background: #e0f7fa;
          border-left: 5px solid #0099cc;
        }
        .evaluation-box.rh-box {
          background: #ffeeee;
          border-left-color: var(--secondary-red);
        }
        .evaluation-box.tip-box,
        .evaluation-box.visual-feedback-box {
          background: #f9fbff;
          border-left-color: var(--primary-light);
        }
      `}</style>

      {/* ---------- Página ---------- */}
      <div className="dashboard-page-container">
        {/* ---------- Encabezado ---------- */}
        <header>
          <h1>¡Bienvenido/a, {userName}!</h1>
          <p>Centro de entrenamiento virtual con Leo</p>
        </header>

        <div className="container-content">
          {/* ---------- Selección de escenario ---------- */}
          <h2 className="section-title">Selecciona tu entrenamiento</h2>

          <div className="card-grid">
            <div className="card">
              <h3>Entrevista con médico</h3>
              <Link
                href={{
                  pathname: '/interactive-session',
                  query: {
                    name: userName,
                    email,
                    scenario: defaultScenario,
                    token: user_token,
                  },
                }}
                passHref
              >
                <button>Iniciar</button>
              </Link>
            </div>
          </div>

          {/* ---------- Consumo de minutos ---------- */}
          <div className="info" style={{ marginTop: '30px' }}>
            <strong>⏱ Tiempo mensual usado:</strong>
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${(usedSeconds / maxSeconds) * 100}%` }}
              />
            </div>
            <p>
              {formatTime(usedSeconds)} / {formatTime(maxSeconds)}
            </p>
          </div>

          {/* ---------- Historial de sesiones ---------- */}
          <div className="session-log">
            <h2 className="section-title">Tus sesiones anteriores</h2>

            {records.length === 0 ? (
              <p>No has realizado sesiones todavía.</p>
            ) : (
              records.map((r) => (
                <div key={r.id ?? r.created_at} className="session-entry">
                  {/* Columna A */}
                  <div>
                    <h3>{r.scenario}</h3>
                    <p style={{ margin: 0, fontSize: '0.9rem' }}>
                      <strong>Fecha:</strong> {r.created_at}
                    </p>

                    {/* Resumen IA público */}
                    <div className="evaluation-box">
                      <p>{r.coach_advice || 'Análisis IA pendiente…'}</p>
                    </div>

                    {/* Comentario RH */}
                    {r.rh_evaluation && (
                      <div className="evaluation-box rh-box">
                        <p>
                          <strong>Comentario RH:</strong> {r.rh_evaluation}
                        </p>
                      </div>
                    )}

                    {/* Tip */}
                    {r.tip && (
                      <div className="evaluation-box tip-box">
                        <p>
                          <strong>Consejo:</strong> {r.tip}
                        </p>
                      </div>
                    )}

                    {/* Feedback visual */}
                    {r.visual_feedback && (
                      <div className="evaluation-box visual-feedback-box">
                        <p>
                          <strong>Feedback visual:</strong>{' '}
                          {r.visual_feedback}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Columna B: video */}
                  <div>
                    {r.video_s3 ? (
                      <video controls src={r.video_s3} />
                    ) : (
                      <p>Video no disponible o procesando.</p>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </>
  );
}
