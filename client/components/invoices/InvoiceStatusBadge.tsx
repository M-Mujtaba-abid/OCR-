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
 *
 * Labels are short on purpose — one or two words, never three. These sit in a
 * table column, and a status long enough to wrap makes every row in the table
 * taller than the one fact it is reporting. `hint` carries the sentence the
 * label had to give up, on hover and to a screen reader.
 */
const PRESENTATION: Record<
  InvoiceStatus,
  { label: string; tone: BadgeTone; hint: string }
> = {
  uploaded: {
    label: "Uploaded",
    tone: "warning",
    hint: "Uploaded and waiting to be read.",
  },
  ocr_queued: {
    label: "Queued",
    tone: "neutral",
    hint: "Queued for extraction.",
  },
  ocr_processing: {
    label: "Scanning",
    tone: "neutral",
    hint: "Being read right now.",
  },
  ocr_failed: {
    label: "Scan failed",
    tone: "negative",
    hint: "The document could not be read. Try reading it again.",
  },
  ocr_done: {
    label: "Scanned",
    tone: "neutral",
    hint: "Read, but not yet matched against a purchase order.",
  },
  matching: {
    label: "Matching",
    tone: "neutral",
    hint: "Being matched against Odoo purchase orders right now.",
  },
  match_failed: {
    label: "Match failed",
    tone: "negative",
    hint: "Matching could not be completed. Run it again.",
  },
  pending_review: {
    label: "Needs review",
    tone: "warning",
    hint: "A purchase order was suggested. Somebody has to confirm it.",
  },
  pending_approval: {
    label: "Awaiting sign-off",
    // Waiting on a person, like "Needs review" — but on a named approver rather
    // than on whoever opens the queue, which is why it is not "warning".
    tone: "accent",
    hint: "Sent through the approval chain. Waiting on an approver.",
  },
  no_match: {
    label: "No match",
    tone: "warning",
    hint: "No purchase order matched. Create one, or match it by hand.",
  },
  confirmed: {
    label: "Confirmed",
    tone: "positive",
    hint: "The suggested purchase order was accepted.",
  },
  corrected: {
    label: "Corrected",
    tone: "accent",
    hint: "A reviewer chose a different purchase order than the one suggested.",
  },
  rejected: {
    label: "Rejected",
    tone: "negative",
    hint: "Rejected by a reviewer. The uploader was told why.",
  },
  po_created: {
    label: "PO created",
    tone: "positive",
    hint: "A draft purchase order was raised in Odoo from this invoice.",
  },
  pushed: {
    label: "Billed",
    tone: "positive",
    hint: "A draft vendor bill was created in Odoo. It still needs posting there.",
  },
};

export function statusLabel(status: InvoiceStatus): string {
  return PRESENTATION[status]?.label ?? status;
}

/** The sentence behind the label — for tooltips and helper text. */
export function statusHint(status: InvoiceStatus): string {
  return PRESENTATION[status]?.hint ?? "";
}

export function InvoiceStatusBadge({
  status,
  /** The dot earns its place in a list and is noise on a detail page. */
  dot = false,
}: {
  status: InvoiceStatus;
  dot?: boolean;
}) {
  const presentation = PRESENTATION[status];
  const { label, tone, hint } = presentation ?? {
    label: status,
    tone: "neutral" as BadgeTone,
    hint: "",
  };
  return (
    <span title={hint || undefined}>
      <Badge tone={tone} dot={dot}>
        {label}
      </Badge>
      {/* The tooltip is not reachable without a pointer, so the same sentence
          is given to a screen reader outright. */}
      {hint && <span className="sr-only"> — {hint}</span>}
    </span>
  );
}

/** Every status, in pipeline order — for filter dropdowns. */
export const ALL_STATUSES = Object.keys(PRESENTATION) as InvoiceStatus[];
