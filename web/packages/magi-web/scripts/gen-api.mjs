// Regenerate src/lib/api-types.ts (openapi-typescript) and src/lib/api-schemas.ts
// (zod schemas, openapi-zod-client) from the admin-api OpenAPI schema.
// Cross-platform (npm scripts run under cmd on Windows, which can't expand
// bash-style ${VAR:-default}), so the default lives here in JS.
//
// The source is normally the live admin-api (ADMIN_API_URL + /openapi.json).
// Set OPENAPI_SPEC_PATH to a local openapi.json instead (e.g. dumped offline
// via `app.openapi()` when the Python service isn't running) — both
// generators accept a local file path the same way they accept a URL.

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const url = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
const source = process.env.OPENAPI_SPEC_PATH ?? `${url}/openapi.json`;

execFileSync("npx", ["openapi-typescript", source, "-o", "src/lib/api-types.ts"], {
  stdio: "inherit",
  shell: true,
});

const schemasOutput = "src/lib/api-schemas.ts";
const template = path.join(scriptDir, "schemas-only.hbs");
execFileSync(
  "npx",
  ["openapi-zod-client", source, "-o", schemasOutput, "-t", template, "--export-schemas"],
  { stdio: "inherit", shell: true },
);

// openapi-zod-client targets zod 3's single-argument `z.record(valueType)`;
// zod 4 requires the key schema too. Patch the generated calls rather than
// hand-editing the generated file.
const generated = readFileSync(schemasOutput, "utf8");
writeFileSync(schemasOutput, generated.replaceAll("z.record(", "z.record(z.string(), "));
