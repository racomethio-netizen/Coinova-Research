/**
 * Minimal structured JSON logger with secret redaction.
 *
 * Every log line is a single JSON object on stdout/stderr so hosting
 * platforms (e.g. Render) can index it. Values under keys that look like
 * credentials are replaced with "[REDACTED]" before serialization.
 */

type Level = "debug" | "info" | "warn" | "error";
const LEVELS: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

const SENSITIVE_KEY = /pass(word)?|secret|token|api[-_]?key|authorization|cookie|service[-_]?role|signature|prompt/i;
const MAX_DEPTH = 6;

export function redact(value: unknown, depth = 0): unknown {
  if (depth > MAX_DEPTH) return "[Truncated]";
  if (value instanceof Error) {
    return { name: value.name, message: value.message, stack: process.env.APP_ENV === "production" ? undefined : value.stack };
  }
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = SENSITIVE_KEY.test(k) ? "[REDACTED]" : redact(v, depth + 1);
    }
    return out;
  }
  return value;
}

function currentLevel(): Level {
  const l = process.env.LOG_LEVEL as Level | undefined;
  return l && l in LEVELS ? l : "info";
}

function emit(level: Level, msg: string, fields?: Record<string, unknown>) {
  if (LEVELS[level] < LEVELS[currentLevel()]) return;
  const line = JSON.stringify({ ts: new Date().toISOString(), level, msg, ...(redact(fields ?? {}) as object) });
  if (level === "error" || level === "warn") console.error(line);
  else console.log(line);
}

export interface Logger {
  debug(msg: string, fields?: Record<string, unknown>): void;
  info(msg: string, fields?: Record<string, unknown>): void;
  warn(msg: string, fields?: Record<string, unknown>): void;
  error(msg: string, fields?: Record<string, unknown>): void;
  child(bindings: Record<string, unknown>): Logger;
}

function make(bindings: Record<string, unknown>): Logger {
  return {
    debug: (m, f) => emit("debug", m, { ...bindings, ...f }),
    info: (m, f) => emit("info", m, { ...bindings, ...f }),
    warn: (m, f) => emit("warn", m, { ...bindings, ...f }),
    error: (m, f) => emit("error", m, { ...bindings, ...f }),
    child: (b) => make({ ...bindings, ...b }),
  };
}

export const logger: Logger = make({});
