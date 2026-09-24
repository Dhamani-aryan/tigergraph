#!/usr/bin/env node
// Pi bridge for the Python fraud agent (LLM_BACKEND=pi).
//
//   node bridge.mjs serve    persistent JSONL server: one JSON request per stdin line,
//                            one JSON response per stdout line (protocol ONLY on stdout)
//   node bridge.mjs models   list openai-codex models and which are authenticated
//   node bridge.mjs login    Pi's supported OpenAI Codex (ChatGPT Plus/Pro) OAuth login
//
// Auth never lives in this repo: ModelRuntime (pi-coding-agent's public SDK export)
// is a pi-ai `Models` collection backed by Pi's own credential store,
// <PI agent dir>/auth.json (default ~/.pi/agent/auth.json), with locked refresh.
// Requests go through pi-ai's openai-codex provider. The bridge never executes
// tools: tool definitions are only declared to the model, and any tool call is
// returned to Python, which checks it against its own allowlist.

import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";

// Keep stdout reserved for protocol frames: anything a library logs goes to stderr.
const protocolWrite = process.stdout.write.bind(process.stdout);
for (const level of ["log", "info", "debug", "warn"]) {
	console[level] = (...args) => console.error(...args);
}
process.stdout.write = (chunk, ...rest) => process.stderr.write(chunk, ...rest);

// pi-ai's package.json is not an exported subpath; read the installed version from the lockfile.
const PI_AI_VERSION = JSON.parse(readFileSync(new URL("./package-lock.json", import.meta.url), "utf-8"))
	.packages["node_modules/@earendil-works/pi-ai"]?.version ?? "unknown";
// Loaded in main() (after the stdout redirect above) rather than by a static import,
// which would evaluate the library before that redirect is in place.
let ModelRuntime;
let PI_CODING_AGENT_VERSION;

const PROVIDER = process.env.PI_PROVIDER || "openai-codex";
const REQUESTED_MODEL = process.env.PI_MODEL || "";
const REASONING_EFFORT = process.env.PI_REASONING_EFFORT || "low";
const DEFAULT_TIMEOUT_MS = Number(process.env.PI_REQUEST_TIMEOUT_MS || 180000);

function log(message) {
	process.stderr.write(`[pi-bridge] ${message}\n`);
}

function send(frame) {
	protocolWrite(`${JSON.stringify(frame)}\n`);
}

// Resolves once everything written to stdout so far has been flushed.
function flushStdout() {
	return new Promise((resolve) => protocolWrite("", () => resolve()));
}

class BridgeError extends Error {
	constructor(kind, message, extra = {}) {
		super(message);
		this.kind = kind;
		this.extra = extra;
	}
}

function classifyProviderError(message) {
	const text = String(message || "");
	const retryMinutes = /Try again in ~(\d+) min/i.exec(text);
	if (/usage limit|rate.?limit|too many requests|\b429\b|quota/i.test(text)) {
		return new BridgeError("rate_limit", text, {
			retry_after_s: retryMinutes ? Number(retryMinutes[1]) * 60 : null,
		});
	}
	if (/oauth|unauthori[sz]ed|\b401\b|\b403\b|login|credential|token.*(expired|invalid|refresh)/i.test(text)) {
		return new BridgeError("auth", text);
	}
	if (/timed? ?out|timeout|ETIMEDOUT/i.test(text)) {
		return new BridgeError("timeout", text);
	}
	if (/model.*(not (found|supported|available))|unknown model|unsupported model/i.test(text)) {
		return new BridgeError("model", text);
	}
	return new BridgeError("provider", text);
}

async function createRuntime() {
	return ModelRuntime.create({});
}

async function resolveModel(runtime) {
	if (!runtime.getProvider(PROVIDER)) {
		throw new BridgeError("provider", `Pi provider '${PROVIDER}' is not registered`);
	}
	let auth;
	try {
		auth = await runtime.checkAuth(PROVIDER);
	} catch (error) {
		throw new BridgeError("auth", `Pi auth check failed for '${PROVIDER}': ${error?.message ?? error}`);
	}
	if (!auth) {
		throw new BridgeError(
			"auth",
			`No Pi credential for '${PROVIDER}'. Run \`node pi_bridge/bridge.mjs login\` (OpenAI Codex OAuth).`,
		);
	}
	if (!REQUESTED_MODEL) {
		throw new BridgeError("model", "PI_MODEL is not set; pick one from `node pi_bridge/bridge.mjs models`");
	}
	const available = await runtime.getAvailable(PROVIDER);
	const model = available.find((m) => m.id === REQUESTED_MODEL);
	if (!model) {
		throw new BridgeError(
			"model",
			`PI_MODEL '${REQUESTED_MODEL}' is not in Pi's authenticated '${PROVIDER}' catalog ` +
				`(available: ${available.map((m) => m.id).join(", ") || "none"})`,
		);
	}
	return model;
}

function toPiTools(tools) {
	if (!Array.isArray(tools)) return undefined;
	return tools.map((tool) => {
		if (!tool || typeof tool.name !== "string" || !tool.name) {
			throw new BridgeError("malformed", "tool definition is missing a name");
		}
		return {
			name: tool.name,
			description: String(tool.description ?? ""),
			// JSON Schema object; pi-ai accepts plain JSON schemas (TypeBox schemas are JSON).
			parameters: tool.parameters ?? { type: "object", properties: {} },
		};
	});
}

function toPiMessages(model, messages) {
	if (!Array.isArray(messages) || messages.length === 0) {
		throw new BridgeError("malformed", "request.messages must be a non-empty array");
	}
	const now = Date.now();
	return messages.map((message, index) => {
		const content = String(message?.content ?? "");
		if (message?.role === "user") {
			return { role: "user", content, timestamp: now + index };
		}
		if (message?.role === "assistant") {
			// A prior attempt echoed back for a corrective retry. Rebuilt fresh from the
			// request every time -- the bridge keeps no conversation state between calls.
			return {
				role: "assistant",
				content: [{ type: "text", text: content }],
				api: model.api,
				provider: model.provider,
				model: model.id,
				usage: {
					input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
					cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
				},
				stopReason: "stop",
				timestamp: now + index,
			};
		}
		throw new BridgeError("malformed", `unsupported message role '${message?.role}' at index ${index}`);
	});
}

function usageOf(message) {
	const u = message?.usage ?? {};
	const input = Number(u.input || 0);
	const output = Number(u.output || 0);
	const cacheRead = Number(u.cacheRead || 0);
	const cacheWrite = Number(u.cacheWrite || 0);
	// pi-ai reports `input` net of cached/cache-write tokens; the truthful billed
	// prompt size is input + cacheRead + cacheWrite, plus output (reasoning included).
	return {
		input,
		output,
		cache_read: cacheRead,
		cache_write: cacheWrite,
		reasoning: Number(u.reasoning || 0),
		total: input + cacheRead + cacheWrite + output,
		provider_total: Number(u.totalTokens || 0),
	};
}

async function complete(runtime, model, request) {
	const timeoutMs = Number(request.timeout_ms || DEFAULT_TIMEOUT_MS);
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	const context = {
		systemPrompt: String(request.system ?? ""),
		messages: toPiMessages(model, request.messages),
	};
	const tools = toPiTools(request.tools);
	if (tools && tools.length > 0) context.tools = tools;
	let message;
	try {
		message = await runtime.complete(model, context, {
			signal: controller.signal,
			timeoutMs,
			transport: "sse",
			// No sessionId + no cache retention: nothing carries over between calls/cases.
			cacheRetention: "none",
			reasoningEffort: REASONING_EFFORT,
			toolChoice: tools && tools.length > 0 ? (request.tool_choice || "auto") : undefined,
		});
	} catch (error) {
		if (controller.signal.aborted) throw new BridgeError("timeout", `Pi request timed out after ${timeoutMs} ms`);
		throw classifyProviderError(error?.message ?? error);
	} finally {
		clearTimeout(timer);
	}
	if (message.stopReason === "aborted" || controller.signal.aborted) {
		throw new BridgeError("timeout", `Pi request timed out after ${timeoutMs} ms`);
	}
	if (message.stopReason === "error") {
		throw classifyProviderError(message.errorMessage || "Pi provider returned an error");
	}
	const text = message.content
		.filter((block) => block.type === "text")
		.map((block) => block.text)
		.join("");
	const toolCalls = message.content
		.filter((block) => block.type === "toolCall")
		.map((block) => ({ name: block.name, arguments: block.arguments ?? {} }));
	return {
		text,
		tool_calls: toolCalls,
		stop_reason: message.stopReason,
		usage: usageOf(message),
		provider: message.provider,
		model: message.model,
		response_model: message.responseModel ?? null,
	};
}

async function serve() {
	let runtime;
	let model;
	try {
		runtime = await createRuntime();
		model = await resolveModel(runtime);
	} catch (error) {
		const kind = error instanceof BridgeError ? error.kind : "startup";
		send({ event: "startup_error", error: { kind, message: String(error?.message ?? error) } });
		process.exitCode = 2;
		return;
	}
	send({
		event: "ready",
		provider: model.provider,
		model: model.id,
		api: model.api,
		reasoning_effort: REASONING_EFFORT,
		pi_ai_version: PI_AI_VERSION,
		pi_coding_agent_version: PI_CODING_AGENT_VERSION,
		node_version: process.version,
	});
	log(`ready provider=${model.provider} model=${model.id}`);

	const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
	const closed = new Promise((resolve) => rl.once("close", resolve));
	// Requests are handled strictly one at a time, in order.
	let chain = Promise.resolve();
	rl.on("line", (line) => {
		chain = chain.then(() => handleLine(runtime, model, line, rl));
	});
	// Ends on stdin EOF or on a `shutdown` request (which closes `rl` itself).
	await closed;
	await chain;
	log("shutting down");
}

let shuttingDown = false;

async function handleLine(runtime, model, line, rl) {
	if (shuttingDown || !line.trim()) return;
	let request;
	try {
		request = JSON.parse(line);
	} catch {
		send({ id: null, ok: false, error: { kind: "malformed", message: "request line is not valid JSON" } });
		return;
	}
	const id = request?.id ?? null;
	try {
		if (request.op === "ping") {
			send({ id, ok: true, result: { provider: model.provider, model: model.id } });
		} else if (request.op === "complete") {
			send({ id, ok: true, result: await complete(runtime, model, request) });
		} else if (request.op === "shutdown") {
			shuttingDown = true;
			send({ id, ok: true, result: { shutdown: true } });
			// Close the readline interface, not just stdin: destroying stdin never emits
			// readline's "close", which left serve() pending and made Node exit 13 with
			// "Detected unsettled top-level await".
			rl.close();
		} else {
			throw new BridgeError("malformed", `unknown op '${request.op}'`);
		}
	} catch (error) {
		const kind = error instanceof BridgeError ? error.kind : "provider";
		const extra = error instanceof BridgeError ? error.extra : {};
		log(`request ${id} failed: ${kind}`);
		send({ id, ok: false, error: { kind, message: String(error?.message ?? error), ...extra } });
	}
}

async function listModels() {
	const runtime = await createRuntime();
	const all = runtime.getModels(PROVIDER);
	let available = [];
	let authError = null;
	try {
		available = await runtime.getAvailable(PROVIDER);
	} catch (error) {
		authError = String(error?.message ?? error);
	}
	const availableIds = new Set(available.map((m) => m.id));
	send({
		provider: PROVIDER,
		authenticated: availableIds.size > 0,
		auth_error: authError,
		pi_ai_version: PI_AI_VERSION,
		models: all.map((m) => ({
			id: m.id,
			name: m.name,
			available: availableIds.has(m.id),
			reasoning: m.reasoning,
			context_window: m.contextWindow,
		})),
	});
}

async function login() {
	const runtime = await createRuntime();
	const loginAbort = new AbortController();
	await runtime.login(PROVIDER, "oauth", {
		signal: loginAbort.signal,
		prompt: async (p) => {
			if (p.type === "select") {
				// Browser login: Pi's own local callback server receives the code.
				const browser = p.options.find((o) => /browser/i.test(o.label)) ?? p.options[0];
				log(`login method: ${browser.label}`);
				return browser.id;
			}
			if (p.type === "manual_code") {
				// Wait for Pi's callback server instead of reading a pasted code; Pi aborts
				// this prompt through p.signal once the browser redirect arrives.
				return new Promise((_resolve, reject) => {
					p.signal?.addEventListener("abort", () => reject(new Error("manual entry not used")), { once: true });
				});
			}
			throw new Error(`Unsupported interactive login prompt: ${p.type}`);
		},
		notify: (event) => {
			if (event.type === "auth_url") {
				log("Open this URL in your browser and sign in with your ChatGPT account:");
				process.stderr.write(`${event.url}\n`);
			} else if (event.type === "device_code") {
				log(`Open ${event.verificationUri} and enter code ${event.userCode}`);
			} else if (event.message) {
				log(event.message);
			}
		},
	});
	log(`login complete; credential stored in Pi's auth store for '${PROVIDER}'`);
}

async function main(command) {
	({ ModelRuntime, VERSION: PI_CODING_AGENT_VERSION } = await import("@earendil-works/pi-coding-agent"));
	if (command === "serve") await serve();
	else if (command === "models") await listModels();
	else if (command === "login") await login();
	else {
		log(`unknown command '${command}' (expected serve | models | login)`);
		process.exitCode = 64;
	}
}

// No top-level await. Once main() settles, flush stdout and exit explicitly, so an
// idle provider socket can't keep the process alive after shutdown.
main(process.argv[2] || "serve")
	.catch((error) => {
		log(`fatal: ${error?.message ?? error}`);
		process.exitCode = 1;
	})
	.then(flushStdout)
	.then(() => process.exit(process.exitCode ?? 0));
