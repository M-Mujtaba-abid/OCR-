import type { InvoiceStatus } from "@/types/invoice.type";

/**
 * The pipeline, as five stages a person actually cares about.
 *
 * Thirteen statuses is more than any chart can carry — past about seven
 * classes, extra colours stop distinguishing and start decorating. So the
 * statuses fold into the five answers an admin opens this page for: is it
 * still moving, does it need me, did it fail, is it done.
 *
 * Every status belongs to exactly one stage, which is what makes the bar
 * honest: the segments always sum to the total, so an invoice can never be
 * invisible on the dashboard the way it used to be.
 */
export interface Stage {
  id: string;
  label: string;
  /** What this stage means, for the tooltip. */
  hint: string;
  statuses: readonly InvoiceStatus[];
  /** Tailwind classes for the bar segment and the legend dot. */
  fill: string;
  /** Text colour for a matching headline number. */
  ink: string;
}

/**
 * Order matters, twice over.
 *
 * Left to right it reads as the pipeline: in flight → needs a human → failed →
 * finished. And it keeps amber and red apart, with indigo between them —
 * adjacent segments touch, and under deuteranopia amber against red measures
 * ΔE 3.1, which is no separation at all. Reordering costs nothing and is what
 * makes the palette pass rather than needing a caveat.
 *
 * Colours are validated (OKLCH lightness band, chroma floor, CVD separation,
 * normal-vision floor, surface contrast) in both modes. Light amber sits below
 * 3:1 on white by design — the legend labels and the status table are the
 * mitigation, which is why neither is optional.
 */
export const STAGES: readonly Stage[] = [
  {
    id: "in_flight",
    label: "In flight",
    hint: "Uploaded, being read, or being matched — nothing to do yet",
    statuses: ["uploaded", "ocr_queued", "ocr_processing", "ocr_done", "matching"],
    // Grey on purpose: this stage is context, not a call to action.
    fill: "bg-slate-300 dark:bg-slate-600",
    ink: "text-slate-600 dark:text-slate-300",
  },
  {
    id: "needs_review",
    label: "Needs review",
    hint: "Matched to a purchase order, waiting for someone to confirm it",
    statuses: ["pending_review"],
    fill: "bg-amber-500 dark:bg-[#bf8618]",
    ink: "text-amber-600 dark:text-amber-400",
  },
  {
    id: "no_match",
    label: "No match",
    hint: "No purchase order fit — assign one, or raise a new PO",
    statuses: ["no_match"],
    fill: "bg-indigo-600 dark:bg-indigo-500",
    ink: "text-indigo-600 dark:text-indigo-400",
  },
  {
    id: "failed",
    label: "Failed",
    hint: "The document could not be read, or matching crashed",
    statuses: ["ocr_failed", "match_failed"],
    fill: "bg-red-600 dark:bg-red-500",
    ink: "text-red-600 dark:text-red-400",
  },
  {
    id: "settled",
    label: "Settled",
    hint: "Confirmed, corrected, raised as a PO, pushed, or rejected",
    statuses: ["confirmed", "corrected", "po_created", "pushed", "rejected"],
    fill: "bg-emerald-600 dark:bg-emerald-600",
    ink: "text-emerald-600 dark:text-emerald-400",
  },
] as const;

/**
 * Every status must belong to a stage — enforced at compile time.
 *
 * This is the guarantee the dashboard rests on. Before it existed the pipeline
 * showed six hand-picked statuses, so seven invoices rendered as four and the
 * missing three were simply invisible. Adding a status to the union without
 * giving it a stage now fails the build instead of quietly hiding rows.
 */
type StagedStatus = (typeof STAGES)[number]["statuses"][number];
type EveryStatusStaged = Exclude<InvoiceStatus, StagedStatus> extends never
  ? true
  : ["unstaged status:", Exclude<InvoiceStatus, StagedStatus>];
const _statusCoverage: EveryStatusStaged = true;
void _statusCoverage;

/** Sum the statuses in a stage. Undefined stats mean "not loaded", not zero. */
export function stageCount(
  stage: Stage,
  byStatus: Record<InvoiceStatus, number> | undefined,
): number {
  if (!byStatus) return 0;
  return stage.statuses.reduce((sum, status) => sum + (byStatus[status] ?? 0), 0);
}

/**
 * Statuses worth a row in the detail table, in pipeline order.
 *
 * The table is where every status is accounted for — the chart summarises, the
 * table reconciles. Rows that are zero still appear, so a reader can tell
 * "none failed" from "failures are not shown here".
 */
export const STATUS_ORDER: readonly InvoiceStatus[] = [
  "uploaded",
  "ocr_queued",
  "ocr_processing",
  "ocr_done",
  "ocr_failed",
  "matching",
  "match_failed",
  "pending_review",
  "no_match",
  "confirmed",
  "corrected",
  "po_created",
  "pushed",
  "rejected",
] as const;

/** Which stage a status belongs to — for the table's colour dot. */
export function stageOf(status: InvoiceStatus): Stage {
  return STAGES.find((stage) => stage.statuses.includes(status)) ?? STAGES[0];
}
