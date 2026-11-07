import { randomUUID } from "node:crypto";
import { ArgumentsHost, Catch, HttpException, HttpStatus, Logger, type ExceptionFilter } from "@nestjs/common";
import type { NextFunction, Request, Response } from "express";

/**
 * Error envelope used by every endpoint:
 *   { "error": { "code": "not_found", "message": "...", "requestId": "..." } }
 * Internal errors never leak messages, stack traces or database details.
 */
export interface ErrorBody {
  error: { code: string; message: string; requestId: string; details?: unknown };
}

const CODE_BY_STATUS: Record<number, string> = {
  400: "bad_request",
  401: "unauthenticated",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  413: "payload_too_large",
  415: "unsupported_media_type",
  422: "validation_failed",
  429: "rate_limited",
  503: "unavailable",
};

const REQUEST_ID_RE = /^[A-Za-z0-9._-]{8,64}$/;

/** Accepts a well-formed upstream X-Request-Id (from our proxy) or generates one. */
export function requestIdMiddleware(req: Request, res: Response, next: NextFunction): void {
  const incoming = req.header("x-request-id");
  const id = incoming && REQUEST_ID_RE.test(incoming) ? incoming : randomUUID();
  res.locals.requestId = id;
  res.setHeader("X-Request-Id", id);
  next();
}

/**
 * Express error middleware placed right after the JSON parser, so malformed or
 * oversized bodies get the standard envelope without parser internals.
 */
export function bodyParserErrorHandler(err: unknown, _req: Request, res: Response, next: NextFunction): void {
  if (!isBodyParserError(err)) return next(err);
  const status = err.status === 413 ? 413 : err.status === 415 ? 415 : 400;
  const message = status === 413 ? "Request body is too large." : status === 415 ? "Unsupported content encoding." : "Malformed request body.";
  const body: ErrorBody = { error: { code: CODE_BY_STATUS[status]!, message, requestId: String(res.locals.requestId ?? randomUUID()) } };
  res.status(status).json(body);
}

@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger("http");

  catch(exception: unknown, host: ArgumentsHost): void {
    const res = host.switchToHttp().getResponse<Response>();
    const req = host.switchToHttp().getRequest<Request>();
    const requestId = String(res.locals.requestId ?? randomUUID());

    let status = HttpStatus.INTERNAL_SERVER_ERROR;
    let message = "An unexpected error occurred.";
    let details: unknown;
    let code: string | undefined;

    if (exception instanceof HttpException) {
      status = exception.getStatus();
      const body = exception.getResponse();
      if (status < 500) {
        if (typeof body === "string") message = body;
        else if (body && typeof body === "object") {
          const m = (body as { message?: unknown }).message;
          message = typeof m === "string" ? m : Array.isArray(m) ? "Request validation failed." : exception.message;
          details = (body as { details?: unknown }).details;
          const c = (body as { code?: unknown }).code;
          if (typeof c === "string") code = c;
        }
      }
    } else if (isBodyParserError(exception)) {
      status = exception.status;
      message = status === 413 ? "Request body is too large." : "Malformed request body.";
    }

    if (status >= 500) {
      // Log the error type and route only; never request bodies or headers (may hold personal data).
      const name = exception instanceof Error ? exception.name : typeof exception;
      this.logger.error(`${req.method} ${req.route?.path ?? "(unmatched)"} failed: ${name} [${requestId}]`);
    }

    const payload: ErrorBody = {
      error: { code: code ?? CODE_BY_STATUS[status] ?? (status >= 500 ? "internal_error" : "error"), message, requestId },
    };
    const retryAfter = (exception as { retryAfterSeconds?: unknown })?.retryAfterSeconds;
    if (typeof retryAfter === "number") res.setHeader("Retry-After", String(retryAfter));
    if (details !== undefined) payload.error.details = details;
    res.status(status).json(payload);
  }
}

function isBodyParserError(e: unknown): e is { status: number; type: string } {
  return typeof e === "object" && e !== null && typeof (e as { status?: unknown }).status === "number" && typeof (e as { type?: unknown }).type === "string";
}
