/**
 * IndexedDB wrapper for persistent session transcript history.
 *
 * Replaces the previous localStorage-based store (5 MB synchronous limit)
 * with an async IndexedDB store that can hold thousands of sessions.
 */

import type { Utterance } from "./types";

const DB_NAME = "soniox-transcripts";
const DB_VERSION = 1;
const STORE_NAME = "sessions";
const HISTORY_MAX = 200; // soft cap – oldest entries pruned on push

export interface HistoryEntry {
  id: string;
  ts: number;
  mode: string;
  targetLang: string;
  utteranceCount: number;
  utterances: Utterance[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("ts", "ts", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txGetAll(db: IDBDatabase): Promise<HistoryEntry[]> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.index("ts").openCursor(null, "prev"); // newest first
    const results: HistoryEntry[] = [];
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        results.push(cursor.value as HistoryEntry);
        cursor.continue();
      } else {
        resolve(results);
      }
    };
    req.onerror = () => reject(req.error);
  });
}

function txPut(db: IDBDatabase, entry: HistoryEntry): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

function txDelete(db: IDBDatabase, id: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

function txClear(db: IDBDatabase): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// ---------------------------------------------------------------------------
// Public API – thin wrappers that swallow errors so the UI never breaks
// ---------------------------------------------------------------------------

export async function listSessions(): Promise<HistoryEntry[]> {
  try {
    const db = await openDB();
    const entries = await txGetAll(db);
    db.close();
    return entries;
  } catch {
    return [];
  }
}

export async function getSession(id: string): Promise<HistoryEntry | undefined> {
  try {
    const db = await openDB();
    const all = await txGetAll(db);
    db.close();
    return all.find((e) => e.id === id);
  } catch {
    return undefined;
  }
}

export async function saveSession(entry: HistoryEntry): Promise<void> {
  try {
    const db = await openDB();
    await txPut(db, entry);

    // Enforce soft cap: if over HISTORY_MAX, delete oldest entries.
    const all = await txGetAll(db); // already sorted desc by ts
    if (all.length > HISTORY_MAX) {
      const toRemove = all.slice(HISTORY_MAX);
      for (const e of toRemove) {
        await txDelete(db, e.id);
      }
    }
    db.close();
  } catch {
    // storage unavailable – silently ignore
  }
}

export async function deleteSession(id: string): Promise<void> {
  try {
    const db = await openDB();
    await txDelete(db, id);
    db.close();
  } catch {
    // ignore
  }
}

export async function clearSessions(): Promise<void> {
  try {
    const db = await openDB();
    await txClear(db);
    db.close();
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Migration: one-shot import from legacy localStorage key.
// ---------------------------------------------------------------------------

const LEGACY_KEY = "soniox_history_v1";

export async function migrateFromLocalStorage(): Promise<void> {
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    if (!raw) return;
    const entries: HistoryEntry[] = JSON.parse(raw);
    if (!Array.isArray(entries) || !entries.length) return;

    const db = await openDB();
    // Only import entries not already present.
    const existing = await txGetAll(db);
    const existingIds = new Set(existing.map((e) => e.id));
    for (const entry of entries) {
      if (!existingIds.has(entry.id)) {
        await txPut(db, entry);
      }
    }
    db.close();

    // Remove legacy data after successful import.
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // migration best-effort
  }
}
