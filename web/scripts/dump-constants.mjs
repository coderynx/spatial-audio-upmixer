#!/usr/bin/env node
// Preview/export parity constants — web-side dump for the live cross-check.
//
// See docs/contracts/preview_export_parity.md and
// tests/test_contract_parity.py (the Python side of this comparison).
// Bundles web/src/lib/contract.ts with esbuild (same pattern as
// render-preview-golden.mjs), calls its canonicalConstants(), and writes the
// result to tests/fixtures/contract/web_constants.json. That fixture is
// committed; test_contract_parity.py loads it and diffs it directly against
// upmixer.contract.canonical_constants() — no hash, nothing to regenerate
// but this JSON file, and re-running this script after a deliberate
// both-sides constant change is exactly the "regenerate" step.
//
// Run: `node web/scripts/dump-constants.mjs` (or `npm run constants:dump`
// from `web/`).
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..");

async function loadBundledModule(entry, tag) {
  const result = await esbuild.build({
    entryPoints: [entry],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "node22",
    // Mirrors vite.config.ts's "@" -> src alias — contract.ts imports
    // masteringProfiles.ts via "@/features/...", same as the rest of src/.
    alias: { "@": path.join(webRoot, "src") },
  });
  const code = result.outputFiles[0].text;
  const tmpFile = path.join(webRoot, "scripts", `.${tag}.bundle.${process.pid}.mjs`);
  fs.writeFileSync(tmpFile, code);
  try {
    return await import(`file://${tmpFile}`);
  } finally {
    fs.unlinkSync(tmpFile);
  }
}

async function main() {
  const { canonicalConstants } = await loadBundledModule(
    path.join(webRoot, "src/lib/contract.ts"),
    "contract",
  );

  const outDir = path.join(repoRoot, "tests/fixtures/contract");
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, "web_constants.json");
  fs.writeFileSync(outPath, JSON.stringify(canonicalConstants(), null, 2) + "\n");
  console.log(`Wrote ${outPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
