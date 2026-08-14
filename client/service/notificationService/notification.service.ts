import api from "@/service/api";
import type { ApiResponse, Paginated } from "@/types/api.type";
import type {
  AppNotification,
  MarkedRead,
  UnreadCount,
} from "@/types/invoice.type";

export interface ListNotificationsParams {
  page?: number;
  pageSize?: number;
  unreadOnly?: boolean;
}

/**
 * Notifications.
 *
 * No user id is ever sent: every query is scoped server-side by the
 * authenticated caller, so there is no way to ask for somebody else's.
 */
export const notificationService = {
  list: async ({
    page = 1,
    pageSize = 20,
    unreadOnly = false,
  }: ListNotificationsParams = {}): Promise<Paginated<AppNotification>> => {
    const response = await api.get<ApiResponse<Paginated<AppNotification>>>(
      "/notifications",
      { params: { page, page_size: pageSize, unread_only: unreadOnly } },
    );
    return response.data.data;
  },

  unreadCount: async (): Promise<UnreadCount> => {
    const response = await api.get<ApiResponse<UnreadCount>>("/notifications/unread");
    return response.data.data;
  },

  markRead: async (notificationId: string): Promise<MarkedRead> => {
    const response = await api.patch<ApiResponse<MarkedRead>>(
      `/notifications/${notificationId}/read`,
    );
    return response.data.data;
  },

  markAllRead: async (): Promise<MarkedRead> => {
    const response = await api.patch<ApiResponse<MarkedRead>>(
      "/notifications/read-all",
    );
    return response.data.data;
  },
};
