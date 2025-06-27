// app/dashboard/page.tsx (Este es el nuevo archivo)

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import DashboardClient from './DashboardClient'; // Importamos el componente que acabas de renombrar

// Definimos la estructura de los datos para mayor claridad
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
    sessions: SessionRecord[];
    used_seconds: number;
}

// Esta función se ejecuta en el servidor para obtener los datos
async function getDashboardData(jwt: string) {
  const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL;
  if (!flaskApiUrl) {
    throw new Error('La URL del backend no está configurada.');
  }

  const res = await fetch(`${flaskApiUrl}/dashboard_data`, {
    method: 'GET',
    headers: {
        'Authorization': `Bearer ${jwt}`,
        'Content-Type': 'application/json',
    },
    cache: 'no-store', // Muy importante para que los datos siempre estén actualizados
  });

  if (!res.ok) {
    const errorText = await res.text();
    console.error(`Error del backend de Flask: ${res.status}`, errorText);
    throw new Error(`Error del backend: ${res.status} - ${errorText}`);
  }

  return res.json();
}


// Este es el componente principal de la página, es un Server Component por defecto
export default async function DashboardPage() {
  const jwt = cookies().get('jwt')?.value;

  // Si no hay token, lo redirigimos a la página de inicio
  if (!jwt) {
    redirect('/');
  }

  let initialData: DashboardData | null = null;
  let error: string | null = null;

  try {
    // Aquí llamamos a la función para obtener los datos de forma segura
    initialData = await getDashboardData(jwt);
  } catch (err: any) {
    console.error('[Dashboard Page Server] Error al obtener datos:', err.message);
    error = err.message; // Guardamos el error para mostrarlo
  }

  // Renderizamos el componente de cliente, pasándole los datos (o el error)
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center bg-zinc-900 text-white">Cargando...</div>}>
      <DashboardClient initialData={initialData} error={error} />
    </Suspense>
  );
}

// Esto asegura que la página siempre se renderice en el servidor en cada petición
export const dynamic = 'force-dynamic';