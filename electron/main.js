// Electron main process for the Soniox Live Translate desktop shell.
//
// Responsibilities:
//  - Spawn the backend (dev: uvicorn via python; prod: the PyInstaller-built
//    SonioxLiveTranslate.exe) with ELECTRON_HOST=1 so launcher.py suppresses
//    its own webbrowser.open()/pystray tray (see installer/launcher.py).
//  - Poll GET http://127.0.0.1:8765/health until ready (mirrors launcher.py's
//    _wait_ready()), then open a BrowserWindow pointed at that URL.
//  - Configure session.setDisplayMediaRequestHandler so the existing
//    getDisplayMedia() call in frontend/src/app.ts (tab/system audio capture)
//    keeps working unmodified, via Electron's native system picker.
//  - Provide a Tray (Open/Settings/Quit) replacing the pystray tray.
//  - Kill the spawned backend process on quit so nothing is orphaned.
"use strict";

const { app, BrowserWindow, Tray, Menu, session, shell } = require("electron");
const path = require("node:path");
const http = require("node:http");
const { spawn } = require("node:child_process");
const fs = require("node:fs");

const HOST = "127.0.0.1";
const PORT = 8765;
const BASE_URL = `http://${HOST}:${PORT}`;
const HEALTH_URL = `${BASE_URL}/health`;
const IS_DEV = !app.isPackaged;

let mainWindow = null;
let tray = null;
let backendProcess = null;
let quitting = false;

function resolveBackendCommand() {
  if (IS_DEV) {
    // Dev mode: run uvicorn directly against the backend source tree.
    const backendDir = path.resolve(__dirname, "..", "backend");
    const venvPython =
      process.platform === "win32"
        ? path.join(backendDir, ".venv", "Scripts", "python.exe")
        : path.join(backendDir, ".venv", "bin", "python");
    const python = fs.existsSync(venvPython) ? venvPython : "python";
    return {
      command: python,
      args: ["-m", "uvicorn", "app.main:app", "--host", HOST, "--port", String(PORT)],
      cwd: backendDir,
    };
  }

  // Production mode: run the PyInstaller-built exe bundled as an extraResource.
  const exeName = process.platform === "win32" ? "SonioxLiveTranslate.exe" : "SonioxLiveTranslate";
  const exePath = path.join(process.resourcesPath, "backend", exeName);
  return { command: exePath, args: [], cwd: path.dirname(exePath) };
}

function startBackend() {
  const { command, args, cwd } = resolveBackendCommand();
  backendProcess = spawn(command, args, {
    cwd,
    env: { ...process.env, ELECTRON_HOST: "1" },
    windowsHide: true,
  });

  backendProcess.stdout?.on("data", (data) => {
    process.stdout.write(`[backend] ${data}`);
  });
  backendProcess.stderr?.on("data", (data) => {
    process.stderr.write(`[backend] ${data}`);
  });
  backendProcess.on("error", (err) => {
    console.error("[backend] failed to start:", err);
  });
  backendProcess.on("exit", (code, signal) => {
    console.log(`[backend] exited (code=${code}, signal=${signal})`);
    backendProcess = null;
    // If the backend dies unexpectedly (not as part of our own quit flow),
    // surface it and quit — mirrors _report_fatal() in launcher.py.
    if (!quitting) {
      quitting = true;
      const { dialog } = require("electron");
      dialog.showErrorBox(
        "Soniox Live Translate",
        "The backend process exited unexpectedly. The application will now close."
      );
      app.quit();
    }
  });
}

function waitForHealth(timeoutMs = 30000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(HEALTH_URL, { timeout: 2000 }, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          resolve();
        } else {
          retry();
        }
      });
      req.on("error", retry);
      req.on("timeout", () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() - start > timeoutMs) {
        reject(new Error("Timed out waiting for backend /health"));
        return;
      }
      setTimeout(attempt, 600);
    };
    attempt();
  });
}

function killBackend() {
  if (!backendProcess) return;
  const pid = backendProcess.pid;
  if (process.platform === "win32" && pid) {
    // Kill the whole process tree; a plain kill() can leave orphaned
    // children behind (e.g. uvicorn workers spawned by the exe).
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
  } else {
    try {
      backendProcess.kill();
    } catch {
      // process may already be gone
    }
  }
  backendProcess = null;
}

function registerDisplayMediaHandler() {
  // Primary (and only) solution for tab/system audio capture, per plan:
  // rely on Electron's native system picker so the existing
  // navigator.mediaDevices.getDisplayMedia({video:true, audio:true}) call in
  // frontend/src/app.ts keeps working completely unmodified.
  session.defaultSession.setDisplayMediaRequestHandler(
    (request, callback) => {
      // useSystemPicker:true means Chromium/the OS already showed the native
      // picker and pre-selected the source; hand back a loopback audio
      // request so both video+audio are granted together.
      callback({ video: undefined, audio: "loopback" });
    },
    { useSystemPicker: true }
  );
}

function createWindow() {
  const iconPath = path.join(__dirname, "build", process.platform === "win32" ? "icon.ico" : "icon.png");

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 720,
    minHeight: 560,
    autoHideMenuBar: true,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.loadURL(BASE_URL);

  // Open any external links (e.g. target=_blank) in the OS browser instead
  // of a new Electron window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(BASE_URL)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.on("close", (event) => {
    if (!quitting) {
      // Closing the window minimizes to tray instead of quitting, matching
      // the previous pystray-based launcher behavior.
      event.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray() {
  const iconPath = path.join(__dirname, "build", "icon.png");
  const trayIconPath = fs.existsSync(iconPath) ? iconPath : path.join(__dirname, "build", "icon.ico");
  tray = new Tray(trayIconPath);
  tray.setToolTip("Soniox Live Translate");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "Open",
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        },
      },
      {
        label: "Settings",
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
            mainWindow.loadURL(`${BASE_URL}/setup`);
          }
        },
      },
      { type: "separator" },
      {
        label: "Quit",
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ])
  );
  tray.on("click", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

app.whenReady().then(async () => {
  registerDisplayMediaHandler();
  startBackend();

  try {
    await waitForHealth();
  } catch (err) {
    console.error(err);
    const { dialog } = require("electron");
    dialog.showErrorBox(
      "Soniox Live Translate",
      "The backend did not become ready in time. Please check your setup and try again."
    );
    quitting = true;
    killBackend();
    app.quit();
    return;
  }

  createWindow();
  createTray();
});

app.on("window-all-closed", () => {
  // Keep running in the tray on all platforms; only Tray "Quit" or
  // app.quit() (e.g. from the OS) should actually terminate the app.
});

app.on("activate", () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  } else if (app.isReady()) {
    createWindow();
  }
});

app.on("before-quit", () => {
  quitting = true;
  killBackend();
});
