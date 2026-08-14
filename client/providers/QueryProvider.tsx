"use client";

import { useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { Toaster } from "react-hot-toast";

import { getQueryClient } from "@/lib/query-client";

/**
 * Query cache + toasts, mounted once above the whole route tree.
 *
 * The client comes from `useState`, not a module constant: React can render a
 * component twice before committing, and a constant created during render would
 * be discarded along with any in-flight queries attached to it. `useState` with
 * an initialiser guarantees exactly one client per mount.
 *
 * Toasts live here rather than inside a page because feedback about an action
 * must outlive the component that triggered it — the upload panel unmounts the
 * moment the user is moved to the invoice list, and a message rendered inside
 * it would be destroyed before the browser painted it.
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(getQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      {children}

      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          // Errors usually need reading, not just noticing.
          error: { duration: 8000 },
          style: {
            borderRadius: "0.75rem",
            padding: "0.75rem 1rem",
            fontSize: "0.875rem",
            maxWidth: "26rem",
          },
        }}
      />

      {/* Stripped from production builds by the bundler's dead-code pass. */}
      {process.env.NODE_ENV === "development" && (
        <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
      )}
    </QueryClientProvider>
  );
}
