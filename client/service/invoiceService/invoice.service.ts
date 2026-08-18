import axios, { type AxiosProgressEvent } from "axios";

import api, { UPLOAD_TIMEOUT } from "@/service/api";
import type { ApiResponse, Paginated } from "@/types/api.type";
import type {
  CreatePoInput,
  FileLink,
  Invoice,
  InvoiceDetail,
  InvoiceListParams,
  InvoiceStats,
  InvoiceTrend,
  JobAccepted,
  PoPreview,
  PublicConfig,
  UploadTicket,
  UploadInput,
  UploadResult,
} from "@/types/invoice.type";

export const invoiceService = {
  /**
   * The limits the server will enforce.
   *
   * Fetched rather than duplicated. These used to be constants here with a
   * comment saying they mirrored the backend — which is two definitions of one
   * number, and the failure is quiet either way round: a browser that refuses
   * a file the server would have taken, or one that uploads a file the server
   * then rejects after the whole transfer.
   */
  config: async (): Promise<PublicConfig> => {
    const response = await api.get<ApiResponse<PublicConfig>>("/config");
    return response.data.data;
  },

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
    // Three steps, because the bytes must not come through the API: a
    // serverless request body is capped at 4.5 MB and a scanned invoice is
    // routinely larger. The file goes browser -> storage directly; the API
    // only ever sees the key.

    // 1. Ask for a signed URL per file. The server names the object.
    const tickets = (
      await api.post<ApiResponse<UploadTicket[]>>("/invoices/upload-url", {
        files: files.map((file) => ({
          file_name: file.name,
          content_type: file.type || "application/octet-stream",
        })),
      })
    ).data.data;

    // 2. PUT each file straight to storage.
    //
    // A BARE axios call, not the shared `api` instance. That one carries the
    // access token and `withCredentials: true` — sending either to Cloudflare
    // would hand a third party our bearer token, and a credentialed
    // cross-origin request fails CORS against a bucket anyway.
    //
    // The headers must match what was signed exactly, or R2 rejects the PUT.
    const sent = new Array<number>(files.length).fill(0);
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0) || 1;

    await Promise.all(
      tickets.map((ticket, index) =>
        axios.put(ticket.upload_url, files[index], {
          timeout: UPLOAD_TIMEOUT,
          headers: {
            "Content-Type": ticket.content_type,
            "Content-Disposition": `inline; filename="${ticket.file_name}"`,
          },
          onUploadProgress: (event: AxiosProgressEvent) => {
            sent[index] = event.loaded;
            const done = sent.reduce((a, b) => a + b, 0);
            onProgress?.(Math.min(99, Math.round((done / totalBytes) * 100)));
          },
        }),
      ),
    );

    // 3. Register them. The server re-reads each object's real size and type
    // from storage — nothing claimed here is taken on trust — and only then
    // creates the rows and queues extraction.
    const response = await api.post<ApiResponse<UploadResult>>(
      "/invoices/register",
      {
        files: tickets.map((ticket) => ({
          key: ticket.key,
          file_name: ticket.file_name,
        })),
        member_ref_no: memberRefNo?.trim() || null,
        member_notes: memberNotes?.trim() || null,
      },
    );
    onProgress?.(100);
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

  /** Daily arrivals and reviews for the admin dashboard. */
  adminTrend: async (days = 14): Promise<InvoiceTrend> => {
    const response = await api.get<ApiResponse<InvoiceTrend>>(
      "/invoices/admin/trend",
      { params: { days } },
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
