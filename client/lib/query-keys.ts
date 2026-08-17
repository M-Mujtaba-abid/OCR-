import type { ListNotificationsParams } from "@/service/notificationService/notification.service";
import type { ListUsersParams } from "@/service/userService/user.service";
import type { InvoiceListParams } from "@/types/invoice.type";

/**
 * Every query key in one place.
 *
 * Keys are the cache's primary index, so a typo in one silently creates a
 * second cache entry that nothing ever invalidates — the classic "I mutated it
 * but the list did not update" bug. Defining them here makes that impossible
 * and makes `invalidateQueries` targets discoverable.
 *
 * Hierarchical by design: invalidating `invoices.all` also invalidates every
 * filtered/paginated list under it, because TanStack matches keys by prefix.
 */
export const queryKeys = {
  /** The signed-in user plus their permissions. */
  session: ["session"] as const,

  invoices: {
    /** Everything invoice-shaped. Reach for it only when everything changed. */
    all: ["invoices"] as const,
    /**
     * Just the tables.
     *
     * Separate from `all` because `all` also matches `detail` and `poPreview`,
     * and a mutation that changes one invoice's status has no business
     * discarding a purchase-order preview that cost a dozen Odoo searches to
     * build. Invalidating this prefix updates every list and page at once.
     */
    lists: ["invoices", "list"] as const,
    mine: (params: InvoiceListParams = {}) =>
      [...queryKeys.invoices.lists, "mine", params] as const,
    queue: (params: InvoiceListParams = {}) =>
      [...queryKeys.invoices.lists, "queue", params] as const,
    detail: (id: string) => [...queryKeys.invoices.all, "detail", id] as const,
    /** The Odoo resolution for a would-be purchase order. */
    poPreview: (id: string) =>
      [...queryKeys.invoices.all, "po-preview", id] as const,
    myStats: [...(["invoices"] as const), "stats", "mine"] as const,
    adminStats: [...(["invoices"] as const), "stats", "admin"] as const,
  },

  users: {
    all: ["users"] as const,
    list: (params: ListUsersParams = {}) =>
      [...queryKeys.users.all, "list", params] as const,
    detail: (id: string) => [...queryKeys.users.all, "detail", id] as const,
    stats: [...(["users"] as const), "stats"] as const,
  },

  notifications: {
    all: ["notifications"] as const,
    list: (params: ListNotificationsParams = {}) =>
      [...queryKeys.notifications.all, "list", params] as const,
    unread: [...(["notifications"] as const), "unread"] as const,
  },
} as const;
