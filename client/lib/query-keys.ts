import type { ListNotificationsParams } from "@/service/notificationService/notification.service";
import type { ListUsersParams } from "@/service/userService/user.service";
import type { BillHistoryParams, InvoiceListParams } from "@/types/invoice.type";

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

  /** Server-enforced limits. Changes only when an env var does. */
  config: ["config"] as const,

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
    /**
     * The bills already raised in Odoo.
     *
     * Under `lists` so that creating one refreshes the history with everything
     * else, without a second invalidation target to remember.
     */
    bills: (params: BillHistoryParams = {}) =>
      [...queryKeys.invoices.lists, "bills", params] as const,
    detail: (id: string) => [...queryKeys.invoices.all, "detail", id] as const,
    /** The Odoo resolution for a would-be purchase order. */
    poPreview: (id: string) =>
      [...queryKeys.invoices.all, "po-preview", id] as const,
    /**
     * What billing this invoice against its order would produce.
     *
     * Under `all` rather than `lists`, for the same reason `poPreview` is: it
     * costs an order read plus a search for existing bills, and a mutation on
     * an unrelated invoice has no business discarding it.
     */
    billPreview: (id: string) =>
      [...queryKeys.invoices.all, "bill-preview", id] as const,
    myStats: [...(["invoices"] as const), "stats", "mine"] as const,
    adminStats: [...(["invoices"] as const), "stats", "admin"] as const,
    trend: (days: number) =>
      [...(["invoices"] as const), "trend", days] as const,
  },

  users: {
    all: ["users"] as const,
    /**
     * Just the tables.
     *
     * Separate from `all` for the same reason `invoices.lists` is: `all` also
     * matches `stats`, whose cached value is a bare `{ total, active, ... }`.
     * An updater written to patch a row into `page.items` throws on it.
     */
    lists: ["users", "list"] as const,
    list: (params: ListUsersParams = {}) =>
      [...queryKeys.users.lists, params] as const,
    detail: (id: string) => [...queryKeys.users.all, "detail", id] as const,
    stats: [...(["users"] as const), "stats"] as const,
  },

  /**
   * The company the caller belongs to.
   *
   * Its own key rather than a branch of `session`: the session is invalidated
   * on every role change and every sign-in, and the company name has not
   * changed just because somebody was promoted.
   */
  company: {
    all: ["company"] as const,
    /**
     * Under `all` rather than BEING it. A bare `["company"]` for the company
     * itself would be a prefix of `odoo`, so refreshing the name would discard
     * the connection status too — the same hierarchy mistake `users.lists` and
     * `notifications.lists` exist to avoid.
     */
    current: [...(["company"] as const), "current"] as const,
    /** The Odoo connection status. Never the credential. */
    odoo: [...(["company"] as const), "odoo"] as const,
  },

  /**
   * The platform console. Companies, never their contents.
   *
   * A prefix of its own rather than a branch of `users` or `invoices`: nothing
   * cached here belongs to a company, so nothing a company mutation
   * invalidates should reach it.
   */
  platform: {
    all: ["platform"] as const,
    companies: [...(["platform"] as const), "companies"] as const,
    stats: [...(["platform"] as const), "stats"] as const,
  },

  notifications: {
    all: ["notifications"] as const,
    /**
     * Just the pages of rows.
     *
     * Separate from `all`, which also matches `unread` — and that one caches a
     * bare `{ count }`, not a page. Writing a page-shaped update across `all`
     * hands the updater the count object and dies on its missing `items`.
     */
    lists: ["notifications", "list"] as const,
    list: (params: ListNotificationsParams = {}) =>
      [...queryKeys.notifications.lists, params] as const,
    unread: [...(["notifications"] as const), "unread"] as const,
  },
} as const;
