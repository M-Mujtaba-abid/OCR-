/**
 * Platform-console payloads, mirroring server/app/schemas/platform.py.
 *
 * Everything here is ABOUT companies and never about what is inside one.
 * There is no invoice, vendor, bill or amount in this file, and that absence
 * is the design: the platform owner creates companies and their first
 * administrators, and has no access to anybody's payables.
 */

export interface PlatformCompany {
  id: string;
  name: string;
  /** Immutable. Object-storage keys are built from it. */
  slug: string;
  is_active: boolean;
  created_at: string;

  user_count: number;
  active_user_count: number;
  admin_count: number;

  /** Whether the company has its own Odoo — never any detail of it. */
  odoo_configured: boolean;
}

/**
 * A new company and the administrator who will run it.
 *
 * No slug: it is derived from the name server-side, because it becomes an
 * object-storage path segment and a typed one invites a value that collides
 * with — or traverses out of — another company's prefix.
 */
export interface CompanyCreateInput {
  name: string;
  admin_email: string;
  admin_password: string;
  admin_full_name?: string | null;
}

export interface CompanyCreated {
  company: PlatformCompany;
  admin_email: string;
  admin_id: string;
}

export interface PlatformStats {
  companies: number;
  active_companies: number;
  users: number;
}
