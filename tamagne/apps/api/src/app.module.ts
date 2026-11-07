import { Module, type DynamicModule } from "@nestjs/common";
import { ConfigModule } from "./config/config.module.js";
import type { AppConfig } from "./config/env.js";
import { PrismaService } from "./database/prisma.service.js";
import { FaydaStatusController } from "./fayda/fayda-status.controller.js";
import { HealthController } from "./health/health.controller.js";

@Module({})
export class AppModule {
  static forRoot(config: AppConfig): DynamicModule {
    return {
      module: AppModule,
      imports: [ConfigModule.forRoot(config)],
      controllers: [HealthController, FaydaStatusController],
      providers: [PrismaService],
    };
  }
}
