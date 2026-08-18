"use client";

import { useState } from "react";

import type { InvoiceTrendPoint } from "@/types/invoice.type";

/**
 * Arrivals against reviews, per day.
 *
 * The rest of this dashboard is a snapshot — what is true right now. This is
 * the only thing on the page that shows a direction, which is what makes a
 * backlog visible: if the received line runs above the reviewed line for a
 * week, the queue is growing whatever today's counts say.
 *
 * Plain SVG, no charting library. Two series over fourteen points needs a
 * path, an axis and a hover — none of which is worth ~100 KB of someone else's
 * abstraction, and hand-drawn means it inherits the app's own colours and dark
 * mode instead of fighting them.
 */
const W = 720;
const H = 200;
const PAD = { top: 12, right: 12, bottom: 26, left: 30 };

export function TrendChart({
  points,
  loading,
}: {
  points: InvoiceTrendPoint[] | undefined;
  loading?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);

  if (loading || !points) {
    return <div className="h-52 w-full animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800" />;
  }
  if (points.length === 0) return null;

  const peak = Math.max(1, ...points.flatMap((p) => [p.received, p.reviewed]));
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const step = points.length > 1 ? plotW / (points.length - 1) : 0;

  const x = (i: number) => PAD.left + i * step;
  const y = (v: number) => PAD.top + plotH - (v / peak) * plotH;

  const line = (key: "received" | "reviewed") =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p[key])}`).join(" ");

  const area =
    `M${x(0)},${PAD.top + plotH} ` +
    points.map((p, i) => `L${x(i)},${y(p.received)}`).join(" ") +
    ` L${x(points.length - 1)},${PAD.top + plotH} Z`;

  // Ticks at a round count rather than every value: an axis labelled 0,1,2,3…
  // is noise, and the numbers that matter are direct-labelled on hover.
  const ticks = [0, Math.round(peak / 2), peak].filter(
    (v, i, all) => all.indexOf(v) === i,
  );
  const active = hover ?? points.length - 1;
  const activePoint = points[active];

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* A legend, because there are two series — identity is never colour
            alone, and these two lines are the whole comparison. */}
        <ul className="flex flex-wrap gap-4 text-xs">
          <li className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span aria-hidden="true" className="h-0.5 w-4 rounded-full bg-indigo-600 dark:bg-indigo-500" />
            Received
          </li>
          <li className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span aria-hidden="true" className="h-0.5 w-4 rounded-full bg-emerald-600" />
            Reviewed
          </li>
        </ul>
        <p className="text-xs tabular-nums text-slate-500 dark:text-slate-400">
          {formatDay(activePoint.day)} · {activePoint.received} received ·{" "}
          {activePoint.reviewed} reviewed
        </p>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-3 w-full"
        role="img"
        aria-label={`Invoices received and reviewed over the last ${points.length} days`}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(value)}
              y2={y(value)}
              className="stroke-slate-200 dark:stroke-slate-800"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 6}
              y={y(value) + 3}
              textAnchor="end"
              className="fill-slate-400 text-[9px] tabular-nums"
            >
              {value}
            </text>
          </g>
        ))}

        <path d={area} className="fill-indigo-600/10 dark:fill-indigo-500/15" />
        <path
          d={line("received")}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-indigo-600 dark:stroke-indigo-500"
        />
        <path
          d={line("reviewed")}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-emerald-600"
        />

        {/* Crosshair on the active day. Solid, a shade stronger than the grid:
            dashing here would read as a threshold or a projection rather than
            "you are pointing at this column". */}
        <line
          x1={x(active)}
          x2={x(active)}
          y1={PAD.top}
          y2={PAD.top + plotH}
          className="stroke-slate-300 dark:stroke-slate-600"
          strokeWidth={1}
        />
        {/* A 2px surface ring keeps the marker readable where the two lines
            cross each other. */}
        <circle cx={x(active)} cy={y(activePoint.received)} r={4}
          className="fill-indigo-600 stroke-white dark:fill-indigo-500 dark:stroke-slate-900" strokeWidth={2} />
        <circle cx={x(active)} cy={y(activePoint.reviewed)} r={4}
          className="fill-emerald-600 stroke-white dark:stroke-slate-900" strokeWidth={2} />

        {/* Hit targets, wider than the marks — pointing at a 4px dot is not a
            thing anybody should have to do. They tile the plot edge to edge, so
            every pixel resolves to its nearest day.
            Focusable as well as hoverable: a keyboard gets the same readout as
            a mouse, rather than the values being locked behind a pointer. */}
        {points.map((p, i) => (
          <rect
            key={p.day}
            x={x(i) - step / 2}
            y={PAD.top}
            width={Math.max(step, 8)}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            role="button"
            aria-label={`${formatDay(p.day)}: ${p.received} received, ${p.reviewed} reviewed`}
            className="outline-none focus-visible:fill-slate-900/5 dark:focus-visible:fill-white/10"
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
          />
        ))}

        {/* First and last day only. Fourteen date labels would collide. */}
        <text x={PAD.left} y={H - 6} className="fill-slate-400 text-[9px]">
          {formatDay(points[0].day)}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className="fill-slate-400 text-[9px]">
          {formatDay(points[points.length - 1].day)}
        </text>
      </svg>
    </div>
  );
}

function formatDay(day: string): string {
  // Parsed as UTC to match the server's grouping, so a late-evening upload
  // does not land on yesterday's column for a reader east of Greenwich.
  const date = new Date(`${day}T00:00:00Z`);
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
