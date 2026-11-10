import { DevBillingSimulator, DEV_SIGNATURE_HEADER, signDevPayload } from "@/lib/billing/dev-simulator";
import { describeBillingProviderConformance } from "../support/billing-conformance";

const SECRET = "s".repeat(40);
const sim = new DevBillingSimulator(SECRET, "http://localhost:3000");

describeBillingProviderConformance("dev_simulator", () => ({
  provider: sim,
  signed: (input) => {
    const { body, signature } = sim.buildSignedEvent(input);
    return { body, headers: new Headers({ [DEV_SIGNATURE_HEADER]: signature }) };
  },
  wrongSecret: (body) => new Headers({ [DEV_SIGNATURE_HEADER]: signDevPayload("w".repeat(40), body) }),
  stale: (body) => new Headers({ [DEV_SIGNATURE_HEADER]: signDevPayload(SECRET, body, Math.floor(Date.now() / 1000) - 3600) }),
}));
