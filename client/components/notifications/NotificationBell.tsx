"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import {
  FEED_PAGE_SIZE,
  flattenFeed,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationFeed,
  useUnreadCount,
} from "@/hooks/notification/useNotifications.hooks";
import { RefreshButton } from "@/components/ui/RefreshButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { timeAgo } from "@/lib/format";
import type { AppNotification, NotificationType } from "@/types/invoice.type";

/**
 * The bell, and the list behind it.
 *
 * The header used to show an unread count with nothing behind it — a number
 * that said "3 new" and gave no way to see what the three were, and no way to
 * make them stop being new. The API and the hooks for all of it already
 * existed; only this was missing.
 *
 * The bell is always rendered, not just when something is unread. An indicator
 * that appears and disappears cannot be checked on purpose — "did I miss
 * something?" needs somewhere to click even when the answer is no.
 */
export function NotificationBell({ className }: { className?: string }) {
  const { can, isAuthenticated } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);

  const { data: unread } = useUnreadCount(isAuthenticated);
  // Fetched only while the panel is open: the feed is rows nobody is looking at
  // the rest of the time, and the count alone drives the badge.
  const feed = useNotificationFeed(open);
  const markAll = useMarkAllNotificationsRead();

  const count = unread?.count ?? 0;

  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        // Focus goes back where it came from, or a keyboard user is left
        // stranded at the top of the document.
        buttonRef.current?.focus();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const items = flattenFeed(feed.data);

  const { fetchNextPage, hasNextPage, isFetchingNextPage } = feed;

  /**
   * Load the next page as the reader APPROACHES the end, not when they hit it.
   *
   * `rootMargin` is what makes it "approaches": the observer treats the sentinel
   * as visible while it is still 160px below the fold, so the request is already
   * in flight by the time those rows would be needed. Waiting for the sentinel
   * to actually appear would show the reader a spinner at the bottom of every
   * page — which is the thing infinite scroll exists to avoid.
   *
   * `root` is the dropdown, not the viewport. The list scrolls inside a fixed
   * 22rem box, so against the default root the sentinel is either always visible
   * or never — and "always" means it fetches every page at once.
   */
  const attachSentinel = useCallback(
    (node: HTMLDivElement | null) => {
      observerRef.current?.disconnect();
      if (!node || !scrollRef.current || !hasNextPage) return;

      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0]?.isIntersecting) void fetchNextPage();
        },
        { root: scrollRef.current, rootMargin: "160px" },
      );
      observerRef.current.observe(node);
    },
    // A callback ref rather than an effect, because the node it watches is
    // mounted and unmounted with the dropdown. An effect would have to guard
    // against a ref that is null on the render it runs after.
    [fetchNextPage, hasNextPage],
  );

  // Disconnect when the dropdown closes; the callback ref only fires on mount
  // and unmount of the sentinel, and a closed panel unmounts it either way.
  useEffect(() => () => observerRef.current?.disconnect(), []);

  return (
    <div ref={containerRef} className={`relative ${className ?? ""}`}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={
          count > 0 ? `Notifications, ${count} unread` : "Notifications"
        }
        className="relative rounded-md p-2 text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {count > 0 && (
          <span className="absolute -right-0.5 -top-0.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-[18px] text-white ring-2 ring-white dark:bg-amber-600 dark:ring-slate-950">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
              Notifications
            </h2>
            <RefreshButton
              onRefresh={() => void feed.refetch()}
              refreshing={feed.isFetching}
              what="notifications"
              size="sm"
              className="ml-auto"
            />
            {count > 0 && (
              <button
                type="button"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
                className="text-xs font-medium text-indigo-600 hover:underline disabled:opacity-50 dark:text-indigo-400"
              >
                Mark all read
              </button>
            )}
          </div>

          <div ref={scrollRef} className="max-h-[22rem] overflow-y-auto">
            {feed.isLoading && (
              <div className="space-y-3 px-4 py-4">
                {[0, 1, 2].map((row) => (
                  <div key={row} className="flex gap-3">
                    <Skeleton className="mt-1.5 size-2 shrink-0 rounded-full" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3 w-3/4" />
                      <Skeleton className="h-2.5 w-1/2" />
                    </div>
                  </div>
                ))}
                <span className="sr-only">Loading notifications</span>
              </div>
            )}
            {!feed.isLoading && items.length === 0 && (
              <p className="px-4 py-6 text-sm text-slate-600 dark:text-slate-400">
                Nothing yet. Uploads, matches and failures show up here.
              </p>
            )}
            <ul className="divide-y divide-slate-100 dark:divide-slate-800/60">
              {items.map((item) => (
                <NotificationRow
                  key={item.id}
                  item={item}
                  // The permission, not the role. It was `isAdmin`, which left
                  // managers with notifications about invoices they are
                  // entitled to read and no way to open one.
                  //
                  // Resolved once here rather than per row: every row asking
                  // for the session would be the same cached read repeated a
                  // dozen times for an answer that cannot differ between them.
                  canOpenInvoices={can("invoice.read.all")}
                  onNavigate={() => setOpen(false)}
                />
              ))}
            </ul>

            {/* Zero-height and invisible. It exists to be scrolled towards. */}
            {hasNextPage && <div ref={attachSentinel} aria-hidden="true" />}

            {isFetchingNextPage && (
              <div className="space-y-3 px-4 py-3">
                {[0, 1].map((row) => (
                  <div key={row} className="flex gap-3">
                    <Skeleton className="mt-1.5 size-2 shrink-0 rounded-full" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3 w-3/4" />
                      <Skeleton className="h-2.5 w-1/2" />
                    </div>
                  </div>
                ))}
                <span className="sr-only">Loading more notifications</span>
              </div>
            )}

            {/* Only once there is enough to have scrolled through. Saying "that
                is everything" under four rows is noise; saying nothing after
                forty leaves the reader wondering whether it is still loading. */}
            {!hasNextPage && items.length > FEED_PAGE_SIZE && (
              <p className="px-4 py-3 text-center text-xs text-slate-400 dark:text-slate-600">
                That is everything.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Colour by outcome, and never by colour alone — every row is also worded. */
const TONE: Record<NotificationType, string> = {
  invoice_uploaded: "bg-slate-300 dark:bg-slate-600",
  processing_started: "bg-slate-300 dark:bg-slate-600",
  ocr_completed: "bg-slate-300 dark:bg-slate-600",
  ocr_failed: "bg-red-600 dark:bg-red-500",
  match_found: "bg-amber-500 dark:bg-[#bf8618]",
  no_match_found: "bg-indigo-600 dark:bg-indigo-500",
  invoice_confirmed: "bg-emerald-600",
  invoice_corrected: "bg-emerald-600",
  invoice_rejected: "bg-red-600 dark:bg-red-500",
  invoice_pushed: "bg-emerald-600",
  approval_requested: "bg-sky-600 dark:bg-sky-500",
  approval_granted: "bg-emerald-600",
  approval_declined: "bg-red-600 dark:bg-red-500",
  // Amber, not red: nobody has refused anything. Something is simply not
  // moving, and that is a nudge rather than a failure.
  approval_overdue: "bg-amber-500 dark:bg-[#bf8618]",
};

function NotificationRow({
  item,
  canOpenInvoices,
  onNavigate,
}: {
  item: AppNotification;
  canOpenInvoices: boolean;
  onNavigate: () => void;
}) {
  const markRead = useMarkNotificationRead();

  // Where this row goes, and it is not always the invoice.
  //
  // "Your approval is needed" is sent to whoever is named on the step, and a
  // chain can name anybody — a receiving clerk on a member account is the case
  // the feature exists for. They cannot open /admin, so pointing them at the
  // invoice would leave the one notification that demands an action as the one
  // with no link on it. /approvals is open to every company account and is
  // where they can actually decide.
  //
  // Granted and declined go the other way: they are sent to whoever asked, who
  // holds invoice.review by definition, and the invoice is what they want.
  const href = !item.match_history_id
    ? null
    : item.type === "approval_requested"
      ? "/approvals"
      : canOpenInvoices
        ? `/admin/invoices/${item.match_history_id}`
        : null;

  const read = () => {
    if (!item.is_read) markRead.mutate(item.id);
  };

  const body = (
    <>
      <span
        aria-hidden="true"
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${TONE[item.type] ?? "bg-slate-300"} ${
          item.is_read ? "opacity-30" : ""
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span
            className={`truncate text-sm ${
              item.is_read
                ? "text-slate-600 dark:text-slate-400"
                : "font-medium text-slate-900 dark:text-white"
            }`}
          >
            {item.title}
          </span>
          <span
            className="shrink-0 text-xs text-slate-400"
            title={new Date(item.created_at).toLocaleString()}
          >
            {timeAgo(item.created_at)}
          </span>
        </span>
        {item.message && (
          <span className="mt-0.5 block line-clamp-2 text-xs text-slate-600 dark:text-slate-400">
            {item.message}
          </span>
        )}
      </span>
    </>
  );

  const shared =
    "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60";

  if (href) {
    return (
      <li>
        <Link
          href={href}
          // Off deliberately, and it matters more now the feed is infinite.
          // Next prefetches every Link in the viewport, so a reader scrolling
          // through sixty notifications would prefetch sixty invoice routes —
          // each a dynamic, server-rendered page — to open at most one of them.
          prefetch={false}
          onClick={() => {
            read();
            onNavigate();
          }}
          className={shared}
        >
          {body}
        </Link>
      </li>
    );
  }

  // Already read and nowhere to go: there is no action left, so it is not a
  // control. A disabled button would still be announced as a button the reader
  // cannot use, which is noise on a list they are only scanning.
  if (item.is_read) {
    return <li className={shared}>{body}</li>;
  }

  return (
    <li>
      <button type="button" onClick={read} className={shared}>
        {body}
      </button>
    </li>
  );
}
