// File: stephaneavril/leo_api/LEO_API-b913b081323a85b5938124f7a062b68789831888/app/layout.tsx
import "@/styles/globals.css";
import { Metadata } from "next";
// Importaciones de fuentes comentadas para solucionar error de NextFontError en Render.com
// import { Fira_Code as FontMono, Inter as FontSans } from "next/font/google";

// const fontSans = FontSans({
//   subsets: ["latin"],
//   variable: "--font-sans",
// });

// const fontMono = FontMono({
//   subsets: ["latin"],
//   variable: "--font-geist-mono",
// });

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
          {children}
        </main>
      </body>
    </html>
  );
}