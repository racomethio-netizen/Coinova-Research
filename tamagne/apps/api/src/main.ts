import { Logger } from "@nestjs/common";
import { createApp } from "./app.factory.js";
import { ConfigError, loadConfig } from "./config/env.js";

async function main(): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (e) {
    if (e instanceof ConfigError) {
      // Lists variable names and problems only, never values.
      console.error(e.message);
      process.exit(1);
    }
    throw e;
  }
  const app = await createApp(config);
  await app.listen(config.port, "0.0.0.0");
  new Logger("bootstrap").log(
    `TAMAGNE API listening on :${config.port} (env=${config.appEnv}, fayda=${config.fayda.mode}, openapi=${config.openApiEnabled})`,
  );
}

void main();
