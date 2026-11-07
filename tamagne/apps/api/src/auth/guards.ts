import {
  CanActivate,
  ExecutionContext,
  HttpStatus,
  Inject,
  Injectable,
  SetMetadata,
  UseGuards,
  applyDecorators,
  createParamDecorator,
} from "@nestjs/common";
import { Reflector } from "@nestjs/core";
import { ApiCookieAuth, ApiForbiddenResponse, ApiUnauthorizedResponse } from "@nestjs/swagger";
import type { Request } from "express";
import { ApiError } from "../common/http.js";
import { SessionService, type StaffRole, type Viewer } from "./session.service.js";

type RequestWithViewer = Request & { viewer?: Viewer };

/** Requires a valid session. Identity always comes from the server-side session, never from the client. */
@Injectable()
export class AuthGuard implements CanActivate {
  constructor(@Inject(SessionService) private readonly sessions: SessionService) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    const req = ctx.switchToHttp().getRequest<RequestWithViewer>();
    const viewer = await this.sessions.resolve(req);
    if (!viewer) throw new ApiError(HttpStatus.UNAUTHORIZED, "unauthenticated", "Please sign in.");
    req.viewer = viewer;
    return true;
  }
}

const STAFF_ROLES_KEY = "tamagne:staffRoles";

/**
 * Requires one of `roles` (any staff role if empty) AND a session that completed TOTP MFA.
 * Must run after AuthGuard.
 */
@Injectable()
export class StaffGuard implements CanActivate {
  constructor(@Inject(Reflector) private readonly reflector: Reflector) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RequestWithViewer>();
    const viewer = req.viewer;
    if (!viewer) throw new ApiError(HttpStatus.UNAUTHORIZED, "unauthenticated", "Please sign in.");
    const required = this.reflector.getAllAndOverride<StaffRole[]>(STAFF_ROLES_KEY, [ctx.getHandler(), ctx.getClass()]) ?? [];
    const allowed = required.length === 0 ? viewer.isStaff : viewer.roles.some((r) => required.includes(r));
    if (!allowed) throw new ApiError(HttpStatus.FORBIDDEN, "forbidden", "You do not have permission to do this.");
    if (!viewer.mfaVerified) throw new ApiError(HttpStatus.FORBIDDEN, "mfa_required", "Two-factor verification is required.");
    return true;
  }
}

export const Authenticated = () =>
  applyDecorators(UseGuards(AuthGuard), ApiCookieAuth("tamagne_session"), ApiUnauthorizedResponse({ description: "Not signed in" }));

export const StaffOnly = (...roles: StaffRole[]) =>
  applyDecorators(
    SetMetadata(STAFF_ROLES_KEY, roles),
    UseGuards(AuthGuard, StaffGuard),
    ApiCookieAuth("tamagne_session"),
    ApiForbiddenResponse({ description: "Missing staff role or MFA" }),
  );

export const CurrentViewer = createParamDecorator((_: unknown, ctx: ExecutionContext): Viewer => {
  const viewer = ctx.switchToHttp().getRequest<RequestWithViewer>().viewer;
  if (!viewer) throw new ApiError(HttpStatus.UNAUTHORIZED, "unauthenticated", "Please sign in.");
  return viewer;
});
