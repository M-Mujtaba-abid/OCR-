"use client";

import { useCallback, useMemo, useRef, useState } from "react";

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
 * The only "same file" signal a browser offers.
 *
 * Name and size alone call two different scans of the same one-page form
 * identical and silently drop the second; `lastModified` separates them.
 */
function identity(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/** Dragged text or a link is not something this dropzone should light up for. */
function carriesFiles(transfer: DataTransfer): boolean {
  return transfer.types.includes("Files");
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

  // The queue holds Files, not verdicts. See `staged` below.
  const [files, setFiles] = useState<File[]>([]);
  const [refNo, setRefNo] = useState("");
  const [notes, setNotes] = useState("");
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);

  /**
   * The queue, with each file's verdict — derived, never stored.
   *
   * `limits` arrives one request after the first paint, so anything staged in
   * that window was checked against nothing. Deriving means those files pick up
   * their verdict the moment the config resolves, instead of sitting there
   * looking fine until the server refuses them.
   */
  const staged = useMemo<Staged[]>(
    () => files.map((file) => ({ file, problem: inspect(file, limits) })),
    [files, limits],
  );

  const uploading = upload.isPending;
  const valid = staged.filter((s) => !s.problem);
  const overLimit = maxFiles > 0 && staged.length > maxFiles;

  /**
   * What the OS dialog filters by.
   *
   * Both forms on purpose: the MIME list is the server's own, and the
   * extensions cover the file types Windows reports no MIME type for — a .tif
   * picked with a MIME-only filter is greyed out and cannot be selected.
   */
  const accept = useMemo(
    () => [...(limits?.accepted_mime_types ?? []), ACCEPT_EXTENSIONS].join(","),
    [limits],
  );

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
    // Copied out here, synchronously, and this line is the whole fix for
    // "the picker closes and no file appears".
    //
    // `input.files` is a LIVE FileList owned by the input element. The change
    // handler clears `input.value` straight after this call — which empties
    // that very object — and a `setState` updater is not guaranteed to run
    // before the handler returns. When React defers it, the updater walks an
    // already-emptied list and stages nothing. Dropping files kept working
    // because a DataTransfer's list is nobody's to clear.
    const picked = Array.from(incoming);
    if (picked.length === 0) return;

    setFiles((current) => {
      const seen = new Set(current.map(identity));
      const fresh = picked.filter((file) => {
        const key = identity(file);
        if (seen.has(key)) return false;
        // Guards the incoming batch against itself too, not just the queue.
        seen.add(key);
        return true;
      });
      // Same array back when everything was a duplicate: no render, and no
      // re-derivation of the verdicts for the whole queue.
      return fresh.length > 0 ? [...current, ...fresh] : current;
    });
  }, []);

  function reset() {
    setFiles([]);
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
        onDragEnter={(e) => {
          if (!uploading && carriesFiles(e.dataTransfer)) setDragging(true);
        }}
        onDragOver={(e) => {
          if (!carriesFiles(e.dataTransfer)) return;
          // Without preventDefault the browser treats the page as a non-target
          // and opens the dropped file instead of handing it over.
          e.preventDefault();
          // Says "copy" rather than "move" on the cursor — and "no drop" while
          // an upload is in flight, so the refusal is visible before the drop.
          e.dataTransfer.dropEffect = uploading ? "none" : "copy";
          if (!uploading) setDragging(true);
        }}
        onDragLeave={(e) => {
          // dragleave also fires when the pointer crosses onto a child, which
          // made the highlight strobe as it passed over the icon and the text.
          // A null relatedTarget means the pointer left the window entirely.
          if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
          setDragging(false);
        }}
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
          accept={accept}
          className="sr-only"
          // Not disabled while uploading: the browser fires no change event on
          // a disabled input, and the button above already blocks the click.
          onChange={(e) => {
            const input = e.currentTarget;
            add(input.files ?? []);
            // Only now, and only after `add` has copied the list out — clearing
            // the value empties `input.files`. It is what lets the same file be
            // picked again after being removed from the queue.
            input.value = "";
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
                // Unique by construction — `add` refuses a file already queued
                // under this key — so removing a row does not re-key the rest.
                key={identity(item.file)}
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
                    setFiles((current) => current.filter((_, i) => i !== index))
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
