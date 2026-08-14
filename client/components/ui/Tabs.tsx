"use client";

import { useCallback, useRef } from "react";

/** DOM ids are derived, so a panel can name the tab that labels it. */
export const tabId = (id: string) => `tab-${id}`;
export const panelId = (id: string) => `panel-${id}`;

/**
 * An accessible tab list.
 *
 * Built rather than pulled in because the accessible behaviour is ~40 lines and
 * a headless UI dependency for one component is not worth the bundle.
 *
 * Follows the ARIA authoring practices tabs pattern: roving tabindex so Tab
 * moves past the whole group rather than through every tab, arrow keys move
 * between tabs, Home/End jump to the ends.
 */

export interface TabItem<T extends string> {
  id: T;
  label: string;
  /** Rendered as a count pill. `undefined` renders nothing, `0` renders "0". */
  badge?: number;
}

interface TabsProps<T extends string> {
  tabs: readonly TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  /** Accessible name for the tab list. */
  label: string;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: TabsProps<T>) {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
      if (!keys.includes(event.key)) return;
      event.preventDefault();

      const index = tabs.findIndex((tab) => tab.id === active);
      const last = tabs.length - 1;
      const next =
        event.key === "ArrowRight"
          ? index >= last
            ? 0
            : index + 1
          : event.key === "ArrowLeft"
            ? index <= 0
              ? last
              : index - 1
            : event.key === "Home"
              ? 0
              : last;

      onChange(tabs[next].id);
      // Selection follows focus in this pattern, so the newly selected tab must
      // actually receive focus or the next arrow press goes nowhere.
      listRef.current
        ?.querySelector<HTMLButtonElement>(`#${CSS.escape(tabId(tabs[next].id))}`)
        ?.focus();
    },
    [active, onChange, tabs],
  );

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="-mb-px flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800"
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            id={tabId(tab.id)}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={panelId(tab.id)}
            // Roving tabindex: only the selected tab is in the tab order.
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={[
              "relative flex shrink-0 items-center gap-2 whitespace-nowrap px-4 py-3 text-sm font-medium transition-colors",
              "border-b-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-slate-950",
              selected
                ? "border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300"
                : "border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900 dark:text-slate-400 dark:hover:border-slate-700 dark:hover:text-slate-200",
            ].join(" ")}
          >
            {tab.label}
            {tab.badge !== undefined && (
              <span
                className={[
                  "rounded-full px-1.5 py-0.5 text-xs font-semibold tabular-nums",
                  selected
                    ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                    : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
                ].join(" ")}
              >
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Wraps a tab's content with the roles the tab list points at.
 *
 * Unmounts rather than hides the inactive panel: these panels hold live data
 * and in-flight requests, and a hidden-but-mounted panel keeps polling and
 * keeps its stale state when the user comes back to it.
 */
export function TabPanel({
  id,
  active,
  children,
}: {
  id: string;
  active: boolean;
  children: React.ReactNode;
}) {
  if (!active) return null;
  return (
    <div
      role="tabpanel"
      id={panelId(id)}
      aria-labelledby={tabId(id)}
      tabIndex={0}
      className="pt-6 outline-none"
    >
      {children}
    </div>
  );
}
