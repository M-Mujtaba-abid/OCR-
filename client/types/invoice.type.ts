/** Invoice and notification payloads, mirroring the FastAPI schemas. */

/** The backend's `invoice_status` enum, in pipeline order. */
export type InvoiceStatus =
  | "uploaded"
  | "ocr_queued"
  | "ocr_processing"
  | "ocr_failed"
  | "ocr_done"
  | "matching"
  | "match_failed"
  | "pending_review"
  | "no_match"
  | "confirmed"
  | "corrected"
  | "rejected"
  /** A draft purchase order was created in Odoo from this invoice. */
  | "po_created"
  | "pushed";

export interface InvoiceUploader {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Invoice {
  id: string;
  file_name: string;
  file_size_bytes: number | null;
  mime_type: string | null;
  page_count: number | null;
  member_ref_no: string | null;
  status: InvoiceStatus;
  extracted_vendor: string | null;
  extracted_invoice_no: string | null;
  extracted_total: number | null;
  extracted_currency: string | null;
  matched_po_name: string | null;
  confidence_score: number | null;
  created_at: string;
  updated_at: string;
  uploader: InvoiceUploader | null;
}

/** The structured extraction, exactly as the backend validated it. */
export interface ExtractedLineItem {
  name: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
  /** Tax printed against this line. 0 when the document taxes only the total. */
  tax: number;
}

export interface ExtractedInvoice {
  vendor_name: string | null;
  vendor_email: string | null;
  vendor_address: string | null;
  po_number: string | null;
  order_date: string | null;
  currency: string;
  items: ExtractedLineItem[];
  untaxed_amount: number;
  tax_amount: number;
  total_amount: number;
}

/** One row of `invoice_line_matches`. */
export interface InvoiceLine {
  id: string;
  line_no: number;
  raw_description: string;
  /** SKU printed on the line — turns fuzzy line matching into an exact lookup. */
  raw_product_code: string | null;
  uom: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
  /** Tax printed against this line. Null when the invoice taxes only the total. */
  tax_amount: number | null;
  matched_product_id: number | null;
  matched_product_name: string | null;
  confidence: number | null;
  status: string;
}

/** One purchase-order line, as carried in the candidate audit blob. */
export interface MatchCandidateLine {
  name: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
  /** Tax charged on the line, and the line total including it. */
  price_tax: number;
  price_total: number;
}

/** One scored purchase order from the pre-filter, as stored in `candidates`. */
export interface MatchCandidate {
  po_id: number;
  po_number: string;
  vendor: string | null;
  amount_untaxed: number;
  amount_total: number;
  order_date: string | null;
  score: number;
  breakdown: Record<string, number>;
  notes: string[];
  /** The model's reason for passing this one over. Null for the winner. */
  rejected_because?: string | null;

  /* The fields below are optional because a candidate blob is written once, at
     match time, and every match run before line details were stored has none
     of them. Re-running the match on such an invoice fills them in. */
  vendor_ref?: string | null;
  currency?: string | null;
  /** Odoo's billing state: "invoiced" means a bill for this order exists. */
  invoice_status?: string | null;
  /** The order's true line count — `items` may be capped below it. */
  line_count?: number;
  items?: MatchCandidateLine[];
}

/** The audit blob: every candidate considered, with the decision. */
export interface MatchCandidates {
  generated_at: string;
  strategy: string;
  weights: Record<string, number>;
  chosen_po_id: number | null;
  confidence: number | null;
  reasoning: string | null;
  items: MatchCandidate[];
}

export interface InvoiceDetail extends Invoice {
  tenant_id: string;
  member_notes: string | null;
  batch_id: string | null;
  extracted_json: ExtractedInvoice | null;
  extracted_untaxed: number | null;
  candidates: MatchCandidates | null;
  match_reasoning: string | null;
  lines: InvoiceLine[];
  ocr_provider: string | null;
  ocr_model: string | null;
  ocr_confidence: number | null;
  detected_language: string | null;
  ocr_completed_at: string | null;
  ocr_error: string | null;
  extracted_date: string | null;
  extracted_tax: number | null;
  extracted_line_count: number | null;
  matched_po_id: number | null;
  match_strategy: string | null;
  was_corrected: boolean;
  final_po_id: number | null;
  pushed_to_odoo: boolean;
  pushed_at: string | null;
  odoo_bill_id: number | null;
  /** What to call the bill on screen. For a draft this is the vendor's own
   *  invoice number, not an Odoo sequence — Odoo does not number a bill until
   *  it is posted, and these are deliberately left in draft. */
  odoo_bill_ref: string | null;
  reviewed_at: string | null;
  rejection_reason: string | null;
}

/* -------------------------------------------------------------------------
 * Creating a purchase order from an invoice
 * ---------------------------------------------------------------------- */

/** One Odoo record a piece of extracted text might refer to. */
export interface OdooMatch {
  id: number;
  name: string;
  /** 0-100. Shown, because a close second is the reason to ask a human. */
  score: number;
}

export interface PoPreviewLine {
  line_no: number;
  description: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
  candidates: OdooMatch[];
  /** Null where the reviewer must choose — including where a wrong candidate
   *  scored well, which is the case this whole flow exists for. */
  preselected_product_id: number | null;
}

/** What would be created in Odoo, before anything is. */
export interface PoPreview {
  vendor_name: string | null;
  /** Null blocks the whole thing: no vendor, no purchase order. */
  vendor: OdooMatch | null;
  order_date: string | null;
  currency: string;
  lines: PoPreviewLine[];
  /** Odoo's base URL, so the deep link needs no client-side setting. */
  odoo_url: string;
}

export interface CreatePoLine {
  product_id: number;
  description: string;
  quantity: number;
  unit_price: number;
}

export interface CreatePoInput {
  partner_id: number;
  order_date: string | null;
  lines: CreatePoLine[];
}

/* -------------------------------------------------------------------------
 * Creating a vendor bill from a matched purchase order
 *
 * One order is billed across several invoices over time — 100 pieces ordered,
 * 50 delivered and billed now, 50 next month. Everything here follows from
 * that: quantities are per line, and "remaining" comes from Odoo on every
 * request rather than being remembered between them.
 * ---------------------------------------------------------------------- */

/** What actually happened in Odoo. A 200 does not mean a bill was created. */
export type BillOutcome = "bill_created" | "bill_exists" | "already_paid";

/** Whether the scanned document made it onto the bill. Never fails a request. */
export type AttachmentStatus = "attached" | "skipped" | "failed";

export interface BillPreviewLine {
  po_line_id: number;
  product_id: number | null;
  product_name: string | null;
  description: string;
  uom: string | null;

  ordered_qty: number;
  /** What has physically arrived. Billing beyond it is legitimate, so this
   *  informs the reviewer rather than constraining them. */
  received_qty: number;
  /** Odoo's `qty_invoiced` — every earlier bill against this line, summed.
   *  Odoo's number, read fresh; nothing here remembers it between invoices. */
  billed_qty: number;
  /** ordered - billed. The ceiling the create endpoint enforces. */
  remaining_qty: number;

  /** The invoice's quantity, capped at `remaining`. Zero where nothing on the
   *  invoice matched — the row is still shown so a quantity can be typed in. */
  proposed_qty: number;
  /** The ORDER's price. A disagreement with the invoice is surfaced, never
   *  silently applied. */
  unit_price: number;

  invoice_line_no: number | null;
  invoice_description: string | null;
  invoice_quantity: number | null;
  invoice_unit_price: number | null;
  /** 0-100. Null means nothing on the invoice mapped to this order line. */
  match_score: number | null;
}

/** An invoice line with no counterpart on the order. Shown, never dropped. */
export interface BillPreviewUnmatchedLine {
  line_no: number;
  description: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
}

export interface BillDuplicate {
  bill_id: number;
  bill_ref: string;
  state: string | null;
  payment_state: string | null;
  amount_total: number;
  outcome: BillOutcome;
}

export interface BillPreview {
  po_id: number;
  po_name: string;
  partner_id: number | null;
  partner_name: string | null;
  /** A draft RFQ cannot be billed, and this is what says so before the click. */
  po_state: string | null;
  currency: string | null;

  /** What the bill's `ref` will be — and the key the duplicate check searches. */
  invoice_ref: string | null;
  invoice_date: string;

  /** Non-null means Odoo already holds a bill for this reference. */
  duplicate: BillDuplicate | null;
  already_pushed: boolean;

  lines: BillPreviewLine[];
  unmatched: BillPreviewUnmatchedLine[];

  proposed_untaxed: number;
  invoice_untaxed: number | null;
  odoo_url: string;
}

/** Ids and quantities only. Odoo derives product, price and tax from the
 *  order line, and an OCR'd price must not overwrite an agreed one. */
export interface CreateBillLine {
  po_line_id: number;
  quantity: number;
}

export interface CreateBillInput {
  po_id: number;
  ref: string | null;
  invoice_date: string | null;
  lines: CreateBillLine[];
  receive_goods: boolean;
  attach_document: boolean;
}

export interface CreateBillResult {
  status: BillOutcome;
  bill_id: number | null;
  bill_ref: string | null;
  attachment_status: AttachmentStatus;
  /** ISO date, `YYYY-MM-DD`. */
  invoice_date: string;
  bill_url: string;
  receipt_name: string | null;
  backorder_names: string[];
  /** The refreshed invoice — write it into the detail cache, do not refetch. */
  invoice: InvoiceDetail;
}

/** A file the server refused. Reported per-file so a partial upload is legible. */
export interface UploadRejection {
  file_name: string;
  reason: string;
  code: string;
}

export interface UploadResult {
  uploaded: Invoice[];
  rejected: UploadRejection[];
}

/** One day on the dashboard's trend chart. */
export interface InvoiceTrendPoint {
  /** ISO date, `YYYY-MM-DD`, grouped in UTC by the server. */
  day: string;
  received: number;
  /** Settled that day — counted against `reviewed_at`, not arrival. */
  reviewed: number;
}

export interface InvoiceTrend {
  days: number;
  /** Continuous: quiet days are present with zeroes, never skipped. */
  points: InvoiceTrendPoint[];
}

export interface InvoiceStats {
  total: number;
  /** Zero-filled by the backend, so every status is always a key. */
  by_status: Record<InvoiceStatus, number>;
  open_count: number;
}

/** A short-lived signed URL. Never store one — it expires. */
export interface FileLink {
  url: string;
  expires_in: number;
  file_name: string;
  mime_type: string | null;
}

/** The body of a 202: the work is scheduled, not finished. */
export interface JobAccepted {
  id: string;
  status: InvoiceStatus;
  message: string;
}

/**
 * Statuses where the server is still working.
 *
 * Drives polling: while any row is in one of these the list refetches, and
 * when none is, it stops. A constant interval would poll a finished queue
 * forever.
 */
export const TRANSIENT_STATUSES = new Set<InvoiceStatus>([
  "ocr_queued",
  "ocr_processing",
  "matching",
]);

/**
 * Limits the server enforces, served from `/config`.
 *
 * Defined once — in the server's environment — and read here. The client keeps
 * no copy of these numbers.
 */
export interface PublicConfig {
  max_file_bytes: number;
  max_files_per_upload: number;
  accepted_mime_types: string[];
}

/** Where the browser PUTs one file, and what to call it when registering. */
export interface UploadTicket {
  /** Server-generated. Never construct one client-side. */
  key: string;
  upload_url: string;
  /** Echoed back so the PUT sends exactly the headers that were signed. */
  content_type: string;
  file_name: string;
}

export interface UploadInput {
  files: File[];
  memberRefNo?: string;
  memberNotes?: string;
  /** 0-100, as bytes leave the browser. */
  onProgress?: (percent: number) => void;
}

export interface InvoiceListParams {
  page?: number;
  pageSize?: number;
  status?: InvoiceStatus;
  openOnly?: boolean;
  uploadedBy?: string;
}

/* -------------------------------------------------------------------------
 * Notifications
 * ---------------------------------------------------------------------- */

export type NotificationType =
  | "invoice_uploaded"
  | "processing_started"
  | "ocr_completed"
  | "ocr_failed"
  | "match_found"
  | "no_match_found"
  | "invoice_confirmed"
  | "invoice_corrected"
  | "invoice_rejected"
  | "invoice_pushed";

export interface AppNotification {
  id: string;
  type: NotificationType;
  title: string;
  message: string | null;
  match_history_id: string | null;
  batch_id: string | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
}

export interface UnreadCount {
  count: number;
}

export interface MarkedRead {
  marked: number;
}
