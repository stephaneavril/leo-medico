// File: stephaneavril/leo_api/LEO_API-b913b081323a85b5938124f7a062b68789831888/app/page.tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Cookies from 'js-cookie';

export default function HomePage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!name || !email || !token) {
      setError('Por favor, completa todos los campos.');
      return;
    }

    try {
      // Simular la validación con el backend Flask.
      // En una implementación real, esto sería una llamada API a Flask.
      // Flask debería retornar un 200 OK si el usuario es válido y activo.
      const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL;
      const response = await fetch(`${flaskApiUrl}/validate_user`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, email, token }),
      });

      if (response.ok) {
        // Almacenar en cookies o sesión para que el dashboard pueda leerlo
        // Usamos Cookies para que sea accesible en el lado del servidor si es necesario (SSR/middleware)
        Cookies.set('user_name', name, { expires: 1 }); // Expira en 1 día
        Cookies.set('user_email', email, { expires: 1 });
        Cookies.set('user_token', token, { expires: 1 });

        router.push('/dashboard');
      } else {
        const errorText = await response.text();
        setError(`Error de acceso: ${errorText}`);
      }
    } catch (err) {
      console.error('Error al iniciar sesión:', err);
      setError('Error de red al intentar iniciar sesión. Intenta de nuevo.');
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-zinc-900 text-white">
      <form onSubmit={handleSubmit} className="bg-zinc-800 p-8 rounded-lg space-y-5 w-full max-w-md shadow-lg">
        <h1 className="text-3xl font-bold text-center text-blue-400 mb-6">
          🧠 Entrenamiento Virtual con Leo
        </h1>
        <input
          type="text"
          placeholder="Tu nombre"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full p-3 rounded-lg bg-zinc-700 text-white placeholder-zinc-400 border border-zinc-600 focus:border-blue-500 focus:outline-none"
          required
        />
        <input
          type="email"
          placeholder="Correo electrónico"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full p-3 rounded-lg bg-zinc-700 text-white placeholder-zinc-400 border border-zinc-600 focus:border-blue-500 focus:outline-none"
          required
        />
        <input
          type="text"
          placeholder="Tu código de acceso"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full p-3 rounded-lg bg-zinc-700 text-white placeholder-zinc-400 border border-zinc-600 focus:border-blue-500 focus:outline-none"
          required
        />
        {error && <p className="text-red-400 text-sm text-center">{error}</p>}
        <button
          type="submit"
          className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 rounded-lg transition duration-200"
        >
          Comenzar
        </button>
      </form>
      <footer className="mt-8 text-sm text-zinc-500">
        <p>Desarrollado por <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">Teams</a> &copy; 2025</p>
      </footer>
    </div>
  );
}