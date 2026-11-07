import { Controller, Get, HttpStatus, Inject, Query } from "@nestjs/common";
import { ApiExcludeController } from "@nestjs/swagger";
import { ApiError } from "../common/http.js";
import { SMS_SENDER, TestOutboxSmsSender, type SmsSender } from "./sms.js";

/**
 * Only registered when SMS_PROVIDER=test, which config allows only with APP_ENV=test.
 * Lets end-to-end tests read OTP codes without a real SMS gateway.
 */
@ApiExcludeController()
@Controller({ path: "test", version: "1" })
export class TestOutboxController {
  constructor(@Inject(SMS_SENDER) private readonly sms: SmsSender) {}

  @Get("sms-outbox/latest-code")
  latest(@Query("phone") phone: string) {
    if (!(this.sms instanceof TestOutboxSmsSender)) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
    const code = this.sms.lastCodeFor(String(phone));
    if (!code) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "No message.");
    return { data: { code } };
  }
}
