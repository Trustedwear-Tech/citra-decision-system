import "./globals.css";
import type { ReactNode } from "react";
import { Inter } from "next/font/google";

// Self-hosted at build time by next/font (no runtime call to Google) — keeps the
// sovereign / air-gapped guarantee while giving the runtime a premium variable
// font instead of the system stack. Exposed as --font-inter for globals.css.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata = {
  title: "Citra Power AI App",
  description: "Citra runtime"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
