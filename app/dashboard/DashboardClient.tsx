'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

// Interfaces para los datos que se reciben como props
interface SessionRecord {
  id?: number;
  scenario: string;
  user_transcript: string;
  avatar_transcript: string;
  coach_advice: string;
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

const SENTINELS = [
  'Video_Not_Available_Error',
  'Video_Processing_Failed',
  'Video_Missing_Error',
];

export default function DashboardClient({ initialData, error }: { initialData: DashboardData | null; error: string | null }) {
  const router = useRouter();

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-zinc-900 text-white">
        <h2 className="text-2xl text-red-500 mb-4">Error al Cargar los Datos del Dashboard</h2>
        <p className="text-zinc-400">No se pudo obtener la información desde el servidor.</p>
        <p className="text-zinc-500 mt-2 text-sm">Detalle: {error}</p>
      </div>
    );
  }

  if (!initialData) {
    return <div className="min-h-screen flex items-center justify-center bg-zinc-900 text-white">Cargando...</div>;
  }
  
  const { name: userName, email, user_token, sessions, used_seconds } = initialData;

  const records: SessionRecord[] = (sessions || []).map(s => ({
    ...s,
    video_s3: s.video_s3 && !SENTINELS.includes(s.video_s3) ? s.video_s3 : null,
    created_at: s.created_at ? new Date(s.created_at).toLocaleString() : '',
  }));
  
  const usedSeconds = used_seconds || 0;

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${String(secs).padStart(2, '0')}s`;
  };

  const maxSeconds = 1800;
  const defaultScenario = "Entrevista con el médico";

  return (
    <div className="dashboard-page-container">
      <style jsx>{`
        .dashboard-page-container { background-color: #f4f6fa; color: #333; font-family: 'Open Sans', sans-serif; }
        header { display: flex; align-items: center; gap: 16px; padding: 16px 32px; background: linear-gradient(90deg, #0c0e2c 0%, #003559 50%, #00bfff 100%); box-shadow: 0 2px 6px rgba(0,0,0,0.45); color: #fff; }
        header h1 { font-family: 'Montserrat', sans-serif; font-weight: 700; font-size: 28px; margin: 0; }
        header p { margin: 0; }
        .container-content { max-width: 1200px; margin: 0 auto; padding: 40px 32px; }
        .section-title { font: 600 24px 'Montserrat', sans-serif; margin: 40px 0 24px; border-bottom: 2px solid #00bfff; padding-bottom: 10px; color: #0c0e2c; }
        .info { background: #e9f0ff; padding: 15px; border-left: 4px solid #00bfff; margin-top: 20px; border-radius: 6px; color: #333; }
        .info h3 { color: #003559; margin-bottom: 10px; }
        .info ul { list-style-type: disc; margin-left: 20px; padding: 0; }
        .info li { margin-bottom: 5px; }
        .card-grid { display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }
        .card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 250px; text-align: center; transition: transform 0.2s ease; }
        .card:hover { transform: translateY(-5px); }
        .card h3 { margin: 10px 0; color: #0c0e2c; }
        .card button { padding: 10px 20px; border: none; background: #00bfff; color: white; border-radius: 5px; cursor: pointer; font-weight: bold; transition: background 0.2s ease, transform 0.1s ease; }
        .card button:hover { background: #009acd; transform: translateY(-1px); }
        .card button:disabled { background: gray; cursor: not-allowed; }
        .progress-bar { background: #e9ecef; border-radius: 8px; overflow: hidden; height: 25px; margin-top: 15px; border: 1px solid #dee2e6; }
        .progress-fill { height: 100%; background: linear-gradient(to right, #00bfff, #007bff); display: flex; align-items: center; justify-content: flex-end; padding-right: 10px; color: #fff; font-weight: bold; font-size: 0.9em; transition: width 0.4s ease-out; }
        .session-log { margin-top: 40px; }
        .session-entry { background: white; color: #333; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,.15); padding: 24px; margin-bottom: 40px; display: grid; grid-template-columns: 1fr; gap: 24px; }
        @media (min-width: 1024px) { .session-entry { grid-template-columns: 1fr 380px; } }
        .session-entry h3 { font: 600 20px 'Montserrat', sans-serif; color: #0c0e2c; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }
        .session-entry video { width: 100%; border: 1px solid #00bfff; border-radius: 12px; object-fit: cover; box-shadow: 0 4px 10px rgba(0,0,0,0.15); }
        .evaluation-box { margin-top: 20px; padding: 15px; border-radius: 8px; background: #e0f7fa; border-left: 5px solid #0099cc; color: #333; }
        .evaluation-box.tip-box, .evaluation-box.visual-feedback-box { background: #f9fbff; border-left: 4px solid #00bfff; }
      `}</style>
      <header>
        <h1>¡Bienvenido/a, {userName}!</h1>
        <p>Centro de entrenamiento virtual con Leo</p>
      </header>
      <div className="container-content">
        <h2 className="section-title">Selecciona tu entrenamiento</h2>
        <div className="info">
          <h3>📘 Instrucciones clave para tu sesión:</h3>
          <ul>
            <li>Al hacer clic en "Iniciar", serás conectado con el doctor virtual Leo.</li>
            <li>El cronómetro comienza automáticamente (8 minutos por sesión).</li>
            <li>Autoriza el acceso a tu cámara y micrófono.</li>
            <li>Sigue el modelo de ventas Da Vinci: saludo, necesidad, propuesta, cierre.</li>
          </ul>
        </div>
        <div className="card-grid">
          <div className="card">
            <h3>Entrevista con médico</h3>
            <Link
              href={{
                pathname: '/interactive-session',
                query: { name: userName, email: email, scenario: defaultScenario, token: user_token },
              }}
              passHref
            >
              <button>Iniciar</button>
            </Link>
          </div>
          <div className="card"><h3>Coaching</h3><button disabled>Muy pronto</button></div>
          <div className="card"><h3>Capacitación</h3><button disabled>Muy pronto</button></div>
        </div>
        <div className="info">
          <strong>⏱ Tiempo mensual utilizado:</strong>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${(usedSeconds / maxSeconds) * 100}%` }}></div>
          </div>
          <p>Usado: {formatTime(usedSeconds)} de {formatTime(maxSeconds)} minutos.</p>
        </div>
        <div className="session-log">
          <h2 className="section-title">Tus sesiones anteriores</h2>
          {records.length === 0 ? (
            <p>No has realizado sesiones todavía.</p>
          ) : (
            records.map((r, idx) => (
              <div key={idx} className="session-entry">
                <div>
                  <h3>{r.scenario}</h3>
                  <p className="session-info"><strong>Fecha:</strong> {r.created_at}</p>
                  <div className="evaluation-box"><p>{r.coach_advice}</p></div>
                  {r.tip && <div className="evaluation-box tip-box"><p><strong>Consejo:</strong> {r.tip}</p></div>}
                  {r.visual_feedback && <div className="evaluation-box visual-feedback-box"><p><strong>Feedback Visual:</strong> {r.visual_feedback}</p></div>}
                </div>
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