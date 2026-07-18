// Builds the PyInstaller backend exe (reusing the existing installer/spec.spec,
// untouched) and copies the resulting onedir build into electron/resources/backend
// so electron-builder can bundle it as an extraResource for the production app.
//
// This does NOT create a new/separate PyInstaller spec: the installer/ pipeline
// (launcher.py, spec.spec, installer.iss) stays exactly as-is and is still used
// on its own for the lightweight non-Electron installer via the existing CI.
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

async function main() {
  if (!isWin) {
    console.warn(
      "[build-backend] Not running on Windows — skipping PyInstaller build.\n" +
        "  The Windows .exe must be produced on a Windows machine/runner (PyInstaller\n" +
        "  cannot cross-compile a Windows exe from Linux). If resources/backend already\n" +
        "  contains a previously built exe, electron-builder will still bundle that."
    );
    try {
      await access(RESOURCES_BACKEND);
      console.log("[build-backend] Found existing resources/backend — reusing it.");
      return;
    } catch {
      console.warn(
        "[build-backend] No resources/backend found either. `npm run dist:win` will\n" +
          "  still run electron-builder, but the produced installer will NOT contain a\n" +
          "  working backend. Run this script on Windows first."
      );
      return;
    }
  }

  const pythonExe = path.join(ROOT, "backend", ".venv", "Scripts", "python.exe");
  const python = (await pathExists(pythonExe)) ? pythonExe : "python";

  console.log("[build-backend] Running PyInstaller via", python);
  execFileSync(
    python,
    [
      "-m",
      "PyInstaller",
      path.join("installer", "spec.spec"),
      "--distpath",
      "dist_win",
      "--workpath",
      "build_win",
      "--noconfirm",
      "--log-level",
      "WARN",
    ],
    { cwd: ROOT, stdio: "inherit" }
  );

  console.log("[build-backend] Copying", DIST_WIN, "->", RESOURCES_BACKEND);
  await rm(RESOURCES_BACKEND, { recursive: true, force: true });
  await mkdir(path.dirname(RESOURCES_BACKEND), { recursive: true });
  await cp(DIST_WIN, RESOURCES_BACKEND, { recursive: true });

  console.log("[build-backend] Done.");
}

async function pathExists(p) {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
