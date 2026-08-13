"use client";

import { forwardRef, useId, useState } from "react";

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  /** Renders a show/hide toggle. Only meaningful for password fields. */
  showPasswordToggle?: boolean;
}

/**
 * Accessible labelled input.
 *
 * The label is a real <label htmlFor>, not a placeholder — placeholder-only
 * fields disappear on focus and are not announced reliably by screen readers.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, showPasswordToggle = false, type = "text", id, className, ...props },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const errorId = `${inputId}-error`;
  const [revealed, setRevealed] = useState(false);

  const resolvedType = showPasswordToggle && revealed ? "text" : type;

  return (
    <div className="space-y-1.5">
      <label
        htmlFor={inputId}
        className="block text-sm font-medium text-slate-700 dark:text-slate-200"
      >
        {label}
      </label>

      <div className="relative">
        <input
          {...props}
          ref={ref}
          id={inputId}
          type={resolvedType}
          // Ties the message to the field so assistive tech announces it.
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={[
            "w-full rounded-lg border bg-white px-3.5 py-2.5 text-sm text-slate-900",
            "placeholder:text-slate-400",
            "transition-colors outline-none",
            "focus:ring-2 focus:ring-offset-1",
            "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500",
            "dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500",
            "dark:disabled:bg-slate-800",
            showPasswordToggle ? "pr-11" : "",
            error
              ? "border-red-400 focus:border-red-500 focus:ring-red-200 dark:border-red-500/60 dark:focus:ring-red-900"
              : "border-slate-300 focus:border-slate-900 focus:ring-slate-200 dark:border-slate-700 dark:focus:border-slate-400 dark:focus:ring-slate-700",
            className ?? "",
          ].join(" ")}
        />

        {showPasswordToggle && (
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            // tabIndex -1 keeps Tab moving Email -> Password -> Submit, which
            // is the flow people expect; the toggle stays mouse/AT reachable.
            tabIndex={-1}
            aria-label={revealed ? "Hide password" : "Show password"}
            className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-lg text-slate-500 transition-colors hover:text-slate-900 dark:hover:text-slate-100"
          >
            {revealed ? <EyeOffIcon /> : <EyeIcon />}
          </button>
        )}
      </div>

      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
});

function EyeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 7 10 7a18 18 0 0 1-2.16 3.19M6.6 6.6A18 18 0 0 0 2 11s3.5 7 10 7a9 9 0 0 0 5.4-1.6" />
      <path d="m2 2 20 20" />
    </svg>
  );
}
