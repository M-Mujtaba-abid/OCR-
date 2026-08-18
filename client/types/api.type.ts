/**
 * Shapes shared by every endpoint.
 *
 * The backend wraps every response in an envelope, so services unwrap it once
 * and hooks never see it. That keeps `useQuery` returning the payload rather
 * than `{ success, message, data }`.
 */

/** Success envelope. */
export interface ApiResponse<T = unknown> {
  success: true;
  message: string;
  data: T;
}

/** Error envelope. `details` is a field -> message map on 422, else null. */
export interface ApiErrorResponse {
  success: false;
  message: string;
  error: {
    code: string;
    details: Record<string, string> | null;
  };
  request_id: string | null;
}

export interface Pagination {
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface Paginated<T> {
  items: T[];
  pagination: Pagination;
}

/**
 * Stable codes the UI branches on. From server/app/core/exceptions.py — add
 * here when a new one is added there.
 */
export type ApiErrorCode =
  | "VALIDATION_ERROR"
  | "INVALID_CREDENTIALS"
  | "EMAIL_ALREADY_REGISTERED"
  | "INACTIVE_USER"
  | "UNAUTHORIZED"
  | "INVALID_TOKEN"
  | "TOKEN_EXPIRED"
  | "INVALID_REFRESH_TOKEN"
  | "REFRESH_TOKEN_REUSED"
  | "FORBIDDEN"
  | "INSUFFICIENT_ROLE"
  | "INSUFFICIENT_PERMISSION"
  | "NOT_FOUND"
  | "CONFLICT"
  | "CANNOT_MODIFY_SELF"
  | "LAST_ADMIN"
  | "EMPTY_FILE"
  | "FILE_TOO_LARGE"
  | "UNSUPPORTED_FILE_TYPE"
  | "TOO_MANY_FILES"
  | "NO_VALID_FILES"
  | "INVOICE_NOT_FOUND"
  | "INVOICE_LOCKED"
  | "INVOICE_NOT_READY"
  /* Odoo. ODOO_REFUSED is the one worth branching on: Odoo understood the
     request and declined, and its own message says what to do about it — so it
     is shown verbatim rather than replaced with wording of ours. The other two
     mean Odoo is unreachable or misconfigured, which no user action fixes. */
  | "ODOO_REFUSED"
  | "ODOO_ERROR"
  | "ODOO_AUTH_ERROR"
  | "ODOO_NOT_CONFIGURED"
  /* Billing refusals, all 409 and all raised before anything is written. */
  | "PO_LINE_OVER_BILLED"
  | "RECEIPT_NOT_POSSIBLE"
  | "NOTHING_TO_BILL"
  | "STORAGE_ERROR"
  | "STORAGE_NOT_CONFIGURED"
  | "DATABASE_ERROR"
  | "INTERNAL_ERROR"
  | "NETWORK_ERROR"
  | "RATE_LIMITED";
