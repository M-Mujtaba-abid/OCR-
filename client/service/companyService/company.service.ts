import api from "@/service/api";
import type { ApiResponse } from "@/types/api.type";
import type {
  Company,
  OdooConfigInput,
  OdooConfigStatus,
  OdooVerifyResult,
} from "@/types/company.type";

/**
 * The caller's own company.
 *
 * There is no company id in the request and no way to ask for another one —
 * the server reads it from the session. A company administrator and a member
 * of the same company get the same answer.
 */
export const companyService = {
  current: async (): Promise<Company> => {
    const response = await api.get<ApiResponse<Company>>("/company");
    return response.data.data;
  },

  /* --------------------------------------------------------------- Odoo */

  odooStatus: async (): Promise<OdooConfigStatus> => {
    const response = await api.get<ApiResponse<OdooConfigStatus>>("/company/odoo");
    return response.data.data;
  },

  /**
   * Save this company's Odoo credentials.
   *
   * Saving does NOT connect. `verifyOdoo` does that, so a typo reads as a
   * failed connection rather than a failed save that discards what was typed.
   */
  saveOdoo: async (data: OdooConfigInput): Promise<OdooConfigStatus> => {
    const response = await api.put<ApiResponse<OdooConfigStatus>>(
      "/company/odoo",
      data,
    );
    return response.data.data;
  },

  /** Authenticate, and report which Odoo answered. */
  verifyOdoo: async (): Promise<OdooVerifyResult> => {
    const response = await api.post<ApiResponse<OdooVerifyResult>>(
      "/company/odoo/verify",
    );
    return response.data.data;
  },

  /** Stop using Odoo without discarding the credentials. */
  disableOdoo: async (): Promise<OdooConfigStatus> => {
    const response = await api.post<ApiResponse<OdooConfigStatus>>(
      "/company/odoo/disable",
    );
    return response.data.data;
  },
};
