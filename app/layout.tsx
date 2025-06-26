// File: stephaneavril/leo_api/LEO_API-7337efa878c219546557704899d19e82342974a0/app/layout.tsx
import "@/styles/globals.css";
import { Metadata } from "next";
import { Suspense } from 'react'; // Import Suspense

export const metadata: Metadata = {
  title: {
    default: "HeyGen Interactive Avatar SDK Demo",
    template: `%s - HeyGen Interactive Avatar SDK Demo`,
  },
  icons: {
    icon: "/heygen-logo.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      suppressHydrationWarning
      // Elimina las clases de variables de fuentes aquí
      // className={`${fontSans.variable} ${fontMono.variable} font-sans`}
      className="font-sans" // Usar una fuente sans-serif genérica del sistema
      lang="en"
    >
      <head />
      <body className="min-h-screen bg-black text-white">
        <main className="relative flex flex-col gap-6 h-screen w-screen">
          {/* <NavBar /> ESTA LÍNEA FUE REMOVIDA PARA ESCONDER LA BARRA DE NAVEGACIÓN */}
          {/* Wrap children with Suspense to handle client components that use browser APIs during prerendering */}
          <Suspense fallback={<div>Cargando contenido...</div>}>
            {children}
          </Suspense>
        </main>
      </body>
    </html>
  );
}