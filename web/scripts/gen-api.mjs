// Regenerate src/lib/api-types.ts from the admin-api OpenAPI schema.
// Cross-platform (npm scripts run under cmd on Windows, which can't expand
// bash-style ${VAR:-default}), so the default lives here in JS.

import { execFileSync } from "node:child_process";

const url = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
execFileSync(
  "npx",
  ["openapi-typescript", `${url}/openapi.json`, "-o", "src/lib/api-types.ts"],
  { stdio: "inherit", shell: true },
);
