import api from "@/service/api";
import type { ApiResponse } from "@/types/api.type";
import type {
  AuthSession,
  LoginData,
  LoginInput,
  LogoutData,
  Permission,
  RegisterInput,
  TokenData,
  User,
} from "@/types/user.type";

/**
 * Auth endpoints.
 *
 * Every method unwraps the `{ success, message, data }` envelope and returns
 * `data`. The envelope is a transport detail: hooks and components should see
 * the payload, not the wrapper.
 */
export const authService = {
  /**
   * Create an account.
   *
   * Returns the created user only — the backend deliberately issues no tokens
   * on register, so the caller sends the user to the login page.
   */
  register: async (data: RegisterInput): Promise<User> => {
    const response = await api.post<ApiResponse<User>>("/auth/register", {
      email: data.email,
      password: data.password,
      // Omit rather than send null when blank, so the backend applies its own
      // default instead of storing an explicit null.
      ...(data.full_name ? { full_name: data.full_name } : {}),
    });
    return response.data.data;
  },

  /** Log in. Sets the HttpOnly refresh cookie as a side effect. */
  login: async (data: LoginInput): Promise<LoginData> => {
    const response = await api.post<ApiResponse<LoginData>>("/auth/login", data);
    return response.data.data;
  },

  /** Revoke this session and clear the cookie server-side. */
  logout: async (): Promise<LogoutData> => {
    const response = await api.post<ApiResponse<LogoutData>>("/auth/logout");
    return response.data.data;
  },

  /** Revoke every session for the current user. */
  logoutAll: async (): Promise<LogoutData> => {
    const response = await api.post<ApiResponse<LogoutData>>("/auth/logout-all");
    return response.data.data;
  },

  /**
   * Exchange the refresh cookie for a new access token.
   *
   * Rarely called directly — the axios interceptor handles the 401 path. It is
   * exposed for the session bootstrap, which needs a token before it can ask
   * who the user is.
   */
  refresh: async (): Promise<TokenData> => {
    const response = await api.post<ApiResponse<TokenData>>("/auth/refresh");
    return response.data.data;
  },

  getProfile: async (): Promise<User> => {
    const response = await api.get<ApiResponse<User>>("/auth/me");
    return response.data.data;
  },

  /**
   * The current user's effective permissions.
   *
   * Fetched rather than derived from `user.role`, so the role→permission
   * mapping exists only in the backend and the two cannot drift.
   */
  getPermissions: async (): Promise<Permission[]> => {
    const response = await api.get<ApiResponse<Permission[]>>("/auth/permissions");
    return response.data.data;
  },

  getSessions: async (): Promise<AuthSession[]> => {
    const response = await api.get<ApiResponse<AuthSession[]>>("/auth/sessions");
    return response.data.data;
  },
};
