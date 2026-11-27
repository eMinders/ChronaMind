/**
 * The server's own rules (`app/schemas/auth.py::UserCreate`,
 * `app/utils/sanitization.py::validate_password_strength`), checked before
 * sending so the user sees every problem at once. The server stays the judge:
 * its answer is shown if it still refuses.
 */

/** Exactly the server's special characters. `-` and `_` do not count. */
export const SPECIAL_CHARACTERS = '!@#$%^&*(),.?":{}|<>';

const RULES: ReadonlyArray<{
  test: (password: string) => boolean;
  message: string;
}> = [
  { test: (p) => p.length >= 8, message: "At least 8 characters" },
  { test: (p) => p.length <= 64, message: "At most 64 characters" },
  { test: (p) => /[A-Z]/.test(p), message: "An uppercase letter" },
  { test: (p) => /[a-z]/.test(p), message: "A lowercase letter" },
  { test: (p) => /[0-9]/.test(p), message: "A number" },
  {
    test: (p) => [...p].some((c) => SPECIAL_CHARACTERS.includes(c)),
    message: `A special character: ${SPECIAL_CHARACTERS}`,
  },
];

/** The rules a password breaks; empty when it is acceptable. */
export function passwordProblems(password: string): string[] {
  return RULES.filter((rule) => !rule.test(password)).map(
    (rule) => rule.message,
  );
}

/** A light check only: the server's email validator decides. */
export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

export const MAX_DISPLAY_NAME = 50;
