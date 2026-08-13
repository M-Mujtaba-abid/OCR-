interface AlertProps {
  variant?: "error" | "success" | "info";
  children: React.ReactNode;
}

const VARIANTS: Record<NonNullable<AlertProps["variant"]>, string> = {
  error:
    "border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200",
  success:
    "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/50 dark:bg-emerald-950/40 dark:text-emerald-200",
  info: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
};

/**
 * Server Component — no "use client". It has no interactivity, so it ships
 * zero JavaScript.
 */
export function Alert({ variant = "error", children }: AlertProps) {
  return (
    <div
      // role="alert" makes screen readers announce it the moment it appears,
      // which matters for a login failure the user did not scroll to.
      role="alert"
      className={`rounded-lg border px-4 py-3 text-sm ${VARIANTS[variant]}`}
    >
      {children}
    </div>
  );
}
