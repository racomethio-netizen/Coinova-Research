import { Controller, Get } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import { CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";

/** Admin surface. Phase 2 provides the guarded entry point; queues and tools arrive in Phase 6. */
@ApiTags("admin")
@Controller({ path: "admin", version: "1" })
export class AdminController {
  @Get("me")
  @StaffOnly()
  @ApiOperation({ summary: "Staff identity and roles (requires a staff role and MFA)" })
  me(@CurrentViewer() viewer: Viewer) {
    return { data: { userId: viewer.userId, roles: viewer.roles } };
  }

  @Get("audit/verify")
  @StaffOnly("SUPER_ADMIN", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Verify the audit log hash chain" })
  verifyAudit() {
    // Implemented in Phase 6 with the audit viewer; present so role checks are testable now.
    return { data: { available: false } };
  }
}
