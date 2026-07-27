// Builds the PyInstaller backend exe (reusing installer/spec.spec) and copies
// the result into electron/resources/backend for electron-builder to bundle.
import { execFileSync } from "node:child_process";
import { cp, rm, mkdir, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const ELECTRON_DIR = path.resolve(__dirname, "..");
const DIST_WIN = path.join(ROOT, "dist_win", "SonioxLiveTranslate");
const RESOURCES_BACKEND = path.join(ELECTRON_DIR, "resources", "backend");

const isWin = process.platform === "win32";

async function pathExists(p) {
  try { await access(p); return true; } catch { return false; }
}

async function main() {
  if (!isWin) {
    console.warn(
      "[build-backend] Not on Windows — skipping PyInstaller build.\n" +
      "  PyInstaller cannot cross-compile a Windows exe from other platforms.\n" +
      "  If resources/backend already contains a previously built exe, electron-builder\n" +
      "  will still bundle it."
    );
    if (await pathExists(RESOURCES_BACKEND)) {
      console.log("[build-backend] Found existing resources/backend — reusing it.");
    } else {
      console.warn("[build-backend] No resources/backend found. The produced installer will lack a working backend.");
    }
    return;
  }

  const venvPython = path.join(ROOT, "backend", ".venv", "Scripts", "python.exe");
  const python = (await pathExists(venvPython)) ? venvPython : "python";

  console.log("[build-backend] Running PyInstaller via", python);
  execFileSync(
    python,
    [
      "-m", "PyInstaller",
      path.join("installer", "spec.spec"),
      "--distpath", "dist_win",
      "--workpath", "build_win",
      "--noconfirm",
      "--log-level", "WARN",
    ],
    { cwd: ROOT, stdio: "inherit" }
  );

  console.log("[build-backend] Copying", DIST_WIN, "->", RESOURCES_BACKEND);
  await rm(RESOURCES_BACKEND, { recursive: true, force: true });
  await mkdir(path.dirname(RESOURCES_BACKEND), { recursive: true });
  await cp(DIST_WIN, RESOURCES_BACKEND, { recursive: true });

  console.log("[build-backend] Done.");
}

main().catch((err) => { console.error(err); process.exit(1); });
