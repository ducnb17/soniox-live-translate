// One-command Windows build:
// frontend deps+build -> Electron deps+icons -> PyInstaller backend -> NSIS installer.
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const ELECTRON_DIR = path.resolve(__dirname, "..");
const FRONTEND_DIR = path.join(ROOT, "frontend");

// Use npx to run pnpm so the script is self-contained —
// no global pnpm install required on the build machine.
const pnpm = "npx";
const pnpmArgs = ["pnpm@9.12.0"];
const npm = "npm";
const npx = "npx";

function run(command, args, cwd) {
  console.log(`\n[build-all] ${command} ${args.join(" ")}`);
  execFileSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
    shell: true,    // Always use shell so .cmd wrappers resolve on Windows
  });
}

function main() {
  run(pnpm, [...pnpmArgs, "install", "--frozen-lockfile"], FRONTEND_DIR);
  run(pnpm, [...pnpmArgs, "run", "build"], FRONTEND_DIR);
  run(npm, ["install"], ELECTRON_DIR);
  run(npm, ["run", "build:icons"], ELECTRON_DIR);
  run(npm, ["run", "build:backend"], ELECTRON_DIR);
  run(npx, ["electron-builder", "--win"], ELECTRON_DIR);

  console.log("\n[build-all] Installer is ready in electron/dist/.");
}

try {
  main();
} catch (err) {
  console.error("\n[build-all] Build failed:", err.message || err);
  process.exit(1);
}
