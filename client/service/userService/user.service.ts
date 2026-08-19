import api from "@/service/api";
import type { ApiResponse, Paginated } from "@/types/api.type";
import type {
  CreateUserInput,
  User,
  UserRole,
  UserStats,
} from "@/types/user.type";

export interface ListUsersParams {
  page?: number;
  pageSize?: number;
  role?: UserRole;
}

/**
 * User administration.
 *
 * Every route here is gated server-side on a permission (`user.read` or
 * `user.update`). Calling one without it returns 403 — the UI hides these
 * controls, and the API refuses them regardless of what the UI did.
 */
export const userService = {
  /**
   * Add somebody to your company. The only way an account is created.
   *
   * Public sign-up was removed when the system became multi-company: a form
   * filled in by a stranger cannot say which business they belong to. The
   * company comes from the caller's own session server-side, which is why
   * nothing about it is sent from here.
   */
  create: async (data: CreateUserInput): Promise<User> => {
    const response = await api.post<ApiResponse<User>>("/users", {
      email: data.email,
      password: data.password,
      role: data.role,
      // Omitted rather than sent as null when blank, so the backend applies
      // its own default instead of storing an explicit null.
      ...(data.full_name ? { full_name: data.full_name } : {}),
    });
    return response.data.data;
  },

  list: async ({
    page = 1,
    pageSize = 20,
    role,
  }: ListUsersParams = {}): Promise<Paginated<User>> => {
    const response = await api.get<ApiResponse<Paginated<User>>>("/users", {
      // params, not string concatenation: axios encodes values and drops
      // undefined keys, so an absent filter never becomes `role=undefined`.
      params: { page, page_size: pageSize, role },
    });
    return response.data.data;
  },

  stats: async (): Promise<UserStats> => {
    const response = await api.get<ApiResponse<UserStats>>("/users/stats");
    return response.data.data;
  },

  getById: async (userId: string): Promise<User> => {
    const response = await api.get<ApiResponse<User>>(`/users/${userId}`);
    return response.data.data;
  },

  /**
   * Promote or demote.
   *
   * The backend refuses two cases the UI also blocks: changing your own role,
   * and demoting the last administrator. The UI disables those controls so the
   * action is not offered; the backend rejects them so a disabled control is
   * not the only thing standing between the system and having zero admins.
   */
  changeRole: async (userId: string, role: UserRole): Promise<User> => {
    const response = await api.patch<ApiResponse<User>>(`/users/${userId}/role`, {
      role,
    });
    return response.data.data;
  },

  setActive: async (userId: string, isActive: boolean): Promise<User> => {
    const response = await api.patch<ApiResponse<User>>(
      `/users/${userId}/${isActive ? "activate" : "deactivate"}`,
    );
    return response.data.data;
  },
};
