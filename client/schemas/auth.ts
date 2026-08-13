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

export const registerSchema = z
  .object({
    full_name: z
      .string()
      .trim()
      .max(255, "Name must be at most 255 characters")
      .optional()
      .or(z.literal("")),
    email,
    password,
    confirmPassword: z.string().min(1, "Please confirm your password"),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  });

export type LoginFormValues = z.infer<typeof loginSchema>;
export type RegisterFormValues = z.infer<typeof registerSchema>;
