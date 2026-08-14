import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { QueryProvider } from "@/providers/QueryProvider";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "OCR",
    template: "%s · OCR",
  },
  description: "Invoice processing and account management",
};

/**
 * Root layout stays a Server Component.
 *
 * QueryProvider is the only client boundary, so every layout and page below it
 * can still be server-rendered. Session state lives in the query cache rather
 * than a context, which is why there is no AuthProvider here any more — a
 * component that needs the user calls `useAuth()` and reads from the same
 * cache entry as everyone else.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
