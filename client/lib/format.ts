/** Display formatting shared across the invoice screens. */

/**
 * A money amount, always to two decimals.
 *
 * Null renders as an em dash rather than "0.00": an amount nobody extracted and
 * an amount that is genuinely zero are different facts, and on an
 * accounts-payable screen they must not look the same.
 */
export function money(
  value: number | null | undefined,
  currency?: string | null,
): string {
  if (value == null) return "—";
  return `${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}${currency ? ` ${currency}` : ""}`;
}
