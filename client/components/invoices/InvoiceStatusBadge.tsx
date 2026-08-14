import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { InvoiceStatus } from "@/types/invoice.type";

/**
 * Status presentation, in one place.
 *
 * Colour carries meaning here, so it is assigned by outcome rather than by
 * position in the pipeline: anything in flight is neutral, anything needing a
 * human is amber, anything failed is red, anything finished is green. A reader
 * scanning a list of forty rows should be able to find the problems without
 * reading a single label.
 */
const PRESENTATION: Record<InvoiceStatus, { label: string; tone: BadgeTone }> = {
  uploaded: { label: "Awaiting review", tone: "warning" },
  ocr_queued: { label: "Queued", tone: "neutral" },
  ocr_processing: { label: "Scanning", tone: "neutral" },
  ocr_failed: { label: "Scan failed", tone: "negative" },
  ocr_done: { label: "Scanned", tone: "neutral" },
  matching: { label: "Matching", tone: "neutral" },
  match_failed: { label: "Match failed", tone: "negative" },
  pending_review: { label: "Needs review", tone: "warning" },
  no_match: { label: "No match", tone: "warning" },
  confirmed: { label: "Confirmed", tone: "positive" },
  corrected: { label: "Corrected", tone: "accent" },
  rejected: { label: "Rejected", tone: "negative" },
  pushed: { label: "Pushed to Odoo", tone: "positive" },
};

export function statusLabel(status: InvoiceStatus): string {
  return PRESENTATION[status]?.label ?? status;
}

export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const { label, tone } = PRESENTATION[status] ?? {
    label: status,
    tone: "neutral" as BadgeTone,
  };
  return <Badge tone={tone}>{label}</Badge>;
}

/** Every status, in pipeline order — for filter dropdowns. */
export const ALL_STATUSES = Object.keys(PRESENTATION) as InvoiceStatus[];
