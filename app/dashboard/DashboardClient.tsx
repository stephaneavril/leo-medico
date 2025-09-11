'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

type PublicComment = {
  id: number;
  author: string;
  body: string;
  created: string; // "YYYY-MM-DD HH:MM"
};

interface SessionRecord {
  id?: number;
  scenario: string;
  user_transcript: string;
  avatar_transcript: string;
  coach_advice: string;
  rh_evaluation?: string;
  video_s3: string | null;
  created_at: string;
  tip: string;
  visual_feedback: string;
  duration: number;
  visible_to_user?: boolean;
  comments_public?: PublicComment[];
}

interface DashboardData {
  name: string;
  email: string;
  user_token: string;
  sessions: SessionRecord[];
  used_seconds: number;
}

const SENTINELS = ['Video_Not_Available_Error', 'Video_Processing_Failed', 'Video_Missing_Error'];

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
      <div className="min-h-screen flex flex-col items-center justify-center">
        <style jsx global>{`
          html, body { margin: 0; padding: 0; background: #0c0e2c; color: #fff; font-family: 'Open Sans', sans-serif; }
        `}</style>
        <h2 className="text-2xl text-red-500 mb-2">Error al cargar datos</h2>
        <p>{error}</p>
      </div>
    );
  }

  if (!initialData) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <style jsx global>{`
          html, body { margin: 0; padding: 0; background: #0c0e2c; color: #fff; font-family: 'Open Sans', sans-serif; }
        `}</style>
        Cargando…
      </div>
    );
  }

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
    comments_public: Array.isArray(s.comments_public) ? s.comments_public : [],
  }));

  // Solo mostrar sesiones que tengan algo publicado para el usuario
  const visibleRecords = records.filter((r) => (r.coach_advice || r.rh_evaluation) && (r.visible_to_user ?? true));

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs.toString().padStart(2, '0')}s`;
  };

  const maxSeconds = 1_800; // 30 min
  const defaultScenario = 'Entrevista con médico';

  return (
    <>
      <style jsx global>{`
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@500;700&family=Open+Sans:wght@400;600&display=swap');

        :root {
          --primary-dark: #0c0e2c;
          --primary-mid: #003559;
          --primary-light: #00bfff;
          --text-dark: #222;
          --bg-gray: #f4f6fa;
          --bg-white: #ffffff;
          --shadow-lg: rgba(0, 0, 0, 0.12);
        }

        html, body, .dashboard-page-container {
          background: var(--bg-gray) !important;
          color: var(--text-dark) !important;
          margin: 0;
          padding: 0;
          font-family: 'Open Sans', sans-serif;
        }

        header {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 18px 28px;
          background: linear-gradient(90deg, var(--primary-dark), var(--primary-mid) 55%, var(--primary-light));
          color: #fff;
          box-shadow: 0 2px 6px rgba(0,0,0,.45);
        }
        header h1 {
          margin: 0;
          font-family: 'Montserrat', sans-serif;
          font-weight: 700;
          font-size: 26px;
          letter-spacing: .2px;
        }
        header p { margin: 0; opacity: .9; }

        .container-content { max-width: 1100px; margin: 0 auto; padding: 32px 22px 40px; }

        .section-title {
          font: 700 22px 'Montserrat', sans-serif;
          margin: 28px 0 16px;
          color: var(--primary-dark);
          display: inline-block;
          border-bottom: 3px solid var(--primary-light);
          padding-bottom: 6px;
        }

        .card-grid { display: flex; flex-wrap: wrap; gap: 16px; }
        .card {
          background: var(--bg-white);
          border-radius: 14px;
          padding: 18px;
          width: 260px;
          box-shadow: 0 8px 22px var(--shadow-lg);
          transition: transform .18s ease;
          border: 1px solid #eef2f7;
        }
        .card:hover { transform: translateY(-3px); }
        .card h3 { margin: 4px 0 12px; font-family: 'Montserrat'; color: var(--primary-dark); }
        .card button {
          padding: 10px 16px;
          border: none; border-radius: 8px;
          background: var(--primary-light); color:#000; font-weight: 700;
          cursor: pointer; transition: background .15s ease, transform .1s ease;
          width: 100%;
        }
        .card button:hover { background: #00a4e6; transform: translateY(-1px); }

        .progress-bar {
          background: #e9eef4; border-radius: 10px; overflow: hidden; height: 18px;
          border: 1px solid #dbe4ee; margin: 10px 0 6px;
        }
        .progress-fill {
          height: 100%; background: linear-gradient(to right, #00bfff, #007bff);
          display: flex; align-items: center; justify-content: flex-end;
          padding-right: 8px; color: #fff; font-weight: 700; font-size: 12px;
          transition: width .35s ease-out;
        }

        .session-entry {
          background: var(--bg-white);
          border-radius: 16px;
          box-shadow: 0 10px 28px var(--shadow-lg);
          padding: 20px;
          margin-bottom: 24px;
          border: 1px solid #eef2f7;
        }
        .session-entry h3 {
          margin: 0 0 6px;
          font: 700 18px 'Montserrat', sans-serif; color: var(--primary-dark);
        }
        .muted { color: #566; font-size: .92rem; }

        .evaluation-box {
          margin-top: 14px; padding: 14px; border-radius: 12px;
          background: #eaf7fb; border-left: 6px solid #00a4e6;
        }
        .evaluation-box.rh-box { background:#fff1f1; border-left-color:#cc0000; }
        .evaluation-box.tip-box { background:#f7fbff; border-left-color:#00bfff; }

        /* Caja ligera para los 7 pasos */
        .howto-box{
          background: var(--bg-white);
          border: 1px solid #eef2f7;
          border-radius: 14px;
          box-shadow: 0 6px 18px var(--shadow-lg);
          padding: 16px 18px;
          margin: 10px 0 22px;
        }
        .howto-box h3{
          margin: 0 0 10px;
          font: 700 18px 'Montserrat', sans-serif;
          color: var(--primary-dark);
        }
        .howto-box ol{ margin: 8px 0 0; padding-left: 20px; line-height: 1.6; }
        .howto-box li{ margin: 6px 0; }

        /* ⚠️ Aviso temporal (paso adicional) */
        .notice-callout{
          display: flex;
          gap: 12px;
          align-items: flex-start;
          background: #fff8e6;
          border: 1px solid #ffd27a;
          border-left: 6px solid #ff9f0a;
          color: #5a4100;
          border-radius: 12px;
          padding: 14px 16px;
          box-shadow: 0 6px 18px var(--shadow-lg);
          margin: 0 0 18px;
        }
        .notice-callout strong { color: #4a3500; }
        .notice-badge{
          min-width: 28px; height: 28px; border-radius: 50%;
          background: #ff9f0a; color:#fff; display:flex; align-items:center; justify-content:center;
          font-weight: 800; margin-top: 2px;
        }
        .notice-steps{ margin: 0; padding-left: 18px; line-height: 1.55; }
        .notice-steps li{ margin: 4px 0; }

        /* Historial */
        .history-box {
          margin-top: 12px;
          padding: 12px;
          border-radius: 12px;
          background: #f6f9ff;
          border: 1px solid #e4ecff;
        }
        .history-item {
          background: #fff;
          border: 1px solid #e6eefc;
          border-radius: 10px;
          padding: 10px 12px;
        }
      `}</style>

      <div className="dashboard-page-container">
        <header>
          <h1>¡Bienvenido/a, {userName}!</h1>
          <p>Centro de entrenamiento con Leo</p>
        </header>

        <div className="container-content">
          <h2 className="section-title">Selecciona tu entrenamiento</h2>

          {/* ⚠️ Aviso temporal: paso adicional para activar micrófono */}
          <section className="notice-callout" role="status" aria-live="polite">
            <div className="notice-badge">!</div>
            <div>
              <p style={{margin: 0}}>
                <strong>Aviso:</strong> por ahora hay un <strong>paso adicional</strong> para que tu computadora
                entienda que quieres hablar con el avatar.
              </p>
              <ol className="notice-steps">
                <li>Al entrar, haz clic en <strong>Iniciar Chat de Voz</strong>.</li>
                <li>Cuando veas al avatar y tu cámara, haz clic en <strong>Text Chat</strong>.</li>
                <li>Inmediatamente vuelve a hacer clic en <strong>Voice Chat</strong>.</li>
              </ol>
              <p style={{margin: '6px 0 0'}}>Desde ese momento, puedes decir: <em>“Hola, doctora”</em> y te responderá por voz.</p>
            </div>
          </section>

          {/* === Pasos de uso === */}
          <section className="howto-box">
            <h3>Cómo aprovechar a Leo en 7 pasos</h3>
            <ol>
              <li>Elige el escenario de entrenamiento que necesitas.</li>
              <li>Activa cámara y micrófono; verifica que todo funcione.</li>
              <li>Haz clic en <strong>Iniciar</strong> y saluda al avatar con “Buenos días, Doctora o Buenos días Doctor”.</li>
              <li>Expón tu objetivo (p. ej., beneficio, evidencia, o cierre de la visita).</li>
              <li>Aplica el modelo Da Vinci y aborda objeciones brevemente.</li>
              <li>Dispones de 8 minutos por sesión y 30 minutos al mes.</li>
              <li>Al terminar, desconéctate. Capacitación revisará con IA y publicará tu resumen para la siguiente práctica.</li>
            </ol>
          </section>

          <div className="card-grid">
            <div className="card">
              <h3>Entrevista con Médico</h3>
              <Link
                href={{
                  pathname: '/interactive-session',
                  query: { name: userName, email, scenario: defaultScenario, token: user_token },
                }}
                passHref
              >
                <button>Iniciar</button>
              </Link>
            </div>
          </div>

          <div style={{ marginTop: '22px' }}>
            <strong>⏱ Tiempo mensual usado</strong>
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${(usedSeconds / maxSeconds) * 100}%` }} />
            </div>
            <div className="muted">{formatTime(usedSeconds)} / {formatTime(maxSeconds)}</div>
          </div>

          <h2 className="section-title">Tus sesiones anteriores</h2>

          {visibleRecords.length === 0 ? (
            <p className="muted">Aún no tienes retroalimentación disponible.</p>
          ) : (
            visibleRecords.map((r) => (
              <div key={r.id ?? r.created_at} className="session-entry">
                <h3>{r.scenario}</h3>
                <div className="muted">Fecha: {r.created_at}</div>

                {r.coach_advice && (
                  <div className="evaluation-box">
                    <p style={{ marginTop: 0, marginBottom: 8 }}>
                      <strong>Resumen de tu sesión</strong>
                    </p>
                    <p style={{ opacity: 0.9, marginTop: 0 }}>
                      Gracias por entrenar con Leo. Observaciones para tu próxima práctica:
                    </p>
                    <p style={{ marginBottom: 0 }}>{r.coach_advice}</p>
                  </div>
                )}

                {r.rh_evaluation && (
                  <div className="evaluation-box rh-box">
                    <p style={{ marginTop: 0, marginBottom: 8 }}>
                      <strong>Mensaje de Capacitación</strong>
                    </p>
                    <p style={{ marginBottom: 0 }}>{r.rh_evaluation}</p>
                  </div>
                )}

                {/* Historial público de comentarios */}
                {r.visible_to_user && (
                  <div className="history-box">
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold text-slate-600">Historial de comentarios</div>
                      <div className="text-xs text-slate-500">{r.comments_public?.length || 0} registros</div>
                    </div>
                    {(!r.comments_public || r.comments_public.length === 0) ? (
                      <p className="mt-2 text-slate-600 text-sm" style={{ margin: 0 }}>Aún no hay historial.</p>
                    ) : (
                      <ul className="mt-2 space-y-2" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                        {r.comments_public.map((c) => (
                          <li key={c.id} className="history-item">
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-medium text-slate-700">{c.author || 'Capacitación'}</span>
                              <span className="text-[11px] text-slate-500">{c.created}</span>
                            </div>
                            <p className="mt-1 text-slate-800 text-sm" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                              {c.body}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}

                {r.tip && (
                  <div className="evaluation-box tip-box">
                    <p style={{ marginTop: 0 }}><strong>Idea para tu próxima práctica</strong></p>
                    <p style={{ marginBottom: 0 }}>{r.tip}</p>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
