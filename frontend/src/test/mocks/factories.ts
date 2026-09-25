import type { z } from "zod";
import type {
	AccountSummary,
	AccountTrendsResponse,
	OauthStartResponse,
	OauthStatusResponse,
} from "@/features/accounts/schemas";
import {
	AccountSummarySchema,
	AccountTrendsResponseSchema,
	OauthCompleteResponseSchema,
	OauthStartResponseSchema,
	OauthStatusResponseSchema,
} from "@/features/accounts/schemas";
import type { ApiKey, ApiKeyCreateResponse } from "@/features/api-keys/schemas";
import {
	ApiKeyCreateResponseSchema,
	ApiKeySchema,
} from "@/features/api-keys/schemas";
import type { AuthSession } from "@/features/auth/schemas";
import { AuthSessionSchema } from "@/features/auth/schemas";
import type { DashboardSettings, UpstreamProxyAdmin } from "@/features/settings/schemas";
import { DashboardSettingsSchema, UpstreamProxyAdminSchema } from "@/features/settings/schemas";

// Backward-compatible type aliases
export type DashboardAuthSession = AuthSession;
export type OauthCompleteResponse = z.infer<typeof OauthCompleteResponseSchema>;

export type {
	AccountSummary,
	AccountTrendsResponse,
	DashboardSettings,
	UpstreamProxyAdmin,
	OauthStartResponse,
	OauthStatusResponse,
	ApiKey,
	ApiKeyCreateResponse,
};

const BASE_TIME = new Date("2026-01-01T12:00:00Z");

function offsetIso(minutes: number): string {
	return new Date(BASE_TIME.getTime() + minutes * 60_000).toISOString();
}

export function createAccountSummary(
	overrides: Partial<AccountSummary> = {},
): AccountSummary {
	return AccountSummarySchema.parse({
		accountId: "acc_primary",
		email: "primary@example.com",
		alias: null,
		displayName: "primary@example.com",
		planType: "plus",
		routingPolicy: "normal",
		status: "active",
		securityWorkAuthorized: false,
		usage: {
			primaryRemainingPercent: 82,
			secondaryRemainingPercent: 67,
			monthlyRemainingPercent: null,
		},
		resetAtPrimary: offsetIso(60),
		resetAtSecondary: offsetIso(24 * 60),
		resetAtMonthly: null,
		windowMinutesPrimary: 300,
		windowMinutesSecondary: 10_080,
		windowMinutesMonthly: null,
		capacityCreditsPrimary: 225,
		remainingCreditsPrimary: 184.5,
		capacityCreditsSecondary: 7_560,
		remainingCreditsSecondary: 5_065.2,
		capacityCreditsMonthly: null,
		remainingCreditsMonthly: null,
		creditsHas: true,
		creditsUnlimited: false,
		creditsBalance: 932,
		auth: {
			access: { expiresAt: offsetIso(30), state: null },
			refresh: { state: "stored" },
			idToken: { state: "parsed" },
		},
		limitWarmupEnabled: false,
		limitWarmup: null,
		...overrides,
	});
}

export function createDefaultAccounts(): AccountSummary[] {
	return [
		createAccountSummary(),
		createAccountSummary({
			accountId: "acc_secondary",
			email: "secondary@example.com",
			displayName: "secondary@example.com",
			status: "paused",
			usage: {
				primaryRemainingPercent: 45,
				secondaryRemainingPercent: 12,
			},
		}),
	];
}

export function createDashboardAuthSession(
	overrides: Partial<DashboardAuthSession> = {},
): DashboardAuthSession {
	return AuthSessionSchema.parse({
		authenticated: true,
		passwordRequired: true,
		totpRequiredOnLogin: false,
		totpConfigured: true,
		authMode: "standard",
		passwordManagementEnabled: true,
		...overrides,
	});
}

export function createDashboardSettings(
	overrides: Partial<DashboardSettings> = {},
): DashboardSettings {
	return DashboardSettingsSchema.parse({
		stickyThreadsEnabled: true,
		upstreamStreamTransport: "default",
		upstreamProxyRoutingEnabled: false,
		upstreamProxyDefaultPoolId: null,
		preferEarlierResetAccounts: false,
		preferEarlierResetWindow: "secondary",
		routingStrategy: "usage_weighted",
		relativeAvailabilityPower: 2,
		relativeAvailabilityTopK: 5,
		singleAccountId: null,
		weeklyPaceWorkingDays: "0,1,2,3,4,5,6",
		openaiCacheAffinityMaxAgeSeconds: 300,
		dashboardSessionTtlSeconds: 43200,
		stickyReallocationBudgetThresholdPct: 95,
		stickyReallocationPrimaryBudgetThresholdPct: 95,
		stickyReallocationSecondaryBudgetThresholdPct: 100,
		warmupModel: "gpt-5.4-mini",
		importWithoutOverwrite: false,
		totpRequiredOnLogin: false,
		totpConfigured: true,
		apiKeyAuthEnabled: true,
		teamModeEnabled: false,
		teamPublicBaseUrl: null,
		limitWarmupEnabled: false,
		limitWarmupWindows: "both",
		limitWarmupModel: "auto",
		limitWarmupPrompt: "Say OK.",
		limitWarmupCooldownSeconds: 3600,
		limitWarmupMinAvailablePercent: 100,
		...overrides,
	});
}

export function createUpstreamProxyAdmin(
	overrides: Partial<UpstreamProxyAdmin> = {},
): UpstreamProxyAdmin {
	return UpstreamProxyAdminSchema.parse({
		routingEnabled: false,
		defaultPoolId: null,
		endpoints: [
			{
				id: "ep_primary",
				name: "Primary proxy",
				scheme: "http",
				host: "proxy-primary.test",
				port: 8080,
				username: "operator",
				isActive: true,
			},
		],
		pools: [
			{
				id: "pool_primary",
				name: "Primary pool",
				isActive: true,
				endpointIds: ["ep_primary"],
			},
		],
		bindings: [],
		...overrides,
	});
}

export function createOauthStartResponse(
	overrides: Partial<OauthStartResponse> = {},
): OauthStartResponse {
	return OauthStartResponseSchema.parse({
		method: "browser",
		authorizationUrl: "https://auth.example.com/start",
		callbackUrl: "http://localhost:3000/api/oauth/callback",
		verificationUrl: null,
		userCode: null,
		deviceAuthId: null,
		intervalSeconds: null,
		expiresInSeconds: null,
		...overrides,
	});
}

export function createOauthStatusResponse(
	overrides: Partial<OauthStatusResponse> = {},
): OauthStatusResponse {
	return OauthStatusResponseSchema.parse({
		status: "pending",
		errorMessage: null,
		...overrides,
	});
}

export function createOauthCompleteResponse(
	overrides: Partial<OauthCompleteResponse> = {},
): OauthCompleteResponse {
	return OauthCompleteResponseSchema.parse({
		status: "ok",
		...overrides,
	});
}

export function createApiKey(overrides: Partial<ApiKey> = {}): ApiKey {
	return ApiKeySchema.parse({
		id: "key_1",
		name: "Default key",
		keyPrefix: "sk-test",
		allowedModels: ["gpt-5.1"],
		applyToCodexModel: false,
		expiresAt: null,
		isActive: true,
		accountAssignmentScopeEnabled: false,
		assignedAccountIds: [],
		createdAt: offsetIso(-60),
		lastUsedAt: offsetIso(-5),
		usageSummary: {
			requestCount: 150,
			totalTokens: 50_000,
			cachedInputTokens: 10_000,
			totalCostUsd: 1.23,
		},
		limits: [
			{
				id: 1,
				limitType: "total_tokens",
				limitWindow: "weekly",
				maxValue: 1_000_000,
				currentValue: 125_000,
				modelFilter: null,
				resetAt: offsetIso(7 * 24 * 60),
			},
		],
		...overrides,
	});
}

export function createApiKeyCreateResponse(
	overrides: Partial<ApiKeyCreateResponse> = {},
): ApiKeyCreateResponse {
	return ApiKeyCreateResponseSchema.parse({
		...createApiKey(),
		key: "sk-test-generated-secret",
		...overrides,
	});
}

export function createDefaultApiKeys(): ApiKey[] {
	return [
		createApiKey(),
		createApiKey({
			id: "key_2",
			name: "Read only key",
			keyPrefix: "sk-second",
			allowedModels: ["gpt-4o-mini"],
			isActive: false,
			expiresAt: null,
			lastUsedAt: null,
			usageSummary: {
				requestCount: 42,
				totalTokens: 12_500,
				cachedInputTokens: 2_200,
				totalCostUsd: 0.42,
			},
			limits: [],
		}),
	];
}

function createUsageTrendPoints(
	basePercent: number,
	count = 28,
): Array<{ t: string; v: number }> {
	return Array.from({ length: count }, (_, i) => ({
		t: new Date(BASE_TIME.getTime() - (count - i) * 6 * 3600_000).toISOString(),
		v: Math.max(0, Math.min(100, basePercent + Math.sin(i) * 15)),
	}));
}

export function createAccountTrends(
	accountId: string,
	overrides: Partial<AccountTrendsResponse> = {},
): AccountTrendsResponse {
	return AccountTrendsResponseSchema.parse({
		accountId,
		primary: createUsageTrendPoints(80),
		secondary: createUsageTrendPoints(55),
		...overrides,
	});
}

