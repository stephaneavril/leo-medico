'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

// ──────────────────────────────────────────────
// 1.  Tipos
// ──────────────────────────────────────────────
interface SessionRecord {
  id?: number;
  scenario: string;
  user_transcript: string;
  avatar_transcript: string;
  coach_advice: string;
  rh_evaluation?: string;              // ⬅️ nuevo
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
// 2.  Componente principal
// ──────────────────────────────────────────────
export default function DashboardClient({
  initialData,
  error,
}: {
  initialData: DashboardData | null;
  error: string | null;
}) {
  const router = useRouter();

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-zinc-900 text-white">
        <h2 className="text-2xl text-red-500 mb-4">Error al cargar los datos</h2>
        <p className="text-zinc-400">{error}</p>
      </div>
    );
  }

  if (!initialData) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-900 text-white">
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

  const maxSeconds = 1_800;           // 30 min
  const defaultScenario = 'Coaching con gerente';

  // ────────────────────────────────────────────
  // 3.  Render
  // ────────────────────────────────────────────
  return (
    <div className="dashboard-page-container">
      {/* ======== ESTILOS EN LINEA PARA NEXT.JS ======== */}
      <style jsx>{`
        /* recortado por brevedad; usa tu CSS original */
      `}</style>

      {/* ---------- Encabezado ---------- */}
      <header>
        <h1>¡Bienvenido/a, {userName}!</h1>
        <p>Centro de entrenamiento virtual con Leo</p>
      </header>

      <div className="container-content">
        {/* ---------- Selección de escenario ---------- */}
        <h2 className="section-title">Selecciona tu entrenamiento</h2>
        {/* … tarjeta Iniciar … */}
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
        <div className="info">
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
                  <p className="session-info">
                    <strong>Fecha:</strong> {r.created_at}
                  </p>

                  {/* Resumen IA público */}
                  <div className="evaluation-box">
                    <p>{r.coach_advice || 'Análisis IA pendiente…'}</p>
                  </div>

                  {/* Comentario RH */}
                  {r.rh_evaluation && (
                    <div className="evaluation-box rh-box">
                      <p>
                        <strong>Comentario RH:</strong> {r.rh_evaluation}
                      </p>
                    </div>
                  )}

                  {/* Tip y feedback visual */}
                  {r.tip && (
                    <div className="evaluation-box tip-box">
                      <p>
                        <strong>Consejo:</strong> {r.tip}
                      </p>
                    </div>
                  )}
                  {r.visual_feedback && (
                    <div className="evaluation-box visual-feedback-box">
                      <p>
                        <strong>Feedback visual:</strong> {r.visual_feedback}
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
  );
}
