import { config as loadEnv } from "dotenv";
import { resolve } from "node:path";

// .env lives at the monorepo root, but workspace scripts run with CWD=apps/api,
// so load it relative to this module instead of the process CWD.
//
// This must run before any module that reads process.env at import time
// (e.g. lib/redis, lib/prisma). Keep it imported as the FIRST import in the
// entrypoint so its side effect executes before those modules are evaluated.
loadEnv({ path: resolve(__dirname, "../../../../.env") });
