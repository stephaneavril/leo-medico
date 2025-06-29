// app/dashboard/page.tsx

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import DashboardClient from './DashboardClient';

// Interfaces para tipar los datos que vienen del backend
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

// Función que se ejecuta en el servidor para llamar al backend de Flask
async function getDashboardData(jwt: string): Promise<DashboardData> {
  const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL;
  if (!flaskApiUrl) {
    throw new Error('La URL del backend no está configurada.');
  }

  const res = await fetch(`${flaskApiUrl}/dashboard_data`, {
    method: 'GET',
    headers: {
        'Authorization': `Bearer ${jwt}`, // El token JWT se usa para la autorización
        'Content-Type': 'application/json',
    },
    cache: 'no-store', // Asegura que los datos siempre sean frescos
  });

  if (!res.ok) {
    const errorText = await res.text();
    console.error(`Error del backend de Flask: ${res.status}`, errorText);
    throw new Error(`Error del backend: ${res.status} - ${errorText}`);
  }

  return res.json();
}

// Componente principal de la página (Server Component)
export default async function DashboardPage() {
    const jwt = (await cookies()).get('jwt')?.value;

  // Si no hay token JWT en las cookies, el usuario no está autenticado, redirigir.
  if (!jwt) {
    redirect('/');
  }

  let initialData: DashboardData | null = null;
  let error: string | null = null;

  try {
    // Llamamos a la función para obtener todos los datos necesarios del backend
    initialData = await getDashboardData(jwt);
  } catch (err: any) {
    console.error('[Dashboard Page Server] Error al obtener datos:', err.message);
    error = err.message;
  }

  // Renderizamos el componente de cliente, pasándole los datos (o el error) como props.
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center bg-zinc-900 text-white">Cargando...</div>}>
      <DashboardClient initialData={initialData} error={error} />
    </Suspense>
  );
}

// Asegura que la página siempre se renderice en el servidor en cada petición
export const dynamic = 'force-dynamic';