// File: stephaneavril/leo_api/LEO_API-7337efa878c219546557704899d19e82342974a0/next.config.js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // **Asegúrate de incluir esta línea**
  // Explícitamente establece assetPrefix a la ruta raíz.
  // Esto ayuda a asegurar que todas las URL de los activos estáticos (JS, CSS)
  // generadas por Next.js comiencen desde la raíz del dominio.
  assetPrefix: '/',
};

module.exports = nextConfig;