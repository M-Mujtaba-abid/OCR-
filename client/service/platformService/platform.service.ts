import api from "@/service/api";
import type { ApiResponse } from "@/types/api.type";
import type {
  CompanyCreated,
  CompanyCreateInput,
  PlatformCompany,
  PlatformStats,
} from "@/types/platform.type";

/**
 * The platform owner's console.
 *
 * Every route is gated server-side on `platform.admin`, which only the
 * platform owner holds and which is granted alongside nothing that reads a
 * company's data. A company administrator calling any of these gets a 403.
 */
export const platformService = {
  listCompanies: async (): Promise<PlatformCompany[]> => {
    const response =
      await api.get<ApiResponse<PlatformCompany[]>>("/platform/companies");
    return response.data.data;
  },

  stats: async (): Promise<PlatformStats> => {
    const response = await api.get<ApiResponse<PlatformStats>>("/platform/stats");
    return response.data.data;
  },

  /**
   * Create a company and its first administrator, in one call.
   *
   * Both together because either alone is not a working company: one with
   * nobody who can sign in is a name and a slug taken for nothing.
   */
  createCompany: async (data: CompanyCreateInput): Promise<CompanyCreated> => {
    const response = await api.post<ApiResponse<CompanyCreated>>(
      "/platform/companies",
      {
        name: data.name,
        admin_email: data.admin_email,
        admin_password: data.admin_password,
        // Omitted rather than sent as null when blank, so the backend applies
        // its own default instead of storing an explicit null.
        ...(data.admin_full_name ? { admin_full_name: data.admin_full_name } : {}),
      },
    );
    return response.data.data;
  },

  /** Switch every account in the company off at once. Reversible. */
  suspend: async (companyId: string): Promise<PlatformCompany> => {
    const response = await api.post<ApiResponse<PlatformCompany>>(
      `/platform/companies/${companyId}/suspend`,
    );
    return response.data.data;
  },

  restore: async (companyId: string): Promise<PlatformCompany> => {
    const response = await api.post<ApiResponse<PlatformCompany>>(
      `/platform/companies/${companyId}/restore`,
    );
    return response.data.data;
  },
};
