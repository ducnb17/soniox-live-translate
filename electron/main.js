"use strict";

const { app, BrowserWindow, Tray, Menu, session, shell } = require("electron");

// ---------------------------------------------------------------------------
// Sentry — initialise as early as possible after require so startup crashes
// are caught.  Set SENTRY_DSN in the environment (or via .env loaded by the
// backend) to enable monitoring.  The init is a no-op when the variable is
// absent.
// ---------------------------------------------------------------------------
(function initSentry() {
  const dsn = process.env.SENTRY_DSN;
  if (!dsn) return;
  try {
    const { init } = require("@sentry/electron/main");
    init({
      dsn,
      environment: process.env.SENTRY_ENVIRONMENT || (app.isPackaged ? "production" : "development"),
      release: process.env.SENTRY_RELEASE,   // set by CI; undefined in dev is fine
      // Capture 10 % of transactions for performance monitoring.
      tracesSampleRate: 0.1,
      // Don't send PII (user IPs, etc.).
      sendDefaultPii: false,
    });
  } catch (e) {
    // @sentry/electron not available — silently skip.
  }
})();

const path = require("node:path");
const http = require("node:http");
const { spawn } = require("node:child_process");
const fs = require("node:fs");

const HOST = "127.0.0.1";
const PORT = 8765;
const BASE_URL = `http://${HOST}:${PORT}`;
const HEALTH_URL = `${BASE_URL}/health`;
const SETUP_STATUS_URL = `${BASE_URL}/setup/status`;
const IS_DEV = !app.isPackaged;

let mainWindow = null;
let tray = null;
let backendProcess = null;
let quitting = false;

// ---------------------------------------------------------------------------
// Backend process management
// ---------------------------------------------------------------------------

function resolveBackendCommand() {
  if (IS_DEV) {
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

  // Production: spawn the PyInstaller-built exe bundled as an extraResource.
  const exeName =
    process.platform === "win32" ? "SonioxLiveTranslate.exe" : "SonioxLiveTranslate";
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
    // Kill entire process tree so uvicorn workers don't become orphans.
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

// ---------------------------------------------------------------------------
// Display media / tab+system audio capture
// ---------------------------------------------------------------------------

function registerDisplayMediaHandler() {
  // Primary approach: Electron 30+ native system picker.
  // Keeps navigator.mediaDevices.getDisplayMedia({video:true, audio:true}) in
  // frontend/src/app.ts (acquireInputStream) working unmodified.
  // audio:"loopback" requests Windows loopback capture alongside the picker.
  session.defaultSession.setDisplayMediaRequestHandler(
    (request, callback) => {
      // Electron 31+ requires `video` to be a WebFrameMain or
      // DesktopCapturerSource — request.frame satisfies that contract
      // while useSystemPicker still drives the native OS picker.
      callback({ video: request.frame, audio: "loopback" });
    },
    { useSystemPicker: true }
  );

  // Grant microphone permission automatically. Without this handler,
  // Electron with sandbox:true may silently deny getUserMedia({audio:true}),
  // breaking microphone capture in the live translation flow.
  session.defaultSession.setPermissionRequestHandler(
    (_webContents, permission, callback) => {
      if (permission === "media") {
        callback(true);
      } else {
        callback(false);
      }
    }
  );
}

// ---------------------------------------------------------------------------
// BrowserWindow
// ---------------------------------------------------------------------------

function getIconPath() {
  // In dev, icons live in electron/build/. In production they are bundled
  // as extraResources into resources/icons/ next to resources/backend/.
  const base = app.isPackaged
    ? path.join(process.resourcesPath, "icons")
    : path.join(__dirname, "build");
  const ico = path.join(base, "icon.ico");
  const png = path.join(base, "icon.png");
  if (process.platform === "win32" && fs.existsSync(ico)) return ico;
  if (fs.existsSync(png)) return png;
  if (fs.existsSync(ico)) return ico;
  return undefined;
}

async function createWindow() {
  const iconPath = getIconPath();

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 720,
    minHeight: 560,
    autoHideMenuBar: true,
    icon: iconPath,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Decide which URL to load first — if not configured yet, go to setup page.
  let startUrl = BASE_URL;
  try {
    const statusData = await fetchJson(SETUP_STATUS_URL);
    if (!statusData.configured) {
      startUrl = `${BASE_URL}/setup`;
    }
  } catch {
    // If /setup/status fails, fall back to root; backend will redirect if needed.
  }

  mainWindow.loadURL(startUrl);

  // Open external links in the OS browser, not a new Electron window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(BASE_URL)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  // Block unexpected navigation outside the local backend.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(BASE_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Closing the window minimizes to tray instead of quitting.
  mainWindow.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// System tray
// ---------------------------------------------------------------------------

function createTray() {
  const trayIconPath = getIconPath();
  if (!trayIconPath) {
    console.warn("[tray] No icon found — tray unavailable");
    return;
  }

  tray = new Tray(trayIconPath);
  tray.setToolTip("Soniox Live Translate");

  const contextMenu = Menu.buildFromTemplate([
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
  ]);

  tray.setContextMenu(contextMenu);
  tray.on("click", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 3000 }, (res) => {
      let raw = "";
      res.on("data", (chunk) => (raw += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(raw));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
  });
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

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

  await createWindow();
  createTray();
});

// Keep running in the tray when all windows are closed.
app.on("window-all-closed", () => { });

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
