import { Module, type DynamicModule, type Type } from "@nestjs/common";
import { AdminController } from "./admin/admin.controller.js";
import { OperationsService } from "./admin/operations.service.js";
import { SellersAdminService } from "./admin/sellers-admin.service.js";
import { AdminAppealsController, AppealsController } from "./appeals/appeals.controller.js";
import { AppealsService } from "./appeals/appeals.service.js";
import { NotificationsController, PublicPlansController } from "./notifications/notifications.controller.js";
import { AdminReportsController, ReportsController } from "./reports/reports.controller.js";
import { ReportsService } from "./reports/reports.service.js";
import { AdminPrivacyController, PrivacyController } from "./privacy/privacy.controller.js";
import { MetricsController, MetricsService } from "./observability/metrics.js";
import { RekeyService } from "./security/rekey.service.js";
import { PrivacyService } from "./privacy/privacy.service.js";
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
import { FaydaOidcClient } from "./fayda/fayda-oidc.js";
import { FAYDA_CLIENT, FaydaService } from "./fayda/fayda.service.js";
import { HealthController } from "./health/health.controller.js";
import { SellersController } from "./sellers/sellers.controller.js";
import { SellersService } from "./sellers/sellers.service.js";
import { CryptoService } from "./security/crypto.service.js";
import { RATE_LIMIT_STORE, RateLimiter, createRateLimitStore, type RateLimitStore } from "./security/rate-limit.js";
import { SMS_SENDER, createSmsSender, type SmsSender } from "./sms/sms.js";
import { TestOutboxController } from "./sms/test-outbox.controller.js";
import { AdminQrController, SellerQrController } from "./qr/qr.controller.js";
import { QrService } from "./qr/qr.service.js";
import { VerifyController } from "./verify/verify.controller.js";
import { VerifyService } from "./verify/verify.service.js";
import { BusinessesController } from "./businesses/businesses.controller.js";
import { BusinessesService } from "./businesses/businesses.service.js";
import { ReviewController } from "./review/review.controller.js";
import { ReviewService } from "./review/review.service.js";
import { AdminIdentityReviewController, IdentityReviewController } from "./identity/identity-review.controller.js";
import { IdentityReviewService } from "./identity/identity-review.service.js";
import { ClamAvScanner, DisabledScanner, EicarOnlyScanner, MALWARE_SCANNER, type MalwareScanner } from "./scanning/scanner.js";
import { DisabledObjectStore, EncryptedObjectStore, LocalObjectStore, OBJECT_STORE, type ObjectStore } from "./storage/storage.js";

export interface AppOverrides {
  rateLimitStore?: RateLimitStore;
  smsSender?: SmsSender;
  objectStore?: ObjectStore;
  scanner?: MalwareScanner;
}

function createObjectStore(config: AppConfig): ObjectStore {
  return config.storage.driver === "local" ? new LocalObjectStore(config.storage.localDir) : new DisabledObjectStore();
}

function createScanner(config: AppConfig): MalwareScanner {
  if (config.scanner.kind === "clamav") return new ClamAvScanner(config.scanner.host, config.scanner.port);
  if (config.scanner.kind === "eicar-only") return new EicarOnlyScanner();
  return new DisabledScanner();
}

@Module({})
export class AppModule {
  static forRoot(config: AppConfig, overrides: AppOverrides = {}): DynamicModule {
    const controllers: Type[] = [HealthController, FaydaStatusController, AuthController, MfaController, SellersController, ConsentsController, AdminController, VerifyController, SellerQrController, AdminQrController, BusinessesController, ReviewController, IdentityReviewController, AdminIdentityReviewController, ReportsController, AdminReportsController, AppealsController, AdminAppealsController, NotificationsController, PublicPlansController, PrivacyController, AdminPrivacyController, MetricsController];
    if (config.smsProvider === "test" && config.appEnv === "test") controllers.push(TestOutboxController);
    return {
      module: AppModule,
      imports: [ConfigModule.forRoot(config)],
      controllers,
      providers: [
        PrismaService,
        MetricsService,
        RekeyService,
        CryptoService,
        AuditService,
        SessionService,
        AuthService,
        SellersService,
        VerifyService,
        QrService,
        BusinessesService,
        FaydaService,
        { provide: FAYDA_CLIENT, useFactory: () => (config.fayda.enabled ? new FaydaOidcClient(config.fayda) : null) },
        ReviewService,
        IdentityReviewService,
        SellersAdminService,
        OperationsService,
        ReportsService,
        AppealsService,
        PrivacyService,
        AuthGuard,
        StaffGuard,
        RateLimiter,
        { provide: RATE_LIMIT_STORE, useFactory: () => overrides.rateLimitStore ?? createRateLimitStore(config) },
        { provide: SMS_SENDER, useFactory: () => overrides.smsSender ?? createSmsSender(config) },
        {
          provide: OBJECT_STORE,
          inject: [CryptoService],
          useFactory: (crypto: CryptoService) => new EncryptedObjectStore(overrides.objectStore ?? createObjectStore(config), crypto.cipher),
        },
        { provide: MALWARE_SCANNER, useFactory: () => overrides.scanner ?? createScanner(config) },
      ],
    };
  }
}
