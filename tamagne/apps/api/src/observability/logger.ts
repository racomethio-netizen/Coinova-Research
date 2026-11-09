import type { LoggerService, LogLevel } from "@nestjs/common";
import type { NextFunction, Request, Response } from "express";
import { routeTemplate } from "./metrics.js";

/**
 * Defence in depth: messages are written to carry IDs and error names only, but any
 * long digit run (phone numbers, FIN/FAN, OTP-like codes inside text) is masked anyway.
 */
export function redactLogText(text: string): string {
  return text
    .replace(/(?<![\w-])\+?\d(?:[\s-]?\d){8,}(?![\w-])/g, "[redacted-number]")
    .replace(/(?<![\w-])\d{6}(?![\w-])/g, "[redacted-code]");
}

type Write = (line: string) => void;

/** One JSON object per line on stdout (errors on stderr), for log shipping. */
export class JsonLogger implements LoggerService {
  constructor(
    private levels: LogLevel[] = ["log", "warn", "error", "fatal"],
    private readonly out: Write = (l) => process.stdout.write(l),
    private readonly err: Write = (l) => process.stderr.write(l),
  ) {}

  log(message: unknown, ...rest: unknown[]) {
    this.write("log", message, rest);
  }
  warn(message: unknown, ...rest: unknown[]) {
    this.write("warn", message, rest);
  }
  error(message: unknown, ...rest: unknown[]) {
    this.write("error", message, rest);
  }
  fatal(message: unknown, ...rest: unknown[]) {
    this.write("fatal", message, rest);
  }
  debug(message: unknown, ...rest: unknown[]) {
    this.write("debug", message, rest);
  }
  verbose(message: unknown, ...rest: unknown[]) {
    this.write("verbose", message, rest);
  }
  setLogLevels(levels: LogLevel[]) {
    this.levels = levels;
  }

  private write(level: LogLevel, message: unknown, rest: unknown[]) {
    if (!this.levels.includes(level)) return;
    // Nest passes the context as the last string argument.
    const context = typeof rest.at(-1) === "string" ? (rest.pop() as string) : undefined;
    const entry: Record<string, unknown> = { ts: new Date().toISOString(), level: level === "log" ? "info" : level };
    if (context) entry.context = context;
    if (typeof message === "object" && message !== null && !(message instanceof Error)) {
      Object.assign(entry, message);
      if (typeof entry.msg === "string") entry.msg = redactLogText(entry.msg);
    } else {
      entry.msg = redactLogText(message instanceof Error ? message.name : String(message));
    }
    // Stack traces from Nest internals: keep only frames, never error messages (they can echo input).
    const stack = rest.find((r): r is string => typeof r === "string" && r.includes("\n    at "));
    if (stack) entry.stack = stack.split("\n").filter((l) => l.trimStart().startsWith("at ")).slice(0, 10).join("\n");
    const line = `${JSON.stringify(entry)}\n`;
    (level === "error" || level === "fatal" ? this.err : this.out)(line);
  }
}

/**
 * One access-log line per request: method, route template, status, duration, request ID.
 * No query strings, concrete paths, IPs, user agents or bodies.
 */
export function accessLogMiddleware(write: (entry: Record<string, unknown>) => void) {
  return (req: Request, res: Response, next: NextFunction) => {
    const start = process.hrtime.bigint();
    res.on("finish", () => {
      write({
        msg: "request",
        method: req.method,
        route: routeTemplate(req),
        status: res.statusCode,
        durationMs: Math.round(Number(process.hrtime.bigint() - start) / 1e5) / 10,
        requestId: res.locals.requestId,
      });
    });
    next();
  };
}
