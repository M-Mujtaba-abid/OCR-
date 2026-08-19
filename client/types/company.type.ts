/** Company payloads, mirroring server/app/schemas/company.py. */

/**
 * The company the signed-in user works for.
 *
 * Everybody in a company can read this — it is what the header renders, and
 * knowing which company you are working in is not a privilege. The platform
 * owner has no company, so for them this request 403s and the header says so
 * instead.
 */
export interface Company {
  id: string;
  name: string;
  /** Immutable. Object-storage keys are built from it. */
  slug: string;
  is_active: boolean;
  created_at: string;
}

/**
 * What is configured, and never the credential itself.
 *
 * There is deliberately no `api_key` here, mirroring the backend response
 * model. A settings screen needs to know whether a connection exists, where it
 * points and whether it has ever worked — none of which requires showing the
 * key, and showing it would put a company's ERP password one XSS away.
 */
export interface OdooConfigStatus {
  configured: boolean;
  base_url: string | null;
  database: string | null;
  username: string | null;
  is_enabled: boolean;
  /** Null until the credentials have actually authenticated. "Saved" and
   *  "working" are different states, and this screen says which. */
  verified_at: string | null;
  /** False when the server has no encryption key — credentials can then be
   *  neither saved nor read, and the screen says so rather than failing on
   *  save. */
  encryption_available: boolean;
  /** True when this company has no configuration of its own and is running on
   *  the server's environment fallback. */
  using_server_fallback: boolean;
}

/** Credentials an administrator is saving for their OWN company. */
export interface OdooConfigInput {
  base_url: string;
  database: string;
  username: string;
  /** Write-only: it appears in no response anywhere. */
  api_key: string;
  is_enabled: boolean;
}

/** What answered, after a successful `verify`. */
export interface OdooVerifyResult {
  connected: boolean;
  uid?: number;
  server_version?: string;
  database?: string;
  url?: string;
}
