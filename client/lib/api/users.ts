/**
 * User administration endpoints.
 *
 * Every function here hits a route the backend gates on a permission
 * (`user.read` or `user.update`). Calling one without the permission returns
 * 403 INSUFFICIENT_PERMISSION — which is the point: the UI hides these
 * controls, and the API refuses them regardless of what the UI did.
 */

import { api } from "@/lib/api/client";
import type { Paginated, User, UserRole, UserStats } from "@/types/auth";

export interface ListUsersParams {
  page?: number;
  pageSize?: number;
  role?: UserRole;
}

export function listUsers({
  page = 1,
  pageSize = 20,
  role,
}: ListUsersParams = {}): Promise<Paginated<User>> {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (role) query.set("role", role);

  return api.get<Paginated<User>>(`/users?${query.toString()}`);
}

export function getUserStats(): Promise<UserStats> {
  return api.get<UserStats>("/users/stats");
}

/**
 * Promote or demote a user.
 *
 * The backend refuses two cases that the UI also blocks, for different reasons:
 * changing your own role, and demoting the last administrator. The UI disables
 * those controls so the action is not offered; the backend rejects them so a
 * disabled control is not the only thing standing between the system and
 * having zero admins.
 */
export function changeUserRole(userId: string, role: UserRole): Promise<User> {
  return api.patch<User>(`/users/${userId}/role`, { role });
}

export function setUserActive(userId: string, isActive: boolean): Promise<User> {
  return api.patch<User>(`/users/${userId}/${isActive ? "activate" : "deactivate"}`);
}
