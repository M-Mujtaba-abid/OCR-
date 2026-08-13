import Link from "next/link";

/**
 * Shell for the auth pages. Server Component — no interactivity, no JS.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12 dark:bg-slate-950">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Link
            href="/"
            className="text-xl font-semibold tracking-tight text-slate-900 dark:text-white"
          >
            OCR
          </Link>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8 dark:border-slate-800 dark:bg-slate-900">
          {children}
        </div>
      </div>
    </div>
  );
}
