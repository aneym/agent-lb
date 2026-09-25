import { HttpResponse, http } from "msw";
import { z } from "zod";

import {
  LIMIT_TYPES,
  LIMIT_WINDOWS,
  TRAFFIC_CLASSES,
} from "@/features/api-keys/schemas";
import {
  type AccountSummary,
  type ApiKey,
  createAccountSummary,
  createAccountTrends,
  createApiKey,
  createApiKeyCreateResponse,
  createDashboardAuthSession,
  createDashboardSettings,
  createDefaultAccounts,
  createDefaultApiKeys,
  createOauthCompleteResponse,
  createOauthStartResponse,
  createOauthStatusResponse,
  createUpstreamProxyAdmin,
  type DashboardAuthSession,
  type DashboardSettings,
  type UpstreamProxyAdmin,
} from "@/test/mocks/factories";

// ── Zod schemas for mock request bodies ──

const OauthStartPayloadSchema = z
  .object({
    forceMethod: z.string().optional(),
  })
  .passthrough();

const ApiKeyCreatePayloadSchema = z
  .object({
    name: z.string().optional(),
    trafficClass: z.enum(TRAFFIC_CLASSES).optional(),
    assignedAccountIds: z.array(z.string()).optional(),
  })
  .passthrough();

const FirewallIpCreatePayloadSchema = z
  .object({
    ipAddress: z.string().optional(),
  })
  .passthrough();

const ApiKeyUpdatePayloadSchema = z
  .object({
    name: z.string().optional(),
    allowedModels: z.array(z.string()).nullable().optional(),
    trafficClass: z.enum(TRAFFIC_CLASSES).optional(),
    isActive: z.boolean().optional(),
    assignedAccountIds: z.array(z.string()).optional(),
    resetUsage: z.boolean().optional(),
    limits: z
      .array(
        z.object({
          limitType: z.enum(LIMIT_TYPES),
          limitWindow: z.enum(LIMIT_WINDOWS),
          maxValue: z.number(),
          modelFilter: z.string().nullable().optional(),
        }),
      )
      .optional(),
  })
  .passthrough();

const AccountAliasPayloadSchema = z.object({
  alias: z.string().max(255).nullable(),
});

const AccountRoutingPolicyPayloadSchema = z.object({
  routingPolicy: z.enum(["normal", "burn_first", "preserve"]),
});

const SettingsPayloadSchema = z
  .object({
    stickyThreadsEnabled: z.boolean().optional(),
    upstreamStreamTransport: z
      .enum(["default", "auto", "http", "websocket"])
      .optional(),
    upstreamProxyRoutingEnabled: z.boolean().optional(),
    upstreamProxyDefaultPoolId: z.string().nullable().optional(),
    preferEarlierResetAccounts: z.boolean().optional(),
    routingStrategy: z
      .enum([
        "usage_weighted",
        "round_robin",
        "capacity_weighted",
        "relative_availability",
        "fill_first",
        "sequential_drain",
        "reset_drain",
        "single_account",
      ])
      .optional(),
    relativeAvailabilityPower: z.number().positive().optional(),
    relativeAvailabilityTopK: z.number().int().min(1).max(20).optional(),
    singleAccountId: z.string().nullable().optional(),
    openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
    stickyReallocationBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationPrimaryBudgetThresholdPct: z
      .number()
      .min(0)
      .max(100)
      .optional(),
    stickyReallocationSecondaryBudgetThresholdPct: z
      .number()
      .min(0)
      .max(100)
      .optional(),
    importWithoutOverwrite: z.boolean().optional(),
    totpRequiredOnLogin: z.boolean().optional(),
    totpConfigured: z.boolean().optional(),
    apiKeyAuthEnabled: z.boolean().optional(),
  })
  .passthrough();

// ── Helpers ──

async function parseJsonBody<T>(
  request: Request,
  schema: z.ZodType<T>,
): Promise<T | null> {
  try {
    const raw: unknown = await request.json();
    const result = schema.safeParse(raw);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

type MockState = {
  accounts: AccountSummary[];
  authSession: DashboardAuthSession;
  settings: DashboardSettings;
  upstreamProxyAdmin: UpstreamProxyAdmin;
  apiKeys: ApiKey[];
  firewallEntries: Array<{ ipAddress: string; createdAt: string }>;
  stickySessions: Array<{
    key: string;
    displayName: string;
    kind: "codex_session" | "sticky_thread" | "prompt_cache";
    createdAt: string;
    updatedAt: string;
    expiresAt: string | null;
    isStale: boolean;
  }>;
};

function createInitialState(): MockState {
  return {
    accounts: createDefaultAccounts(),
    authSession: createDashboardAuthSession(),
    settings: createDashboardSettings(),
    upstreamProxyAdmin: createUpstreamProxyAdmin(),
    apiKeys: createDefaultApiKeys(),
    firewallEntries: [],
    stickySessions: [],
  };
}

let state: MockState = createInitialState();

export function resetMockState(): void {
  state = createInitialState();
}

function findAccount(accountId: string): AccountSummary | undefined {
  return state.accounts.find((account) => account.accountId === accountId);
}

function findApiKey(keyId: string): ApiKey | undefined {
  return state.apiKeys.find((item) => item.id === keyId);
}

export const handlers = [
  http.get("/health", () => {
    return HttpResponse.json({ status: "ok" });
  }),

  http.get("/api/runtime/version", () => {
    return HttpResponse.json({
      currentVersion: "1.19.0",
      latestVersion: "1.19.0",
      updateAvailable: false,
      checkedAt: "2026-05-26T00:00:00Z",
      source: "github",
      releaseUrl: "https://github.com/aneym/agent-lb/releases/latest",
    });
  }),

  http.get("/api/accounts", () => {
    return HttpResponse.json({ accounts: state.accounts });
  }),

  http.post("/api/accounts/import", async () => {
    const sequence = state.accounts.length + 1;
    const created = createAccountSummary({
      accountId: `acc_imported_${sequence}`,
      email: `imported-${sequence}@example.com`,
      displayName: `imported-${sequence}@example.com`,
      status: "active",
    });
    state.accounts = [...state.accounts, created];
    return HttpResponse.json({
      accountId: created.accountId,
      email: created.email,
      planType: created.planType,
      status: created.status,
    });
  }),

  http.post("/api/accounts/:accountId/pause", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    account.status = "paused";
    return HttpResponse.json({ status: "paused" });
  }),

  http.post("/api/accounts/:accountId/reactivate", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    account.status = "active";
    return HttpResponse.json({ status: "reactivated" });
  }),

  http.put("/api/accounts/:accountId/alias", async ({ params, request }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    const payload = await parseJsonBody(request, AccountAliasPayloadSchema);
    if (!payload) {
      return HttpResponse.json(
        {
          error: { code: "validation_error", message: "Invalid alias payload" },
        },
        { status: 422 },
      );
    }
    const normalized =
      typeof payload.alias === "string" ? payload.alias.trim() : null;
    account.alias = normalized === "" ? null : normalized;
    account.displayName = account.alias ?? account.email;
    return HttpResponse.json({ accountId, alias: account.alias });
  }),

  http.put(
    "/api/accounts/:accountId/limit-warmup",
    async ({ params, request }) => {
      const accountId = String(params.accountId);
      const account = findAccount(accountId);
      if (!account) {
        return HttpResponse.json(
          {
            error: { code: "account_not_found", message: "Account not found" },
          },
          { status: 404 },
        );
      }
      const body = await request.json().catch(() => ({}));
      const enabled =
        typeof body === "object" && body !== null && "enabled" in body
          ? Boolean((body as { enabled?: unknown }).enabled)
          : false;
      account.limitWarmupEnabled = enabled;
      return HttpResponse.json({
        status: enabled ? "enabled" : "disabled",
        enabled,
      });
    },
  ),

  http.put(
    "/api/accounts/:accountId/routing-policy",
    async ({ params, request }) => {
      const accountId = String(params.accountId);
      const account = findAccount(accountId);
      if (!account) {
        return HttpResponse.json(
          {
            error: { code: "account_not_found", message: "Account not found" },
          },
          { status: 404 },
        );
      }
      const payload = await parseJsonBody(
        request,
        AccountRoutingPolicyPayloadSchema,
      );
      if (!payload) {
        return HttpResponse.json(
          {
            error: {
              code: "validation_error",
              message: "Invalid routing policy payload",
            },
          },
          { status: 422 },
        );
      }
      account.routingPolicy = payload.routingPolicy;
      return HttpResponse.json({
        accountId,
        routingPolicy: account.routingPolicy,
      });
    },
  ),

  http.patch("/api/accounts/:accountId", async ({ params, request }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    const payload = (await request.json()) as {
      securityWorkAuthorized?: boolean;
    };
    if (typeof payload.securityWorkAuthorized === "boolean") {
      account.securityWorkAuthorized = payload.securityWorkAuthorized;
    }
    return HttpResponse.json({ status: "updated" });
  }),

  http.get("/api/accounts/:accountId/trends", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    return HttpResponse.json(createAccountTrends(accountId));
  }),

  http.post("/api/accounts/:accountId/probe", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    return HttpResponse.json({
      status: "probed",
      accountId,
      probeStatusCode: 200,
      primaryUsedPercentBefore: account.usage?.primaryRemainingPercent ?? null,
      primaryUsedPercentAfter: account.usage?.primaryRemainingPercent ?? null,
      secondaryUsedPercentBefore: account.usage?.secondaryRemainingPercent ?? null,
      secondaryUsedPercentAfter: account.usage?.secondaryRemainingPercent ?? null,
      accountStatusBefore: account.status,
      accountStatusAfter: account.status,
    });
  }),

  http.get("/api/accounts/:accountId/rate-limit-reset-credits", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    const availableCount = account.resetCreditsAvailable ?? 0;
    return HttpResponse.json({
      accountId,
      availableCount,
      credits: Array.from({ length: availableCount }, (_unused, index) => ({
        id: `RateLimitResetCredit_${accountId}_${index}`,
        resetType: "codex_rate_limits",
        status: "available",
        grantedAt: new Date().toISOString(),
        expiresAt: null,
        title: "Full reset",
        description: null,
      })),
    });
  }),

  http.post(
    "/api/accounts/:accountId/rate-limit-reset-credits/consume",
    ({ params }) => {
      const accountId = String(params.accountId);
      const account = findAccount(accountId);
      if (!account) {
        return HttpResponse.json(
          { error: { code: "account_not_found", message: "Account not found" } },
          { status: 404 },
        );
      }
      const banked = account.resetCreditsAvailable ?? 0;
      if (banked < 1) {
        return HttpResponse.json({
          status: "not_redeemed",
          accountId,
          code: "no_credit",
          windowsReset: 0,
        });
      }
      account.resetCreditsAvailable = banked - 1;
      account.status = "active";
      return HttpResponse.json({
        status: "redeemed",
        accountId,
        code: "reset",
        windowsReset: 1,
        primaryUsedPercentBefore: 100,
        primaryUsedPercentAfter: 0,
        secondaryUsedPercentBefore: 100,
        secondaryUsedPercentAfter: 0,
      });
    },
  ),

  http.post("/api/accounts/:accountId/subscription/check", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    const subscription = {
      ...(account.subscription ?? {}),
      status: "active" as const,
      lastVerifiedAt: new Date().toISOString(),
      notes: "Subscription check succeeded.",
    };
    account.subscription = subscription;
    return HttpResponse.json({
      status: "checked",
      accountId,
      working: true,
      probeStatusCode: 200,
      subscription,
      message: null,
    });
  }),

	http.post("/api/accounts/:accountId/export/auth", ({ params }) => {
		const accountId = String(params.accountId);
		const account = findAccount(accountId);
		if (!account) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		return HttpResponse.json({
			filename: `opencode-auth-${account.email}.json`,
			account: {
				accountId: account.accountId,
				chatgptAccountId: account.accountId,
				email: account.email,
			},
			tokens: {
				idToken: "id-token-mock-value",
				accessToken: "access-token-mock-value",
				refreshToken: "refresh-token-mock-value",
				expiresAtMs: 2_000_000_000_000,
			},
			codexAuthJson: {
				auth_mode: "chatgpt",
				OPENAI_API_KEY: null,
				tokens: {
					id_token: "id-token",
					access_token: "access-token",
					refresh_token: "refresh-token",
					account_id: accountId,
				},
				last_refresh: "2026-01-01T12:00:00.000000Z",
			},
			opencodeAuthJson: {
				openai: {
					type: "oauth",
					refresh: "refresh-token",
					access: "access-token",
					expires: 2_000_000_000_000,
					accountId: accountId,
				},
			},
		});
	}),

	http.delete("/api/accounts/:accountId", ({ params }) => {
		const accountId = String(params.accountId);
		const exists = state.accounts.some(
			(account) => account.accountId === accountId,
		);
		if (!exists) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		state.accounts = state.accounts.filter(
			(account) => account.accountId !== accountId,
		);
		return HttpResponse.json({ status: "deleted" });
	}),

  http.post("/api/accounts/:accountId/export", ({ params }) => {
    const accountId = String(params.accountId);
    const account = findAccount(accountId);
    if (!account) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    return HttpResponse.json({
      accountId: account.accountId,
      email: account.email,
      planType: account.planType,
      status: account.status,
      authJson: JSON.stringify(
        {
          auth_mode: "chatgpt",
          OPENAI_API_KEY: null,
          tokens: {
            id_token: "id-token",
            access_token: "access-token",
            refresh_token: "refresh-token",
            account_id: accountId,
          },
          last_refresh: "2026-01-01T12:00:00.000000Z",
        },
        null,
        2,
      ),
    });
  }),

  http.delete("/api/accounts/:accountId", ({ params }) => {
    const accountId = String(params.accountId);
    const exists = state.accounts.some(
      (account) => account.accountId === accountId,
    );
    if (!exists) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    state.accounts = state.accounts.filter(
      (account) => account.accountId !== accountId,
    );
    return HttpResponse.json({ status: "deleted" });
  }),

  http.post("/api/oauth/start", async ({ request }) => {
    const payload = await parseJsonBody(request, OauthStartPayloadSchema);
    if (payload?.forceMethod === "device") {
      return HttpResponse.json(
        createOauthStartResponse({
          method: "device",
          authorizationUrl: null,
          callbackUrl: null,
          verificationUrl: "https://auth.example.com/device",
          userCode: "AAAA-BBBB",
          deviceAuthId: "device-auth-id",
          intervalSeconds: 5,
          expiresInSeconds: 900,
        }),
      );
    }
    return HttpResponse.json(createOauthStartResponse());
  }),

  http.get("/api/oauth/status", () => {
    return HttpResponse.json(createOauthStatusResponse());
  }),

  http.post("/api/oauth/complete", () => {
    return HttpResponse.json(createOauthCompleteResponse());
  }),

  http.get("/api/settings", () => {
    return HttpResponse.json(state.settings);
  }),



  http.get("/api/settings/upstream-proxy", () => {
    return HttpResponse.json(state.upstreamProxyAdmin);
  }),

  http.post("/api/settings/upstream-proxy/endpoints", async ({ request }) => {
    const payload = await parseJsonBody(
      request,
      z
        .object({
          name: z.string().min(1),
          scheme: z.enum(["http", "https", "socks5", "socks5h"]),
          host: z.string().min(1),
          port: z.number().int(),
          username: z.string().nullable().optional(),
          isActive: z.boolean().optional(),
        })
        .passthrough(),
    );
    if (!payload) {
      return HttpResponse.json(
        {
          error: {
            code: "invalid_proxy_endpoint",
            message: "Invalid proxy endpoint",
          },
        },
        { status: 400 },
      );
    }
    const endpoint = {
      id: `ep_${state.upstreamProxyAdmin.endpoints.length + 1}`,
      name: payload.name,
      scheme: payload.scheme,
      host: payload.host,
      port: payload.port,
      username: payload.username ?? null,
      isActive: payload.isActive ?? true,
    };
    state.upstreamProxyAdmin = {
      ...state.upstreamProxyAdmin,
      endpoints: [...state.upstreamProxyAdmin.endpoints, endpoint],
    };
    return HttpResponse.json(endpoint);
  }),

  http.post("/api/settings/upstream-proxy/pools", async ({ request }) => {
    const payload = await parseJsonBody(
      request,
      z
        .object({
          name: z.string().min(1),
          endpointIds: z.array(z.string()).optional(),
          isActive: z.boolean().optional(),
        })
        .passthrough(),
    );
    if (!payload) {
      return HttpResponse.json(
        { error: { code: "invalid_proxy_pool", message: "Invalid proxy pool" } },
        { status: 400 },
      );
    }
    const pool = {
      id: `pool_${state.upstreamProxyAdmin.pools.length + 1}`,
      name: payload.name,
      isActive: payload.isActive ?? true,
      endpointIds: payload.endpointIds ?? [],
    };
    state.upstreamProxyAdmin = {
      ...state.upstreamProxyAdmin,
      pools: [...state.upstreamProxyAdmin.pools, pool],
    };
    return HttpResponse.json(pool);
  }),

  http.post(
    "/api/settings/upstream-proxy/pools/:poolId/members",
    async ({ params, request }) => {
      const poolId = String(params.poolId);
      const payload = await parseJsonBody(
        request,
        z.object({ endpointId: z.string().min(1) }).passthrough(),
      );
      const pool = state.upstreamProxyAdmin.pools.find(
        (item) => item.id === poolId,
      );
      if (!pool || !payload) {
        return HttpResponse.json(
          {
            error: {
              code: "proxy_pool_not_found",
              message: "Proxy pool not found",
            },
          },
          { status: 404 },
        );
      }
      if (pool.endpointIds.includes(payload.endpointId)) {
        return HttpResponse.json(
          {
            error: {
              code: "proxy_pool_member_duplicate",
              message: "Proxy endpoint is already a member of this pool",
            },
          },
          { status: 400 },
        );
      }
      const updatedPool = {
        ...pool,
        endpointIds: [...pool.endpointIds, payload.endpointId],
      };
      state.upstreamProxyAdmin = {
        ...state.upstreamProxyAdmin,
        pools: state.upstreamProxyAdmin.pools.map((item) =>
          item.id === poolId ? updatedPool : item,
        ),
      };
      return HttpResponse.json(updatedPool);
    },
  ),

  http.put(
    "/api/settings/upstream-proxy/accounts/:accountId/binding",
    async ({ params, request }) => {
      const accountId = String(params.accountId);
      const payload = await parseJsonBody(
        request,
        z
          .object({ poolId: z.string().min(1), isActive: z.boolean().optional() })
          .passthrough(),
      );
      if (!payload) {
        return HttpResponse.json(
          {
            error: {
              code: "invalid_proxy_binding",
              message: "Invalid proxy binding",
            },
          },
          { status: 400 },
        );
      }
      const binding = {
        accountId,
        poolId: payload.poolId,
        isActive: payload.isActive ?? true,
      };
      state.upstreamProxyAdmin = {
        ...state.upstreamProxyAdmin,
        bindings: [
          ...state.upstreamProxyAdmin.bindings.filter(
            (item) => item.accountId !== accountId,
          ),
          binding,
        ],
      };
      return HttpResponse.json(binding);
    },
  ),

  http.get("/api/firewall/ips", () => {
    return HttpResponse.json({
      mode:
        state.firewallEntries.length === 0 ? "allow_all" : "allowlist_active",
      entries: state.firewallEntries,
    });
  }),

  http.post("/api/firewall/ips", async ({ request }) => {
    const payload = await parseJsonBody(request, FirewallIpCreatePayloadSchema);
    const ipAddress = String(payload?.ipAddress || "").trim();
    if (!ipAddress) {
      return HttpResponse.json(
        { error: { code: "invalid_ip", message: "IP address is required" } },
        { status: 400 },
      );
    }
    if (state.firewallEntries.some((entry) => entry.ipAddress === ipAddress)) {
      return HttpResponse.json(
        { error: { code: "ip_exists", message: "IP address already exists" } },
        { status: 409 },
      );
    }
    const created = { ipAddress, createdAt: new Date().toISOString() };
    state.firewallEntries = [...state.firewallEntries, created];
    return HttpResponse.json(created);
  }),

  http.delete("/api/firewall/ips/:ipAddress", ({ params }) => {
    const ipAddress = decodeURIComponent(String(params.ipAddress));
    const exists = state.firewallEntries.some(
      (entry) => entry.ipAddress === ipAddress,
    );
    if (!exists) {
      return HttpResponse.json(
        { error: { code: "ip_not_found", message: "IP address not found" } },
        { status: 404 },
      );
    }
    state.firewallEntries = state.firewallEntries.filter(
      (entry) => entry.ipAddress !== ipAddress,
    );
    return HttpResponse.json({ status: "deleted" });
  }),

  http.put("/api/settings", async ({ request }) => {
    const payload = await parseJsonBody(request, SettingsPayloadSchema);
    if (!payload) {
      return HttpResponse.json(state.settings);
    }
    state.settings = createDashboardSettings({
      ...state.settings,
      ...payload,
    });
    if (
      payload.upstreamProxyRoutingEnabled !== undefined ||
      payload.upstreamProxyDefaultPoolId !== undefined
    ) {
      state.upstreamProxyAdmin = {
        ...state.upstreamProxyAdmin,
        routingEnabled:
          payload.upstreamProxyRoutingEnabled ??
          state.upstreamProxyAdmin.routingEnabled,
        defaultPoolId:
          payload.upstreamProxyDefaultPoolId !== undefined
            ? payload.upstreamProxyDefaultPoolId
            : state.upstreamProxyAdmin.defaultPoolId,
      };
    }
    return HttpResponse.json(state.settings);
  }),

  http.get("/api/sticky-sessions", ({ request }) => {
    const url = new URL(request.url);
    const staleOnly = url.searchParams.get("staleOnly") === "true";
    const accountQuery = (url.searchParams.get("accountQuery") ?? "")
      .trim()
      .toLowerCase();
    const keyQuery = (url.searchParams.get("keyQuery") ?? "")
      .trim()
      .toLowerCase();
    const sortBy = url.searchParams.get("sortBy") ?? "updated_at";
    const sortDir = url.searchParams.get("sortDir") ?? "desc";
    const offset = Number(url.searchParams.get("offset") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "10");
    const filteredEntries = state.stickySessions
      .filter((entry) => {
        if (staleOnly && !(entry.kind === "prompt_cache" && entry.isStale)) {
          return false;
        }
        if (
          accountQuery &&
          !entry.displayName.toLowerCase().includes(accountQuery)
        ) {
          return false;
        }
        if (keyQuery && !entry.key.toLowerCase().includes(keyQuery)) {
          return false;
        }
        return true;
      })
      .sort((left, right) => {
        const direction = sortDir === "asc" ? 1 : -1;
        if (sortBy === "account") {
          return left.displayName.localeCompare(right.displayName) * direction;
        }
        if (sortBy === "key") {
          return left.key.localeCompare(right.key) * direction;
        }
        const leftTime = Date.parse(
          sortBy === "created_at" ? left.createdAt : left.updatedAt,
        );
        const rightTime = Date.parse(
          sortBy === "created_at" ? right.createdAt : right.updatedAt,
        );
        if (leftTime !== rightTime) {
          return (leftTime - rightTime) * direction;
        }
        return left.key.localeCompare(right.key);
      });
    const entries = filteredEntries.slice(offset, offset + limit);
    const stalePromptCacheCount = state.stickySessions.filter(
      (entry) => entry.kind === "prompt_cache" && entry.isStale,
    ).length;
    return HttpResponse.json({
      entries,
      stalePromptCacheCount,
      total: filteredEntries.length,
      hasMore: offset + entries.length < filteredEntries.length,
    });
  }),

  http.post("/api/sticky-sessions/delete", async ({ request }) => {
    const payload = (await parseJsonBody(
      request,
      z.object({
        sessions: z
          .array(
            z.object({
              key: z.string().min(1),
              kind: z.enum(["codex_session", "sticky_thread", "prompt_cache"]),
            }),
          )
          .min(1)
          .max(500)
          .refine(
            (sessions) =>
              new Set(
                sessions.map((session) => `${session.kind}:${session.key}`),
              ).size === sessions.length,
            "Duplicate sticky session targets are not allowed",
          ),
      }),
    )) ?? { sessions: [] };
    const targets = new Set(
      payload.sessions.map((session) => `${session.kind}:${session.key}`),
    );
    const deleted = state.stickySessions
      .filter((entry) => targets.has(`${entry.kind}:${entry.key}`))
      .map((entry) => ({ key: entry.key, kind: entry.kind }));
    const deletedTargets = new Set(
      deleted.map((entry) => `${entry.kind}:${entry.key}`),
    );
    state.stickySessions = state.stickySessions.filter(
      (entry) => !targets.has(`${entry.kind}:${entry.key}`),
    );
    return HttpResponse.json({
      deletedCount: deleted.length,
      deleted,
      failed: payload.sessions
        .filter(
          (session) => !deletedTargets.has(`${session.kind}:${session.key}`),
        )
        .map((session) => ({
          key: session.key,
          kind: session.kind,
          reason: "not_found",
        })),
    });
  }),

  http.post("/api/sticky-sessions/delete-filtered", async ({ request }) => {
    const payload = (await parseJsonBody(
      request,
      z.object({
        staleOnly: z.boolean().default(false),
        accountQuery: z.string().default(""),
        keyQuery: z.string().default(""),
      }),
    )) ?? {
      staleOnly: false,
      accountQuery: "",
      keyQuery: "",
    };
    const accountQuery = payload.accountQuery.trim().toLowerCase();
    const keyQuery = payload.keyQuery.trim().toLowerCase();
    const matched = state.stickySessions.filter((entry) => {
      if (
        payload.staleOnly &&
        !(entry.kind === "prompt_cache" && entry.isStale)
      ) {
        return false;
      }
      if (
        accountQuery &&
        !entry.displayName.toLowerCase().includes(accountQuery)
      ) {
        return false;
      }
      if (keyQuery && !entry.key.toLowerCase().includes(keyQuery)) {
        return false;
      }
      return true;
    });
    const targets = new Set(
      matched.map((entry) => `${entry.kind}:${entry.key}`),
    );
    state.stickySessions = state.stickySessions.filter(
      (entry) => !targets.has(`${entry.kind}:${entry.key}`),
    );
    return HttpResponse.json({ deletedCount: matched.length });
  }),

  http.post("/api/sticky-sessions/purge", async ({ request }) => {
    const payload = (await parseJsonBody(
      request,
      z.object({ staleOnly: z.boolean().default(true) }),
    )) ?? {
      staleOnly: true,
    };
    if (payload.staleOnly) {
      const before = state.stickySessions.length;
      state.stickySessions = state.stickySessions.filter(
        (entry) => !entry.isStale,
      );
      return HttpResponse.json({
        deletedCount: before - state.stickySessions.length,
      });
    }
    const deletedCount = state.stickySessions.length;
    state.stickySessions = [];
    return HttpResponse.json({ deletedCount });
  }),

  http.get("/api/dashboard-auth/session", () => {
    return HttpResponse.json(state.authSession);
  }),

  http.post("/api/dashboard-auth/password/setup", () => {
    state.authSession = createDashboardAuthSession({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: state.authSession.totpConfigured,
    });
    return HttpResponse.json(state.authSession);
  }),

  http.post("/api/dashboard-auth/password/login", () => {
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      authenticated: !state.authSession.totpRequiredOnLogin,
    });
    return HttpResponse.json(state.authSession);
  }),

  http.post("/api/dashboard-auth/password/change", () => {
    return HttpResponse.json({ status: "ok" });
  }),

  http.delete("/api/dashboard-auth/password", () => {
    state.authSession = createDashboardAuthSession({
      authenticated: false,
      passwordRequired: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
    });
    return HttpResponse.json({ status: "ok" });
  }),

  http.post("/api/dashboard-auth/totp/setup/start", () => {
    return HttpResponse.json({
      secret: "JBSWY3DPEHPK3PXP",
      otpauthUri: "otpauth://totp/agent-lb?secret=JBSWY3DPEHPK3PXP",
      qrSvgDataUri: "data:image/svg+xml;base64,PHN2Zy8+",
    });
  }),

  http.post("/api/dashboard-auth/totp/setup/confirm", () => {
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      totpConfigured: true,
      authenticated: true,
    });
    return HttpResponse.json({ status: "ok" });
  }),

  http.post("/api/dashboard-auth/totp/verify", () => {
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      authenticated: true,
    });
    return HttpResponse.json(state.authSession);
  }),

  http.post("/api/dashboard-auth/totp/disable", () => {
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      totpConfigured: false,
      totpRequiredOnLogin: false,
      authenticated: true,
    });
    return HttpResponse.json({ status: "ok" });
  }),

  http.post("/api/dashboard-auth/logout", () => {
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      authenticated: false,
    });
    return HttpResponse.json({ status: "ok" });
  }),

  http.get("/api/models", () => {
    return HttpResponse.json({
      models: [
        { id: "gpt-5.1", name: "GPT 5.1" },
        { id: "gpt-5.1-codex-mini", name: "GPT 5.1 Codex Mini" },
        { id: "gpt-4o-mini", name: "GPT 4o Mini" },
      ],
    });
  }),

  http.get("/api/api-keys/", () => {
    return HttpResponse.json(state.apiKeys);
  }),

  http.post("/api/api-keys/", async ({ request }) => {
    const payload = await parseJsonBody(request, ApiKeyCreatePayloadSchema);
    const sequence = state.apiKeys.length + 1;
    const created = createApiKeyCreateResponse({
      ...createApiKey({
        id: `key_${sequence}`,
        name: payload?.name ?? `API Key ${sequence}`,
        accountAssignmentScopeEnabled:
          (payload?.assignedAccountIds?.length ?? 0) > 0,
        assignedAccountIds: payload?.assignedAccountIds ?? [],
        trafficClass: payload?.trafficClass ?? "foreground",
      }),
      key: `sk-test-generated-${sequence}`,
    });
    state.apiKeys = [...state.apiKeys, createApiKey(created)];
    return HttpResponse.json(created);
  }),

  http.patch("/api/api-keys/:keyId", async ({ params, request }) => {
    const keyId = String(params.keyId);
    const existing = findApiKey(keyId);
    if (!existing) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "API key not found" } },
        { status: 404 },
      );
    }
    const payload = await parseJsonBody(request, ApiKeyUpdatePayloadSchema);
    if (!payload) {
      return HttpResponse.json(existing);
    }

    // Build override with converted limits (create format → response format)
    const overrides: Partial<ApiKey> = {
      ...(payload.name !== undefined ? { name: payload.name } : {}),
      ...(payload.allowedModels !== undefined
        ? { allowedModels: payload.allowedModels }
        : {}),
      ...(payload.isActive !== undefined ? { isActive: payload.isActive } : {}),
      ...(payload.trafficClass !== undefined
        ? { trafficClass: payload.trafficClass }
        : {}),
      ...(payload.assignedAccountIds !== undefined
        ? {
            accountAssignmentScopeEnabled:
              payload.assignedAccountIds.length > 0,
          }
        : {}),
      ...(payload.assignedAccountIds !== undefined
        ? { assignedAccountIds: payload.assignedAccountIds }
        : {}),
    };

    if (payload.limits) {
      overrides.limits = payload.limits.map((l, idx) => ({
        id: idx + 100,
        limitType: l.limitType,
        limitWindow: l.limitWindow,
        maxValue: l.maxValue,
        currentValue: 0,
        modelFilter: l.modelFilter ?? null,
        resetAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
      }));
    }

    const updated = createApiKey({
      ...existing,
      ...overrides,
      id: keyId,
    });
    state.apiKeys = state.apiKeys.map((item) =>
      item.id === keyId ? updated : item,
    );
    return HttpResponse.json(updated);
  }),

  http.delete("/api/api-keys/:keyId", ({ params }) => {
    const keyId = String(params.keyId);
    const exists = state.apiKeys.some((item) => item.id === keyId);
    if (!exists) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "API key not found" } },
        { status: 404 },
      );
    }
    state.apiKeys = state.apiKeys.filter((item) => item.id !== keyId);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/api-keys/:keyId/regenerate", ({ params }) => {
    const keyId = String(params.keyId);
    const existing = findApiKey(keyId);
    if (!existing) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "API key not found" } },
        { status: 404 },
      );
    }
    const regenerated = createApiKeyCreateResponse({
      ...existing,
      key: `sk-test-regenerated-${keyId}`,
    });
    state.apiKeys = state.apiKeys.map((item) =>
      item.id === keyId ? createApiKey(regenerated) : item,
    );
    return HttpResponse.json(regenerated);
  }),

];
