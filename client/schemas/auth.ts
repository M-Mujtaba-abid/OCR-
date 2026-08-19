/**
 * Form validation schemas.
 *
 * These mirror the backend's Pydantic constraints so users get instant
 * feedback — but they are a UX layer, never a security boundary. The backend
 * revalidates everything and remains the source of truth.
 */

import { z } from "zod";

const email = z
  .string()
  .trim()
  .min(1, "Email is required")
  .email("Enter a valid email address");

/**
 * min 8 / max 128 exactly matches `UserCreate` on the backend, so the client
 * never submits something the server will reject. The maximum is a DoS guard:
 * Argon2 cost scales with input length.
 */
const password = z
  .string()
  .min(8, "Password must be at least 8 characters")
  .max(128, "Password must be at most 128 characters");

export const loginSchema = z.object({
  email,
  // Only "required" on login — applying the strength rules here would reject
  // legitimate older passwords that predate the current policy.
  password: z.string().min(1, "Password is required"),
});

/**
 * An administrator adding somebody to their company.
 *
 * This is what the old public sign-up schema became, rather than a second copy
 * of the same rules: the constraints are identical because the backend applies
 * the same `UserCreate` to both, and only the person filling the form changed.
 *
 * There is no company field, and there should never be one. The company is
 * taken from the administrator's own session on the server — a company sent
 * from a browser is a company the caller chose.
 */
export const createUserSchema = z
  .object({
    full_name: z
      .string()
      .trim()
      .max(255, "Name must be at most 255 characters")
      .optional()
      .or(z.literal("")),
    email,
    password,
    confirmPassword: z.string().min(1, "Please confirm the password"),
    // `super_admin` is absent on purpose: it is not a rung on a company's
    // ladder, and the API refuses it from this endpoint anyway.
    role: z.enum(["member", "manager", "admin"]),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  });

export type LoginFormValues = z.infer<typeof loginSchema>;
export type CreateUserFormValues = z.infer<typeof createUserSchema>;
