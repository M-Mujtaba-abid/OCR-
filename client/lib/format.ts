/** Display formatting shared across the invoice screens. */

/**
 * A money amount, always to two decimals.
 *
 * Null renders as an em dash rather than "0.00": an amount nobody extracted and
 * an amount that is genuinely zero are different facts, and on an
 * accounts-payable screen they must not look the same.
 */
/**
 * "4m ago", "2d ago" — a rough age, not a timestamp.
 *
 * Notifications are read as a stream, where "when, roughly" is the only
 * question; an absolute time forces the reader to do the subtraction. The exact
 * value stays available as a `title` wherever this is used.
 */
export function timeAgo(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";

  const steps: [number, string][] = [
    [60, "m"],
    [3600, "h"],
    [86400, "d"],
  ];
  for (const [size, unit] of steps) {
    const next = size * (unit === "m" ? 60 : unit === "h" ? 24 : 7);
    if (seconds < next) return `${Math.floor(seconds / size)}${unit} ago`;
  }
  // Past a week the age stops being useful — show the date instead.
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

export function money(
  value: number | null | undefined,
  currency?: string | null,
): string {
  if (value == null) return "—";
  return `${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}${currency ? ` ${currency}` : ""}`;
}
