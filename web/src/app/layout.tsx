import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Camoufox Profile Manager",
  description: "Professional antidetect browser profile management system",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased bg-dark-900 text-white min-h-screen">
        {children}
      </body>
    </html>
  );
}
