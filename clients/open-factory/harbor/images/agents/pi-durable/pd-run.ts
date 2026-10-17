import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync, writeSync } from "node:fs";
import { hostname } from "node:os";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { Type } from "@earendil-works/pi-ai";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";
import { openAICodexResponsesApi } from "@earendil-works/pi-ai/api/openai-codex-responses.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";
import { createModels, createProvider } from "@earendil-works/pi-ai/models";
import { fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai/providers/faux";
import { AssistantEntry, createRegistry, defineExtension, defineTool, Harness, SessionFailed, section, watchEvents, wrapTool } from "@earendil-works/pi-durable";
import { NodeExecutionEnv } from "@earendil-works/pi-durable/env/node";
import { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";
import { CodingTools, createBashTool } from "@earendil-works/pi-durable/tools";

const { values: opt } = parseArgs({
	options: {
		db: { type: "string" },
		"request-id": { type: "string" },
		cwd: { type: "string" },
		provider: { type: "string", default: "lb-responses" },
		"faux-script": { type: "string" },
		model: { type: "string" },
		effort: { type: "string", default: "medium" },
		"prompt-file": { type: "string" },
		events: { type: "string" },
		last: { type: "string" },
		"max-retries": { type: "string", default: "3" },
		"pd-dir": { type: "string" },
	},
});
if (!opt.db || !opt["request-id"] || !opt.cwd) {
	process.stderr.write("usage: pd-run.ts --db <path> --request-id <id> --cwd <dir> [...]\n");
	process.exit(64);
}
const db = resolve(opt.db);
const cwd = resolve(opt.cwd);
const requestId = opt["request-id"];
const pdDir = opt["pd-dir"] ? resolve(opt["pd-dir"]) : join(cwd, ".pd");
mkdirSync(pdDir, { recursive: true });
const log = (line: string) => process.stderr.write(`pd-run: ${line}\n`);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const lockPath = `${db}.lock`;
const host = hostname();
function pidAlive(pid: number): boolean {
	if (pid === process.pid) return false;
	try {
		process.kill(pid, 0);
		return true;
	} catch (error) {
		return (error as NodeJS.ErrnoException).code === "EPERM";
	}
}
function takeLock(): void {
	for (let attempt = 0; attempt < 3; attempt++) {
		try {
			const fd = openSync(lockPath, "wx");
			writeSync(fd, JSON.stringify({ pid: process.pid, host, started: new Date().toISOString() }));
			closeSync(fd);
			return;
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
			let held: { pid?: number; host?: string } = {};
			try {
				held = JSON.parse(readFileSync(lockPath, "utf8"));
			} catch {}
			if (held.host === host && typeof held.pid === "number" && pidAlive(held.pid)) {
				log(`busy: pid ${held.pid} on ${held.host} owns ${db}`);
				process.exit(75);
			}
			if (held.host !== undefined && held.host !== host) {
				log(`busy: lock held by host ${held.host}, refusing cross-host takeover`);
				process.exit(75);
			}
			log(`stale-lock: old pid ${held.pid} host ${held.host} is gone; new pid ${process.pid} host ${host}`);
			unlinkSync(lockPath);
		}
	}
	process.exit(75);
}
takeLock();
const releaseLock = () => {
	try {
		const held = JSON.parse(readFileSync(lockPath, "utf8"));
		if (held.pid === process.pid && held.host === host) unlinkSync(lockPath);
	} catch {}
};

const pause = (process.env.PD_PAUSE_AT ?? "").split(":");
const pauseName = pause[0];
const pauseAt = Number(pause[1] ?? 0);
const seen = new Map<string, number>();
function shouldPause(name: string): boolean {
	const n = (seen.get(name) ?? 0) + 1;
	seen.set(name, n);
	if (name !== pauseName || n !== pauseAt) return false;
	writeFileSync(join(pdDir, `at-${name}-${n}`), `${process.pid}\n`);
	log(`paused at ${name}:${n} pid ${process.pid}`);
	return true;
}

const SideEffect = defineTool({
	name: "side_effect",
	description: "Record an external side effect exactly once under a key, such as sending a report.",
	parameters: Type.Object({ key: Type.String(), text: Type.String() }),
	execute: async (args, api, context) => {
		appendFileSync(join(pdDir, "side-effect-calls"), `${process.pid}\t${args.key}\n`);
		const memo = `effect-${args.key}`;
		if ((await api.memo(memo, context)) !== undefined) {
			return { output: [{ type: "text", text: `already done: ${args.key}` }] };
		}
		await api.memo(memo, true, context);
		appendFileSync(join(pdDir, "side-effects.log"), `${args.key}\t${args.text.replace(/\s+/g, " ")}\n`);
		if (shouldPause("side_effect")) await sleep(30000);
		return { output: [{ type: "text", text: `recorded: ${args.key}` }] };
	},
});
const CheckpointNote = defineTool({
	name: "checkpoint_note",
	description: "Write a progress note.",
	parameters: Type.Object({ text: Type.String() }),
	replay: "safe",
	execute: async (args) => {
		appendFileSync(join(pdDir, "notes.log"), `${args.text}\n`);
		return { output: [{ type: "text", text: "noted" }] };
	},
});
const Factory = defineExtension({
	name: "factory",
	tools: [SideEffect, CheckpointNote],
	wraps: [
		wrapTool(createBashTool(), (bash) => ({
			...bash,
			execute: async (args, api, context) => {
				appendFileSync(join(pdDir, "bash-runs"), `${process.pid}\t${String((args as { command: string }).command).replace(/\s+/g, " ")}\n`);
				const running = bash.execute(args, api, context);
				if (shouldPause("bash")) {
					const [result] = await Promise.all([running, sleep(30000)]);
					return result;
				}
				return running;
			},
		})),
	],
});
const Coding = defineExtension({
	name: "coding",
	sections: [
		section(
			"preamble",
			() =>
				"You are a coding agent working in a git repository. Use the tools to read, edit and test. Do exactly what the task asks, keep edits minimal, write no code comments, and finish with a short summary.",
			{ tag: false },
		),
		section("cwd", (input) => input.env?.cwd),
	],
});
const registry = createRegistry();
registry.install(CodingTools);
registry.install(Coding);
registry.install(Factory);

const models = createModels();
const lbUrl = (process.env.AGENT_LB_URL ?? "http://127.0.0.1:2455").replace(/\/$/, "");
const seatHeader = process.env.AGENT_LB_SEAT ?? "pi-durable-seat";
const lbAuth = {
	apiKey: {
		name: "agent-lb",
		resolve: async () => ({ auth: { apiKey: process.env.AGENT_LB_API_KEY ?? "sk-local" }, source: "agent-lb" }),
	},
};
const zeroCost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
function lbModel(provider: string, api: string, baseUrl: string, id: string) {
	return { id, name: id, api, provider, baseUrl, headers: { "x-agent-lb-seat": seatHeader }, reasoning: true, input: ["text"], cost: zeroCost, contextWindow: 272000, maxTokens: 64000 } as never;
}
let provider = opt.provider!;
let modelId = opt.model ?? "gpt-6.1-sol";
if (provider === "faux") {
	const faux = fauxProvider();
	models.setProvider(faux.provider);
	modelId = "faux-1";
	const script: Array<{ tool?: string; args?: Record<string, unknown>; text?: string }> = JSON.parse(readFileSync(opt["faux-script"]!, "utf8"));
	const step = async (context: { messages: readonly { role: string }[] }) => {
		const index = context.messages.filter((m) => m.role === "toolResult").length;
		if (shouldPause("gen")) await sleep(30000);
		const item = script[Math.min(index, script.length - 1)]!;
		appendFileSync(join(pdDir, "gen-steps"), `${process.pid}\t${index}\n`);
		if (item.tool) return fauxAssistantMessage([fauxToolCall(item.tool, item.args as never, { id: `s${index}` })], { stopReason: "toolUse" });
		return fauxAssistantMessage([fauxText(item.text ?? "Done.")]);
	};
	faux.setResponses(Array.from({ length: 200 }, () => step as never));
} else if (provider === "lb-responses") {
	models.setProvider(
		createProvider({ id: provider, baseUrl: `${lbUrl}/v1`, headers: { "x-agent-lb-seat": seatHeader }, auth: lbAuth, models: [lbModel(provider, "openai-responses", `${lbUrl}/v1`, modelId)], api: openAIResponsesApi() }) as never,
	);
} else if (provider === "lb-codex") {
	models.setProvider(
		createProvider({ id: provider, baseUrl: `${lbUrl}/backend-api`, headers: { "x-agent-lb-seat": seatHeader }, auth: lbAuth, models: [lbModel(provider, "openai-codex-responses", `${lbUrl}/backend-api`, modelId)], api: openAICodexResponsesApi() }) as never,
	);
} else if (provider === "lb-anthropic") {
	models.setProvider(
		createProvider({ id: provider, baseUrl: lbUrl, headers: { "x-agent-lb-seat": seatHeader }, auth: lbAuth, models: [lbModel(provider, "anthropic-messages", lbUrl, modelId)], api: anthropicMessagesApi() }) as never,
	);
} else {
	log(`unknown provider ${provider}`);
	releaseLock();
	process.exit(64);
}

const ctx = BACKGROUND_CONTEXT;
let exitCode = 0;
try {
	const harness = await Harness.open(
		await openNodeSqliteStorage(db),
		{
			models,
			registry,
			settings: { retry: { maxRetries: Number(opt["max-retries"]) }, toolExecution: "sequential" },
			env: (target: { cwd?: string }) => new NodeExecutionEnv({ cwd: target.cwd ?? cwd }),
		},
		ctx,
	);
	const root = await harness.root(ctx, { agent: { model: { provider, modelId }, cwd, thinkingLevel: opt.effort as never } });
	const stream = await watchEvents(harness, root.id, ctx);
	if (opt.events) {
		appendFileSync(opt.events, `${JSON.stringify({ type: "pd_open", pid: process.pid, host, at: new Date().toISOString() })}\n`);
		stream.start(async (events) => {
			for (const event of events) appendFileSync(opt.events!, `${JSON.stringify(event)}\n`);
		});
	}
	harness.resume();
	const promptFile = opt["prompt-file"];
	const content = promptFile === "-" ? readFileSync(0, "utf8") : promptFile ? readFileSync(promptFile, "utf8") : "Reply OK.";
	const sub = await root.submit({ type: "input", content, requestId }, ctx);
	log(`submission ${sub.id} request ${requestId}`);
	const settled = await sub.wait(ctx);
	await harness.waitForIdle(ctx);
	await sleep(0);
	if (settled.status === "done" && settled.type === "input") {
		const entry = await root.commit((tx) => tx.entry(AssistantEntry, settled.answer), ctx);
		const message = entry?.model?.[0];
		const text = message?.role === "assistant" ? message.content.flatMap((c) => (c.type === "text" ? [c.text] : [])).join("") : "";
		if (opt.last) writeFileSync(opt.last, text);
		process.stdout.write(`${text}\n`);
	} else {
		log(`unanswered: ${JSON.stringify(settled)}`);
		exitCode = 2;
	}
	await stream.stop();
	await harness.close(ctx);
} catch (error) {
	log(`error: ${(error as Error).stack ?? error}`);
	exitCode = error instanceof SessionFailed ? 70 : 1;
}
const artifacts = process.env.SEAT_ARTIFACT_DIR;
if (artifacts && existsSync(db)) {
	try {
		mkdirSync(artifacts, { recursive: true });
		writeFileSync(join(artifacts, "pd.sqlite"), readFileSync(db));
	} catch {}
}
releaseLock();
process.exit(exitCode);
