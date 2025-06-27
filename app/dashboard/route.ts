// app/api/dashboard/route.ts

import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

export async function GET() {
  const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL;
  if (!flaskApiUrl) {
    return NextResponse.json(
      { error: 'La URL del backend no está configurada.' },
      { status: 500 }
    );
  }

  // 1. Obtenemos el JWT que el middleware guardó en las cookies.
  // Esto se ejecuta de forma segura en el servidor de Next.js.
  const jwt = cookies().get('jwt')?.value;

  if (!jwt) {
    return NextResponse.json({ error: 'Token no encontrado. Inicie sesión de nuevo.' }, { status: 401 });
  }

  try {
    // 2. Hacemos la llamada a Flask, pasando el JWT en el header de autorización.
    const flaskResponse = await fetch(`${flaskApiUrl}/dashboard_data`, {
        method: 'GET', // Cambiado a GET para seguir las mejores prácticas de API RESTful
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${jwt}` // Flask espera esto
        },
        // El body ya no es necesario, el email está dentro del JWT.
    });

    if (!flaskResponse.ok) {
        const errorBody = await flaskResponse.text();
        console.error(`Error from Flask backend: ${flaskResponse.status}`, errorBody);
        return NextResponse.json(
            { error: `Error del backend: ${errorBody}` },
            { status: flaskResponse.status }
        );
    }

    // 3. Devolvemos la respuesta de Flask directamente al componente de cliente.
    const data = await flaskResponse.json();
    return NextResponse.json(data);

  } catch (error: any) {
    console.error('Error al conectar con el backend de Flask:', error.message);
    return NextResponse.json(
      { error: 'No se pudo conectar con el servicio del backend.' },
      { status: 503 } // Service Unavailable
    );
  }
}

// Forzar que esta ruta sea dinámica y no se guarde en caché
export const dynamic = 'force-dynamic';