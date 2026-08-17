import api, { UPLOAD_TIMEOUT } from "@/service/api";
import type { ApiResponse, Paginated } from "@/types/api.type";
import type {
  CreatePoInput,
  FileLink,
  Invoice,
  InvoiceDetail,
  InvoiceListParams,
  InvoiceStats,
  JobAccepted,
  PoPreview,
  UploadInput,
  UploadResult,
} from "@/types/invoice.type";

/** Mirrors the backend's UPLOAD_MAX_SIZE_MB / MAX_FILES_PER_UPLOAD. */
export const MAX_FILES = 10;
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const ACCEPTED_MIME = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/tiff",
] as const;

export const invoiceService = {
  /**
   * Upload 1..MAX_FILES invoices.
   *
   * `onUploadProgress` is why this uses axios rather than fetch: fetch has no
   * event for bytes sent, so a fetch-based upload can only show a spinner that
   * pretends to know how far along it is.
   *
   * Content-Type is left unset deliberately — the browser must set it itself so
   * it can append the multipart boundary. Setting it by hand breaks parsing on
   * the server.
   */
  upload: async ({
    files,
    memberRefNo,
    memberNotes,
    onProgress,
  }: UploadInput): Promise<UploadResult> => {
    const form = new FormData();
    // Repeated "files" parts — what FastAPI's `list[UploadFile]` expects and
    // what a native <input multiple> produces.
    for (const file of files) form.append("files", file, file.name);
    if (memberRefNo?.trim()) form.append("member_ref_no", memberRefNo.trim());
    if (memberNotes?.trim()) form.append("member_notes", memberNotes.trim());

    const response = await api.post<ApiResponse<UploadResult>>(
      "/invoices/upload",
      form,
      {
        timeout: UPLOAD_TIMEOUT,
        onUploadProgress: (event) => {
          if (!event.total) return;
          onProgress?.(Math.round((event.loaded / event.total) * 100));
        },
      },
    );
    return response.data.data;
  },

  /** The caller's own uploads. */
  listMine: async ({
    page = 1,
    pageSize = 10,
    status,
  }: InvoiceListParams = {}): Promise<Paginated<Invoice>> => {
    const response = await api.get<ApiResponse<Paginated<Invoice>>>("/invoices/my", {
      params: { page, page_size: pageSize, status },
    });
    return response.data.data;
  },

  myStats: async (): Promise<InvoiceStats> => {
    const response = await api.get<ApiResponse<InvoiceStats>>("/invoices/my/stats");
    return response.data.data;
  },

  /** Everybody's uploads. Requires invoice.read.all. */
  listAll: async ({
    page = 1,
    pageSize = 10,
    status,
    openOnly,
    uploadedBy,
  }: InvoiceListParams = {}): Promise<Paginated<Invoice>> => {
    const response = await api.get<ApiResponse<Paginated<Invoice>>>(
      "/invoices/admin/queue",
      {
        params: {
          page,
          page_size: pageSize,
          status,
          open_only: openOnly,
          uploaded_by: uploadedBy,
        },
      },
    );
    return response.data.data;
  },

  adminStats: async (): Promise<InvoiceStats> => {
    const response = await api.get<ApiResponse<InvoiceStats>>("/invoices/admin/stats");
    return response.data.data;
  },

  getById: async (invoiceId: string): Promise<InvoiceDetail> => {
    const response = await api.get<ApiResponse<InvoiceDetail>>(
      `/invoices/${invoiceId}`,
    );
    return response.data.data;
  },

  /**
   * Mint a signed download URL.
   *
   * Call it at click time and never cache the result: the bucket is private and
   * the signature expires in minutes, so a URL rendered into an href at page
   * load is usually dead by the time anyone clicks it.
   */
  getFileLink: async (invoiceId: string): Promise<FileLink> => {
    const response = await api.get<ApiResponse<FileLink>>(
      `/invoices/${invoiceId}/file`,
    );
    return response.data.data;
  },

  remove: async (invoiceId: string): Promise<void> => {
    await api.delete(`/invoices/${invoiceId}`);
  },

  /* ----------------------------------------------------------- pipeline */

  /** Re-run extraction. Answers 202 — poll the invoice for the result. */
  runOcr: async (invoiceId: string): Promise<JobAccepted> => {
    const response = await api.post<ApiResponse<JobAccepted>>(
      `/invoices/${invoiceId}/ocr`,
    );
    return response.data.data;
  },

  /** Pull Odoo POs, pre-filter, and let the model choose. Answers 202. */
  runMatching: async (invoiceId: string): Promise<JobAccepted> => {
    const response = await api.post<ApiResponse<JobAccepted>>(
      `/invoices/${invoiceId}/match`,
    );
    return response.data.data;
  },

  /**
   * Accept the suggested purchase order, or override it with another.
   *
   * The same endpoint for both: the backend compares `po_id` against what it
   * suggested and records the difference as `was_corrected`. That flag is the
   * record of where the matcher was wrong, which is the most useful signal
   * this product produces.
   */
  confirmMatch: async (invoiceId: string, poId: number): Promise<InvoiceDetail> => {
    const response = await api.post<ApiResponse<InvoiceDetail>>(
      `/invoices/${invoiceId}/confirm`,
      { po_id: poId },
    );
    return response.data.data;
  },

  /**
   * What creating a purchase order from this invoice would produce.
   *
   * Read-only and safe to call before the reviewer has decided anything: it
   * resolves the vendor and offers products, but writes nothing.
   */
  poPreview: async (invoiceId: string): Promise<PoPreview> => {
    const response = await api.get<ApiResponse<PoPreview>>(
      `/invoices/${invoiceId}/po-preview`,
    );
    return response.data.data;
  },

  /**
   * Create the draft purchase order in Odoo.
   *
   * The payload carries the mapping the reviewer approved, product ids and
   * all — the server does not re-resolve, because resolving twice can produce
   * two answers and only one of them was looked at by a person.
   */
  createPo: async (
    invoiceId: string,
    input: CreatePoInput,
  ): Promise<InvoiceDetail> => {
    const response = await api.post<ApiResponse<InvoiceDetail>>(
      `/invoices/${invoiceId}/create-po`,
      input,
    );
    return response.data.data;
  },

  reject: async (invoiceId: string, reason: string): Promise<InvoiceDetail> => {
    const response = await api.post<ApiResponse<InvoiceDetail>>(
      `/invoices/${invoiceId}/reject`,
      { reason },
    );
    return response.data.data;
  },
};
