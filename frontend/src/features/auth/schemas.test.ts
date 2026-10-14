import { describe, expect, it } from "vitest";

import {
  AuthSessionSchema,
  LoginRequestSchema,
  PasswordChangeRequestSchema,
  PasswordSetupRequestSchema,
} from "@/features/auth/schemas";

describe("AuthSessionSchema", () => {
  it("parses valid auth session payload", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      authMode: "trusted_header",
      passwordManagementEnabled: true,
    });

    expect(parsed).toEqual({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      bootstrapRequired: false,
      bootstrapTokenConfigured: false,
      authMode: "trusted_header",
      passwordManagementEnabled: true,
      passwordSessionActive: false,
    });
  });

  it("rejects missing required fields", () => {
    const result = AuthSessionSchema.safeParse({
      authenticated: true,
      passwordRequired: false,
      totpRequiredOnLogin: false,
    });

    expect(result.success).toBe(false);
  });

  it("defaults optional auth mode fields for older responses", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
    });

    expect(parsed.bootstrapRequired).toBe(false);
    expect(parsed.bootstrapTokenConfigured).toBe(false);
    expect(parsed.authMode).toBe("standard");
    expect(parsed.passwordManagementEnabled).toBe(true);
  });
});

describe("LoginRequestSchema", () => {
  it("accepts non-empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        password: "strong-password",
      }).success,
    ).toBe(true);
  });

  it("rejects empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        password: "",
      }).success,
    ).toBe(false);
  });
});

describe("dashboard password length cap (#615)", () => {





  it("PasswordSetupRequestSchema accepts a password whose UTF-8 length is exactly 72 bytes", () => {
    const exact = "a".repeat(72);
    expect(new TextEncoder().encode(exact)).toHaveLength(72);
    expect(
      PasswordSetupRequestSchema.safeParse({
        password: exact,
      }).success,
    ).toBe(true);
  });

  it("PasswordSetupRequestSchema rejects an ASCII password whose UTF-8 length exceeds 72 bytes", () => {
    const tooLong = "a".repeat(73);
    expect(new TextEncoder().encode(tooLong)).toHaveLength(73);
    const result = PasswordSetupRequestSchema.safeParse({
      password: tooLong,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toMatch(/72 bytes/);
    }
  });

  it("PasswordSetupRequestSchema rejects a multi-byte password whose UTF-8 length exceeds 72 bytes", () => {

    const emojiPassword = "🔒".repeat(19);
    expect(new TextEncoder().encode(emojiPassword).length).toBe(76);
    const result = PasswordSetupRequestSchema.safeParse({
      password: emojiPassword,
    });
    expect(result.success).toBe(false);
  });

  it("PasswordSetupRequestSchema still rejects passwords below the 8-character minimum", () => {
    expect(
      PasswordSetupRequestSchema.safeParse({
        password: "short",
      }).success,
    ).toBe(false);
  });

  it("PasswordChangeRequestSchema enforces the 72-byte cap on newPassword", () => {
    const tooLong = "z".repeat(73);
    const result = PasswordChangeRequestSchema.safeParse({
      currentPassword: "current-password-x",
      newPassword: tooLong,
    });
    expect(result.success).toBe(false);
    if (!result.success) {


      expect(result.error.issues[0]?.path).toContain("newPassword");
    }
  });

  it("PasswordChangeRequestSchema accepts a 72-byte newPassword", () => {
    expect(
      PasswordChangeRequestSchema.safeParse({
        currentPassword: "current-password-x",
        newPassword: "b".repeat(72),
      }).success,
    ).toBe(true);
  });
});
