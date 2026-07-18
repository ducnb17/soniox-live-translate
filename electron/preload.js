// Preload script for the Soniox Live Translate desktop shell.
//
// contextIsolation is enabled and nodeIntegration is disabled (see main.js),
// so the renderer (the existing frontend/src/app.ts bundle, served by the
// backend at http://127.0.0.1:8765) runs with no Node/Electron APIs by
// default. This file is intentionally minimal: the app currently needs no
// privileged bridge (getDisplayMedia works via the main-process
// setDisplayMediaRequestHandler without any renderer-side IPC), so nothing
// is exposed yet. Kept as a placeholder / extension point so we don't have
// to touch main.js's webPreferences again if a bridge is ever needed.
"use strict";

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("sonioxDesktop", {
  isElectron: true,
});
