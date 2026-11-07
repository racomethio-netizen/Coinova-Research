import { Module, type DynamicModule, type Type } from "@nestjs/common";
import { AdminController } from "./admin/admin.controller.js";
import { AuditService } from "./audit/audit.service.js";
import { AuthController } from "./auth/auth.controller.js";
import { AuthService } from "./auth/auth.service.js";
import { AuthGuard, StaffGuard } from "./auth/guards.js";
import { MfaController } from "./auth/mfa.controller.js";
import { SessionService } from "./auth/session.service.js";
import { ConfigModule } from "./config/config.module.js";
import type { AppConfig } from "./config/env.js";
import { ConsentsController } from "./consents/consents.controller.js";
import { PrismaService } from "./database/prisma.service.js";
import { FaydaStatusController } from "./fayda/fayda-status.controller.js";
import { HealthController } from "./health/health.controller.js";
import { SellersController } from "./sellers/sellers.controller.js";
import { SellersService } from "./sellers/sellers.service.js";
import { CryptoService } from "./security/crypto.service.js";
import { RATE_LIMIT_STORE, RateLimiter, createRateLimitStore, type RateLimitStore } from "./security/rate-limit.js";
import { SMS_SENDER, createSmsSender, type SmsSender } from "./sms/sms.js";
import { TestOutboxController } from "./sms/test-outbox.controller.js";

export interface AppOverrides {
  rateLimitStore?: RateLimitStore;
  smsSender?: SmsSender;
}

@Module({})
export class AppModule {
  static forRoot(config: AppConfig, overrides: AppOverrides = {}): DynamicModule {
    const controllers: Type[] = [HealthController, FaydaStatusController, AuthController, MfaController, SellersController, ConsentsController, AdminController];
    if (config.smsProvider === "test" && config.appEnv === "test") controllers.push(TestOutboxController);
    return {
      module: AppModule,
      imports: [ConfigModule.forRoot(config)],
      controllers,
      providers: [
        PrismaService,
        CryptoService,
        AuditService,
        SessionService,
        AuthService,
        SellersService,
        AuthGuard,
        StaffGuard,
        RateLimiter,
        { provide: RATE_LIMIT_STORE, useFactory: () => overrides.rateLimitStore ?? createRateLimitStore(config) },
        { provide: SMS_SENDER, useFactory: () => overrides.smsSender ?? createSmsSender(config) },
      ],
    };
  }
}
