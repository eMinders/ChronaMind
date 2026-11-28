import { describe, expect, it } from "vitest";
import { looksLikeEmail, passwordProblems } from "./passwordRules";

describe("passwordProblems (the server's rules)", () => {
  it("accepts a password that meets every rule", () => {
    expect(passwordProblems("Demo!pass1")).toEqual([]);
  });

  it("names each broken rule", () => {
    expect(passwordProblems("short")).toEqual([
      "At least 8 characters",
      "An uppercase letter",
      "A number",
      'A special character: !@#$%^&*(),.?":{}|<>',
    ]);
  });

  it.each(["-", "_", "+", "=", "~"])(
    "does not count %s as a special character, like the server",
    (character) => {
      expect(passwordProblems(`Demopass1${character}`)).toEqual([
        'A special character: !@#$%^&*(),.?":{}|<>',
      ]);
    },
  );

  it("refuses more than 64 characters", () => {
    expect(passwordProblems(`A!a1${"x".repeat(61)}`)).toEqual([
      "At most 64 characters",
    ]);
  });
});

describe("looksLikeEmail", () => {
  it.each([
    ["me@example.com", true],
    ["  me@example.com  ", true],
    ["me@example", false],
    ["me example.com", false],
    ["", false],
  ])("%s → %s", (value, expected) => {
    expect(looksLikeEmail(value)).toBe(expected);
  });
});
