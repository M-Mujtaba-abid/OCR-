"use client";

import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  useAppConfig,
  useUploadInvoices,
} from "@/hooks/invoice/useInvoices.hooks";
import type { PublicConfig } from "@/types/invoice.type";

/** Extensions the file picker suggests, alongside the server's MIME list. */
const ACCEPT_EXTENSIONS = ".pdf,.png,.jpg,.jpeg,.tif,.tiff";

/** A file plus why it cannot be sent. `null` means it is fine. */
interface Staged {
  file: File;
  problem: string | null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Client-side pre-check.
 *
 * Exists purely so the user finds out about a 40 MB file before waiting for it
 * to upload — it is not a security control. The server sniffs magic bytes and
 * re-checks the size, because a browser's `File.type` is derived from the file
 * extension and is trivially wrong.
 */
function inspect(file: File, limits: PublicConfig | undefined): string | null {
  if (file.size === 0) return "File is empty";
  // Until the limits arrive there is nothing to check against, and inventing a
  // number here would recreate the duplication this removed. The server
  // enforces them regardless; this only saves the user a wasted upload.
  if (!limits) return null;

  if (file.size > limits.max_file_bytes) {
    return `Too large (${formatBytes(file.size)}, limit ${formatBytes(limits.max_file_bytes)})`;
  }
  // Some browsers report an empty type for .tif — let those through and let the
  // server's content sniffing decide.
  if (file.type && !limits.accepted_mime_types.includes(file.type)) {
    return "Unsupported format";
  }
  return null;
}

export function InvoiceUpload({ onUploaded }: { onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const upload = useUploadInvoices();
  // The server's limits, fetched once. This component holds no copy of them.
  const { data: limits } = useAppConfig();
  const maxFiles = limits?.max_files_per_upload ?? 0;

  const [staged, setStaged] = useState<Staged[]>([]);
  const [refNo, setRefNo] = useState("");
  const [notes, setNotes] = useState("");
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);

  const uploading = upload.isPending;
  const valid = staged.filter((s) => !s.problem);
  const overLimit = maxFiles > 0 && staged.length > maxFiles;

  /**
   * Why the upload button is disabled, in words.
   *
   * A disabled button with no explanation is the most common way a form becomes
   * unusable — the user clicks, nothing happens, and nothing on screen says why.
   */
  const blockedReason = uploading
    ? null
    : staged.length === 0
      ? "Choose at least one file to upload."
      : overLimit
        ? `Remove ${staged.length - maxFiles} file${staged.length - maxFiles === 1 ? "" : "s"} — the limit is ${maxFiles} per upload.`
        : valid.length === 0
          ? "None of the selected files can be uploaded."
          : null;

  const add = useCallback((incoming: FileList | File[]) => {
    setStaged((current) => {
      const seen = new Set(current.map((s) => `${s.file.name}:${s.file.size}`));
      const next = [...current];
      for (const file of Array.from(incoming)) {
        // Name AND size is the only available signal for "same file"; dropping
        // a folder twice should not queue everything twice.
        const key = `${file.name}:${file.size}`;
        if (seen.has(key)) continue;
        seen.add(key);
        next.push({ file, problem: inspect(file, limits) });
      }
      return next;
    });
    // `limits` arrives asynchronously, so a file staged before it lands is
    // checked against nothing. That is the safe direction — the server still
    // enforces both — but the callback must see the current value rather than
    // close over the first one.
  }, [limits]);

  function reset() {
    setStaged([]);
    setRefNo("");
    setNotes("");
    setProgress(0);
    if (inputRef.current) inputRef.current.value = "";
  }

  function submit() {
    if (!valid.length || overLimit) return;
    setProgress(0);

    upload.mutate(
      {
        files: valid.map((s) => s.file),
        memberRefNo: refNo,
        memberNotes: notes,
        onProgress: setProgress,
      },
      {
        // Success and failure toasts are raised by the mutation hook, which
        // survives this component unmounting when the caller switches tabs.
        onSuccess: () => {
          reset();
          onUploaded();
        },
      },
    );
  }

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------------ dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!uploading) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!uploading) add(e.dataTransfer.files);
        }}
        className={[
          "rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging
            ? "border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-950/40"
            : "border-slate-300 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50",
        ].join(" ")}
      >
        <svg
          aria-hidden="true"
          width="36"
          height="36"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="mx-auto text-slate-400 dark:text-slate-500"
        >
          <path d="M12 16V4m0 0L8 8m4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" strokeLinecap="round" />
        </svg>

        <p className="mt-3 text-sm font-medium text-slate-900 dark:text-white">
          Drop invoices here
        </p>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          PDF, PNG, JPEG or TIFF
          {limits && (
            <>
              {" "}
              · up to {formatBytes(limits.max_file_bytes)} each · {maxFiles} files
              per upload
            </>
          )}
        </p>

        <div className="mt-4">
          <Button
            variant="secondary"
            type="button"
            disabled={uploading}
            onClick={() => inputRef.current?.click()}
          >
            Browse files
          </Button>
        </div>

        <input
          ref={inputRef}
          type="file"
          multiple
          accept={[...(limits?.accepted_mime_types ?? []), ACCEPT_EXTENSIONS].join(",")}
          className="sr-only"
          onChange={(e) => {
            if (e.target.files) add(e.target.files);
            // Cleared so selecting the same file again still fires onChange.
            e.target.value = "";
          }}
        />
      </div>

      {/* --------------------------------------------------------------- queue */}
      {staged.length > 0 && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-800">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
            <p className="text-sm font-medium text-slate-900 dark:text-white">
              {staged.length} file{staged.length === 1 ? "" : "s"} selected
            </p>
            <button
              type="button"
              onClick={reset}
              disabled={uploading}
              className="text-sm text-slate-600 underline underline-offset-4 hover:text-slate-900 disabled:opacity-50 dark:text-slate-400 dark:hover:text-white"
            >
              Clear all
            </button>
          </div>

          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {staged.map((item, index) => (
              <li
                key={`${item.file.name}-${item.file.size}-${index}`}
                className="flex items-center gap-3 px-4 py-3"
              >
                <span
                  aria-hidden="true"
                  className={
                    item.problem ? "text-red-500" : "text-slate-400 dark:text-slate-500"
                  }
                >
                  {item.problem ? "!" : "●"}
                </span>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-900 dark:text-slate-100">
                    {item.file.name}
                  </p>
                  <p
                    className={
                      item.problem
                        ? "text-xs text-red-600 dark:text-red-400"
                        : "text-xs text-slate-500 dark:text-slate-400"
                    }
                  >
                    {item.problem ?? formatBytes(item.file.size)}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={() =>
                    setStaged((current) => current.filter((_, i) => i !== index))
                  }
                  disabled={uploading}
                  aria-label={`Remove ${item.file.name}`}
                  className="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50 dark:hover:bg-slate-800 dark:hover:text-white"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ------------------------------------------------------------- metadata */}
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
            Reference number
          </span>
          <input
            value={refNo}
            onChange={(e) => setRefNo(e.target.value)}
            disabled={uploading}
            maxLength={120}
            placeholder="Optional"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
            Notes
          </span>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={uploading}
            maxLength={4000}
            placeholder="Optional"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
          />
        </label>
      </div>

      {/* ------------------------------------------------------------- progress */}
      {uploading && (
        <div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600 dark:text-slate-400">
              {progress < 100 ? "Uploading…" : "Processing on the server…"}
            </span>
            <span className="tabular-nums text-slate-600 dark:text-slate-400">
              {progress}%
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Upload progress"
            className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
          >
            <div
              className="h-full rounded-full bg-indigo-600 transition-[width] duration-150 dark:bg-indigo-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-3">
        {/* aria-live so a screen reader hears the reason change as files are
            added or removed, not only when the region first appears. */}
        <p aria-live="polite" className="text-sm text-slate-600 dark:text-slate-400">
          {blockedReason}
        </p>
        <Button
          type="button"
          onClick={submit}
          isLoading={uploading}
          disabled={uploading || blockedReason !== null}
        >
          {uploading
            ? "Uploading…"
            : valid.length === 0
              ? "Upload"
              : `Upload ${valid.length} file${valid.length === 1 ? "" : "s"}`}
        </Button>
      </div>
    </div>
  );
}
