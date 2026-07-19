import {
  TTS_SAMPLE_RATE,
  BARGE_RMS_THRESHOLD,
  BARGE_HOLD_MS,
  BARGE_TTS_START_GRACE_MS,
  LANGUAGES,
  VOICES,

  type SonioxSttResponse,
  type Utterance,
  type AppMode,
  type AppState,
  type TranslationMode,
  type AudioSource,
  type ConnectionStatus,
} from "./types";
import { resolveAudioDevices, type AudioDeviceLike } from "./device-selection";


// UTF-8 safe base64 (handles non-ASCII context text).
function b64Utf8(str: string): string {
  return btoa(unescape(encodeURIComponent(str)));
}

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Element #${id} not found`);
  return el as T;
};

const $mode = (): TranslationMode =>
  (document.querySelector<HTMLInputElement>("input[name=mode]:checked")?.value as TranslationMode) || "one_way";

const $audioSourceBlock = $<HTMLDivElement>("audio-source-block");
const $audioSource = (): AudioSource =>
  (document.querySelector<HTMLInputElement>("input[name=audio-source]:checked")?.value as AudioSource) ||
  "microphone";

const $targetLang = $<HTMLSelectElement>("target-language");

const $langA = $<HTMLSelectElement>("lang-a");
const $langB = $<HTMLSelectElement>("lang-b");
const $oneWayBlock = $<HTMLDivElement>("one-way-block");
const $twoWayBlock = $<HTMLDivElement>("two-way-block");
const $voice = $<HTMLSelectElement>("voice");
const $voiceB = $<HTMLSelectElement>("voice-b");
const $voiceBBlock = $<HTMLDivElement>("voice-b-block");
const $diarization = $<HTMLInputElement>("diarization");
const $langId = $<HTMLInputElement>("lang-id");
const $tts = $<HTMLInputElement>("tts");
const $barge = $<HTMLInputElement>("barge");
const $bargeHint = $<HTMLParagraphElement>("barge-hint");
const $contextJson = $<HTMLTextAreaElement>("context-json");
const $actionRow = document.querySelector<HTMLDivElement>(".action-row")!;
const $actionBtn = $<HTMLButtonElement>("action");
const $actionLabel = $actionBtn.querySelector<HTMLSpanElement>(".btn-label")!;
const $modeToggle = $<HTMLButtonElement>("mode-toggle");
const $audioUrl = $<HTMLInputElement>("audio-url");
const $originalCol = $<HTMLDivElement>("original");
const $translationCol = $<HTMLDivElement>("translation");
const $status = $<HTMLSpanElement>("status");
const $bargeMeter = $<HTMLDivElement>("barge-meter");
const $bargeBar = $<HTMLDivElement>("barge-bar");
const $sessionInfo = $<HTMLDivElement>("session-info");
const $sessionId = document.getElementById("session-id") as HTMLElement;
const $transcriptLink = $<HTMLAnchorElement>("transcript-link");
const $dlJson = $<HTMLButtonElement>("dl-json");
const $dlCsv = $<HTMLButtonElement>("dl-csv");
const $themeToggle = $<HTMLButtonElement>("theme-toggle");
const $inputDevice = $<HTMLSelectElement>("input-device");
const $outputDevice = $<HTMLSelectElement>("output-device");
const $btnTestInput = $<HTMLButtonElement>("btn-test-input");
const $btnStopTestInput = $<HTMLButtonElement>("btn-stop-test-input");
const $btnTestOutput = $<HTMLButtonElement>("btn-test-output");
const $inputLevelMeter = $<HTMLDivElement>("input-level-meter");
const $inputLevelBar = $<HTMLDivElement>("input-level-bar");
const $connectionDot = $<HTMLDivElement>("connection-dot");
const $btnRetry = $<HTMLButtonElement>("btn-retry");
const $ttsProvider = $<HTMLSelectElement>("tts-provider-select");
const $ttsVoice = $<HTMLSelectElement>("tts-voice-select");
const $ttsApiKey = $<HTMLInputElement>("tts-api-key");
const $ttsApiKeyRow = $<HTMLDivElement>("tts-api-key-row");
const $btnSaveTtsKey = $<HTMLButtonElement>("btn-save-tts-key");
const $ttsCostHint = $<HTMLParagraphElement>("tts-cost-hint");

// ---------------------------------------------------------------------------
// Populate selectors
// ---------------------------------------------------------------------------
function populateLangs(select: HTMLSelectElement, value: string): void {
  for (const [code, name] of LANGUAGES) {
    const opt = document.createElement("option");
    opt.value = code;
    opt.textContent = name;
    select.appendChild(opt);
  }
  select.value = value;
}

function populateVoices(select: HTMLSelectElement, value: string): void {
  for (const name of VOICES) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  select.value = value;
}

populateLangs($targetLang, "vi");
populateLangs($langA, "en");
populateLangs($langB, "es");
populateVoices($voice, "Maya");
populateVoices($voiceB, "Daniel");

// ---------------------------------------------------------------------------
// Device enumeration & selection
// ---------------------------------------------------------------------------
const INPUT_DEVICE_KEY = "soniox-input-device";
const OUTPUT_DEVICE_KEY = "soniox-output-device";

let isTestingMic = false;
let testMicStream: MediaStream | null = null;
let testMicRaf: number | null = null;

function getSavedDeviceId(key: string): string {
  try { return localStorage.getItem(key) || "default"; }
  catch { return "default"; }
}

function saveDeviceId(key: string, id: string): void {
  try { localStorage.setItem(key, id); } catch { /* storage disabled */ }
}

async function refreshDeviceList(): Promise<void> {
  if (!navigator.mediaDevices?.enumerateDevices) {
    populateDeviceSelect($inputDevice, [], "default");
    populateDeviceSelect($outputDevice, [], "default");
    setStatus("Audio device enumeration is not supported; using System Default");
    return;
  }

  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const resolved = resolveAudioDevices(
      devices,
      getSavedDeviceId(INPUT_DEVICE_KEY),
      getSavedDeviceId(OUTPUT_DEVICE_KEY),
    );

    populateDeviceSelect($inputDevice, resolved.inputs, resolved.inputId);
    populateDeviceSelect($outputDevice, resolved.outputs, resolved.outputId);

    const missing: string[] = [];
    if (resolved.missingInput) {
      saveDeviceId(INPUT_DEVICE_KEY, "default");
      missing.push("microphone");
    }
    if (resolved.missingOutput) {
      saveDeviceId(OUTPUT_DEVICE_KEY, "default");
      missing.push("speaker");
      if (audioCtx) {
        void setAudioOutputDevice(audioCtx, "default").catch((error: unknown) => {
          console.warn("Could not reroute active audio to System Default", error);
        });
      }
    }

    if (missing.length) {
      setStatus(`Saved ${missing.join(" and ")} no longer available; switched to System Default`);
    } else if (!resolved.inputs.length && !resolved.outputs.length) {
      setStatus("No audio devices found; using System Default");
    }
  } catch (error) {
    populateDeviceSelect($inputDevice, [], "default");
    populateDeviceSelect($outputDevice, [], "default");
    setStatus(`Could not read audio devices; using System Default (${(error as Error).message})`);
  }
}

function populateDeviceSelect(
  select: HTMLSelectElement,
  devices: readonly AudioDeviceLike[],
  selectedId: string,
): void {
  select.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "default";
  defaultOpt.textContent = "System Default";
  select.appendChild(defaultOpt);

  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || `Device ${d.deviceId.slice(0, 8)}`;
    select.appendChild(opt);
  }
  select.value = devices.some(d => d.deviceId === selectedId) ? selectedId : "default";
}

function onDeviceChange(): void {
  void refreshDeviceList();
}

$inputDevice.addEventListener("change", () => {
  saveDeviceId(INPUT_DEVICE_KEY, $inputDevice.value);
  setStatus(`Microphone set to ${$inputDevice.selectedOptions[0]?.textContent || "System Default"}`);
});

$outputDevice.addEventListener("change", () => {
  saveDeviceId(OUTPUT_DEVICE_KEY, $outputDevice.value);
  setStatus(`Speaker set to ${$outputDevice.selectedOptions[0]?.textContent || "System Default"}`);
});

function isMissingDeviceError(error: unknown): boolean {
  const name = error && typeof error === "object" && "name" in error ? String(error.name) : "";
  return name === "NotFoundError" || name === "OverconstrainedError";
}

async function getSelectedInputStream(baseConstraints: MediaTrackConstraints): Promise<MediaStream> {
  const deviceId = $inputDevice.value;
  const selectedConstraints: MediaTrackConstraints = { ...baseConstraints };
  if (deviceId !== "default") selectedConstraints.deviceId = { exact: deviceId };

  try {
    return await navigator.mediaDevices.getUserMedia({ audio: selectedConstraints });
  } catch (error) {
    if (deviceId === "default" || !isMissingDeviceError(error)) throw error;
    $inputDevice.value = "default";
    saveDeviceId(INPUT_DEVICE_KEY, "default");
    setStatus("Selected microphone disappeared; switched to System Default");
    return navigator.mediaDevices.getUserMedia({ audio: baseConstraints });
  }
}

// Test input mic with level meter
async function startTestMic(): Promise<void> {
  if (isTestingMic) return stopTestMic();
  isTestingMic = true;
  $btnTestInput.classList.add("hidden");
  $btnStopTestInput.classList.remove("hidden");
  $inputLevelMeter.classList.remove("hidden");

  try {
    testMicStream = await getSelectedInputStream({});
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(testMicStream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.4;
    source.connect(analyser);
    const arr = new Uint8Array(analyser.frequencyBinCount);

    function tick(): void {
      if (!isTestingMic) { ctx.close(); return; }
      analyser.getByteFrequencyData(arr);
      let sum = 0;
      for (let i = 0; i < arr.length; i++) sum += arr[i];
      const avg = sum / arr.length;
      const pct = Math.min(100, Math.round((avg / 255) * 200));
      $inputLevelBar.style.width = `${pct}%`;
      $inputLevelBar.classList.toggle("warn", pct > 50 && pct <= 80);
      $inputLevelBar.classList.toggle("hot", pct > 80);
      testMicRaf = requestAnimationFrame(tick);
    }
    tick();

    // Auto-stop after 10s
    setTimeout(() => { if (isTestingMic) stopTestMic(); }, 10000);
  } catch (err) {
    setStatus(`Mic test failed: ${(err as Error).message}`);
    stopTestMic();
  }
}

function stopTestMic(): void {
  isTestingMic = false;
  if (testMicRaf) cancelAnimationFrame(testMicRaf);
  testMicRaf = null;
  if (testMicStream) {
    testMicStream.getTracks().forEach(t => t.stop());
    testMicStream = null;
  }
  $btnTestInput.classList.remove("hidden");
  $btnStopTestInput.classList.add("hidden");
  $inputLevelMeter.classList.add("hidden");
  $inputLevelBar.style.width = "0%";
  $inputLevelBar.classList.remove("warn", "hot");
}

$btnTestInput.addEventListener("click", startTestMic);
$btnStopTestInput.addEventListener("click", stopTestMic);

type SinkRoutableAudioContext = AudioContext & {
  setSinkId?: (sinkId: string) => Promise<void>;
};

async function setAudioOutputDevice(ctx: AudioContext, deviceId: string): Promise<void> {
  const setSinkId = (ctx as SinkRoutableAudioContext).setSinkId;
  if (deviceId === "default") {
    // An empty sink id selects the current OS default. This also reroutes an
    // already-active context after its previously selected device is removed.
    if (typeof setSinkId === "function") await setSinkId.call(ctx, "");
    return;
  }
  if (typeof setSinkId !== "function") {
    throw new Error("this browser cannot route audio to a selected output device");
  }
  await setSinkId.call(ctx, deviceId);
}

// Test output speaker
async function testSpeaker(): Promise<void> {
  let ctx: AudioContext | null = null;
  try {
    ctx = new AudioContext();
    const devId = $outputDevice.value;
    await setAudioOutputDevice(ctx, devId);
    playTestTone(ctx, "Playing test tone...");
  } catch (err) {
    $outputDevice.value = "default";
    saveDeviceId(OUTPUT_DEVICE_KEY, "default");
    if (ctx) {
      await setAudioOutputDevice(ctx, "default").catch(() => undefined);
      playTestTone(ctx, `Selected speaker unavailable; using System Default (${(err as Error).message})`);
    } else {
      setStatus(`Speaker test failed: ${(err as Error).message}`);
    }
  }
}

function playTestTone(ctx: AudioContext, message: string): void {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = 440;
  gain.gain.setValueAtTime(0.3, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.addEventListener("ended", () => { void ctx.close(); }, { once: true });
  osc.start(ctx.currentTime);
  osc.stop(ctx.currentTime + 0.3);
  setStatus(message);
}

$btnTestOutput.addEventListener("click", testSpeaker);

// Initialize devices
if (navigator.mediaDevices) {
  void refreshDeviceList();
  navigator.mediaDevices.addEventListener("devicechange", onDeviceChange);
}

// ---------------------------------------------------------------------------
// TTS Provider selection
// ---------------------------------------------------------------------------
let ttsProviders: any[] = [];
let currentTtsProvider = "soniox";

async function loadTtsProviders(): Promise<void> {
  try {
    const resp = await fetch("/api/tts/providers");
    ttsProviders = await resp.json();
    populateTtsProviderSelect();
    // Also load config
    const cfgResp = await fetch("/api/tts/config");
    const cfg = await cfgResp.json();
    currentTtsProvider = cfg.current_provider || "soniox";
    $ttsProvider.value = currentTtsProvider;
    await onTtsProviderChange();
    // Set saved voice
    if (cfg.current_voice) {
      setTimeout(() => { $ttsVoice.value = cfg.current_voice; }, 200);
    }
  } catch {
    setTimeout(loadTtsProviders, 3000);
  }
}

function populateTtsProviderSelect(): void {
  $ttsProvider.innerHTML = "";
  for (const p of ttsProviders) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.name}${p.has_api_key ? " [key]" : ""}`;
    $ttsProvider.appendChild(opt);
  }
}

async function onTtsProviderChange(): Promise<void> {
  const pid = $ttsProvider.value;
  currentTtsProvider = pid;
  const provider = ttsProviders.find(p => p.id === pid);

  // Show/hide API key row
  $ttsApiKeyRow.style.display = provider?.requires_api_key ? "block" : "none";

  // Update cost hint
  if (provider) {
    $ttsCostHint.textContent = `Cost: ~$${provider.approximate_cost_per_1m_chars}/million chars`;
  }

  // Load voices
  await loadTtsVoices(pid);
}

async function loadTtsVoices(providerId: string): Promise<void> {
  $ttsVoice.innerHTML = '<option value="">Loading...</option>';
  try {
    const lang = $mode() === "two_way" ? $langB.value : $targetLang.value;
    const resp = await fetch(`/api/tts/providers/${providerId}/voices?lang=${lang}`);
    const voices = await resp.json();
    $ttsVoice.innerHTML = "";
    for (const v of voices) {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = v.name;
      $ttsVoice.appendChild(opt);
    }
  } catch {
    $ttsVoice.innerHTML = '<option value="">Error loading</option>';
  }
}

$ttsProvider.addEventListener("change", onTtsProviderChange);

$btnSaveTtsKey.addEventListener("click", async () => {
  const pid = $ttsProvider.value;
  const key = $ttsApiKey.value.trim();
  if (!key) return;
  try {
    await fetch("/api/tts/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: pid, api_key: key }),
    });
    setStatus("API key saved");
    $ttsApiKey.value = "";
    await loadTtsProviders();
  } catch (err) {
    setStatus("Failed to save API key");
  }
});

// Load providers on startup
loadTtsProviders();

$btnRetry.addEventListener("click", () => {
  void retryConnection();
});

// ---------------------------------------------------------------------------
// Mode toggle
// ---------------------------------------------------------------------------
function syncMode(): void {
  const two = $mode() === "two_way";
  $oneWayBlock.classList.toggle("hidden", two);
  $twoWayBlock.classList.toggle("hidden", !two);
  $voiceBBlock.classList.toggle("hidden", !two);
}

document.querySelectorAll<HTMLInputElement>("input[name=mode]").forEach((r) =>
  r.addEventListener("change", syncMode),
);
syncMode();

const DEFAULT_AUDIO_URL = "https://soniox.com/media/examples/spanish_weather_report.mp3";
$audioUrl.value = new URLSearchParams(location.search).get("audio") || DEFAULT_AUDIO_URL;

// ---------------------------------------------------------------------------
// Session History (localStorage)
// ---------------------------------------------------------------------------
const HISTORY_KEY = "soniox_history_v1";
const HISTORY_MAX = 50;

interface HistoryEntry {
  id: string;
  ts: number;
  mode: string;
  targetLang: string;
  utteranceCount: number;
  utterances: Utterance[];
}

const sessionHistory = {
  load(): HistoryEntry[] {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]"); }
    catch { return []; }
  },
  save(entries: HistoryEntry[]): void {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_MAX))); }
    catch { /* storage full */ }
  },
  push(entry: HistoryEntry): void { const l = this.load(); l.unshift(entry); this.save(l); },
  remove(id: string): void       { this.save(this.load().filter((e) => e.id !== id)); },
  clear(): void                  { localStorage.removeItem(HISTORY_KEY); },
};

function saveToHistory(utts: Utterance[], translationMode: string, targetLang: string): void {
  const final = utts.filter((u) => u.originalFinal || u.translationFinal);
  if (!final.length) return;
  sessionHistory.push({
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    ts: Date.now(),
    mode: translationMode,
    targetLang,
    utteranceCount: final.length,
    utterances: final,
  });
  const panel = document.getElementById("history-panel");
  if (panel?.classList.contains("open")) renderHistoryPanel();
}

function renderHistoryPanel(): void {
  const list = document.getElementById("history-list");
  if (!list) return;
  const entries = sessionHistory.load();
  if (!entries.length) {
    list.innerHTML = '<p class="history-empty">Chưa có phiên nào.</p>';
    return;
  }
  list.innerHTML = entries
    .map((e) => {
      const date    = new Date(e.ts).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
      const arrow   = e.mode === "two_way" ? "↔" : "→";
      const preview = (e.utterances[0]?.originalFinal ?? "").slice(0, 72);
      return `<div class="history-item">
        <div class="history-meta">
          <span class="history-date">${date}</span>
          <span class="history-badge">${arrow}&nbsp;${e.targetLang.toUpperCase()}</span>
          <span class="history-count">${e.utteranceCount} câu</span>
        </div>
        <div class="history-preview">${preview}…</div>
        <div class="history-actions">
          <button class="hbtn hbtn-view" data-id="${e.id}">Xem</button>
          <button class="hbtn hbtn-json" data-id="${e.id}">JSON</button>
          <button class="hbtn hbtn-csv"  data-id="${e.id}">CSV</button>
          <button class="hbtn hbtn-del"  data-id="${e.id}">🗑</button>
        </div>
      </div>`;
    })
    .join("");

  list.querySelectorAll<HTMLButtonElement>(".hbtn-view").forEach((b) => {
    b.onclick = () => {
      const e = sessionHistory.load().find((x) => x.id === b.dataset.id);
      if (e) { utterances = e.utterances; render(); }
    };
  });
  list.querySelectorAll<HTMLButtonElement>(".hbtn-json").forEach((b) => {
    b.onclick = () => {
      const e = sessionHistory.load().find((x) => x.id === b.dataset.id);
      if (e) downloadBlob(new Blob([JSON.stringify(e, null, 2)], { type: "application/json" }), `transcript-${e.id}.json`);
    };
  });
  list.querySelectorAll<HTMLButtonElement>(".hbtn-csv").forEach((b) => {
    b.onclick = () => {
      const e = sessionHistory.load().find((x) => x.id === b.dataset.id);
      if (!e) return;
      const rows = [
        ["speaker", "language", "original", "translation"].join(","),
        ...e.utterances.map((u) =>
          [u.speaker ?? "", u.language ?? "",
           `"${(u.originalFinal ?? "").replace(/"/g, '""')}"`,
           `"${(u.translationFinal ?? "").replace(/"/g, '""')}"`,
          ].join(",")
        ),
      ];
      downloadBlob(new Blob(["\uFEFF" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" }), `transcript-${e.id}.csv`);
    };
  });
  list.querySelectorAll<HTMLButtonElement>(".hbtn-del").forEach((b) => {
    b.onclick = () => { sessionHistory.remove(b.dataset.id!); renderHistoryPanel(); };
  });
}

function toggleHistoryPanel(): void {
  const panel = document.getElementById("history-panel");
  const btn   = document.getElementById("btn-history-toggle");
  if (!panel || !btn) return;
  const open = panel.classList.toggle("open");
  btn.textContent = open ? "Lịch sử ▲" : "Lịch sử ▼";
  if (open) renderHistoryPanel();
}

// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------
let mode: AppMode = "file";
let state: AppState = "idle";
let mediaRecorder: MediaRecorder | null = null;
let audioCtx: AudioContext | null = null;
let nextPlayTime = 0;
let utterances: Utterance[] = [];
let currentUtt = newUtt();
let fileAudio: HTMLAudioElement | null = null;
let fileTtsHeard = false;
let ws: WebSocket | null = null;
let sessionId: string | null = null;
let connectionStatus: ConnectionStatus = "idle";
let pendingAudioBlobs: Blob[] = [];
let pendingAudioOverflowed = false;
let manualStopRequested = false;
let lastWebSocketParams: Record<string, string> = {};
let manualRetryInProgress = false;
let resumeTranscriptOnNextSession = false;

// Scheduled TTS audio sources for barge-in interrupt.
let activeSources: AudioBufferSourceNode[] = [];

// Barge-in VAD state
let bargeAnalyser: AnalyserNode | null = null;
let bargeArray: Uint8Array<ArrayBuffer> | null = null;
let bargeRaf: number | null = null;
let bargeHoldSince = 0;
let bargeArmed = false;
let micStream: MediaStream | null = null;
// Timestamp of the most recent empty -> non-empty transition of
// activeSources (i.e. a new TTS chunk started playing after silence).
// Used to suppress barge-in during the initial grace window, since the
// onset "pop" of TTS audio can be picked up by the mic as echo.
let bargeTtsStartedAt = 0;
let wasTtsAudible = false;


function newUtt(): Utterance {
  return {
    speaker: null,
    language: null,
    originalFinal: "",
    originalPartial: "",
    translationFinal: "",
    translationPartial: "",
  };
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function flushPendingAudio(): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    for (const blob of pendingAudioBlobs) {
      try { ws.send(blob); } catch { break; }
    }
  }
  pendingAudioBlobs = [];
}


function isRetryableSttError(data: SonioxSttResponse): boolean {
  if (data.error_type === "service_unavailable" || data.error_type === "max_duration_reached") {
    return true;
  }
  const numericCode = Number(data.error_code);
  return Number.isFinite(numericCode) && numericCode >= 500;
}


function openWebSocket(extraParams: Record<string, string> = {}): Promise<void> {
  lastWebSocketParams = { ...extraParams };
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const m = $mode();
  const params = new URLSearchParams({
    mode: m,
    lang_id: String($langId.checked),
    diarize: String($diarization.checked),
    voice: $voice.value,
    tts: String($tts.checked),
    ...extraParams,
  });

  if (m === "one_way") {
    params.set("target_lang", $targetLang.value);
  } else {
    params.set("lang_a", $langA.value);
    params.set("lang_b", $langB.value);
    params.set("target_lang", $langB.value);
    params.set("voice_b", $voiceB.value);
  }

  const ctxText = $contextJson.value.trim();
  if (ctxText) {
    try {
      JSON.parse(ctxText);
      params.set("context_b64", b64Utf8(ctxText));
    } catch (e) {
      setStatus(`Context JSON invalid: ${(e as Error).message}`);
      return Promise.reject(new Error("invalid context json"));
    }
  }

  // Pass device selection to backend
  params.set("input_device", $inputDevice.value);
  params.set("output_device", $outputDevice.value);
  params.set("tts_provider", $ttsProvider.value);

  // Use TTS provider voice for one-way, or fallback to Soniox voices
  const ttsVoice = currentTtsProvider === "soniox" ? $voice.value : $ttsVoice.value;
  params.set("voice", ttsVoice);
  params.set("voice_b", currentTtsProvider === "soniox" ? $voiceB.value : ttsVoice);

  const url = `${proto}//${location.host}/ws/translate?${params}`;
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.onmessage = (event: MessageEvent) => {
    if (typeof event.data === "string") {
      const data: SonioxSttResponse = JSON.parse(event.data);

      if (data.session_id) {
        sessionId = data.session_id;
        showSessionInfo(sessionId);
        if (resumeTranscriptOnNextSession) {
          resumeTranscriptOnNextSession = false;
          sendTranscriptSnapshot();
        }
        return;
      }

      if (data.reconnecting) {
        setConnectionStatus("reconnecting");
        setStatus(`Đang kết nối lại… (lần ${data.attempt}/${data.max_attempts})`);
        return;
      }

      if (data.reconnected) {
        setConnectionStatus("connected");
        setStatus(`Đã kết nối lại sau ${((data.downtime_ms || 0) / 1000).toFixed(1)}s`);
        // Insert downtime marker in transcript if provided
        if (data.downtime_text) {
          currentUtt.originalFinal += data.downtime_text;
          render();
        }
        // Flush any buffered audio
        flushPendingAudio();
        return;
      }

      if (data.reconnect_failed) {
        setConnectionStatus("failed");
        setStatus(data.error_message || "Không thể kết nối lại.");
        return;
      }

      if (data.error_code || data.error_message) {
        if (isRetryableSttError(data)) {
          console.warn("Retryable STT error:", data.error_type, data.error_code);
          setConnectionStatus("reconnecting");
          setStatus("Kết nối STT bị gián đoạn, đang chuẩn bị thử lại…");
          return;
        }
        console.error("Server error:", data.error_code, data.error_message);
        const friendlyMsg = data.error_message
          ? data.error_message.replace(/code \d+/g, "").replace(/\(.*\)/g, "").trim()
          : "Lỗi kết nối. Vui lòng thử lại.";
        setState("idle", friendlyMsg);
        cleanup();
        return;
      }
      if (data.barge_ack) return;
      if (data.session_done) {
        stop();
        return;
      }
      handleSttResult(data);
    } else {
      handleTtsAudio(new Uint8Array(event.data as ArrayBuffer));
    }
  };

  ws.onclose = (event: CloseEvent) => {
    if (manualStopRequested) {
      setConnectionStatus("idle");
      return;
    }
    console.log("WebSocket closed", event.code, event.reason);
    if (event.code === 4000) {
      setConnectionStatus("failed");
      setStatus("Không thể kết nối lại. Nhấn “Thử lại” để tiếp tục phiên.");
    }
  };

  return new Promise<void>((resolve, reject) => {
    ws!.onopen = () => resolve();
    ws!.onerror = () => reject(new Error("WebSocket error"));
  });
}


function sendTranscriptSnapshot(): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const snapshot = [...utterances, currentUtt].filter(
    (u) =>
      u.originalFinal ||
      u.translationFinal ||
      u.originalPartial ||
      u.translationPartial,
  );
  if (!snapshot.length) return;
  try {
    ws.send(JSON.stringify({ type: "utterances", utterances: snapshot }));
  } catch { /* socket closed between readyState check and send */ }
}


async function retryConnection(): Promise<void> {
  if (connectionStatus !== "failed" || manualRetryInProgress) return;
  manualRetryInProgress = true;
  manualStopRequested = false;
  resumeTranscriptOnNextSession = true;
  setConnectionStatus("reconnecting");
  setStatus("Đang thử kết nối lại…");

  try {
    await openWebSocket(lastWebSocketParams);
    setConnectionStatus("connected");
    setStatus("Đã kết nối lại thủ công");
    if (pendingAudioOverflowed) {
      currentUtt.originalFinal += "[mất âm thanh trong lúc chờ thử lại; buffer trình duyệt đầy]";
      pendingAudioOverflowed = false;
      render();
    }
    flushPendingAudio();
  } catch (error) {
    console.error("Manual reconnect failed", error);
    resumeTranscriptOnNextSession = false;
    setConnectionStatus("failed");
    setStatus("Thử lại chưa thành công. Vui lòng kiểm tra mạng và thử lại.");
  } finally {
    manualRetryInProgress = false;
  }
}


function showSessionInfo(id: string): void {
  $sessionId.textContent = id;
  $transcriptLink.href = `/transcript/${id}`;
  $transcriptLink.textContent = `/transcript/${id}`;
  $sessionInfo.classList.remove("hidden");
}

function handleSttResult(data: SonioxSttResponse): void {
  if (data.session_done) {
    stop();
    return;
  }

  currentUtt.originalPartial = "";
  currentUtt.translationPartial = "";

  for (const t of data.tokens || []) {
    if (!t.text) continue;

    if (t.text === "<end>") {
      if (
        currentUtt.originalFinal ||
        currentUtt.translationFinal ||
        currentUtt.originalPartial ||
        currentUtt.translationPartial
      ) {
        utterances.push(currentUtt);
        currentUtt = newUtt();
      }
      continue;
    }

    if (t.speaker != null) currentUtt.speaker = t.speaker;

    const isTranslation = t.translation_status === "translation";
    const spokenLang = isTranslation ? t.source_language : t.language;
    if (spokenLang) currentUtt.language = spokenLang;

    const side = isTranslation ? "translation" : "original" as const;
    if (t.is_final) {
      currentUtt[`${side}Final`] += t.text;
    } else {
      currentUtt[`${side}Partial`] += t.text;
    }
  }

  render();
}

// ---------------------------------------------------------------------------
// Recorder
// ---------------------------------------------------------------------------
async function acquireInputStream(): Promise<MediaStream> {
  if ($audioSource() === "tab") {
    if (!navigator.mediaDevices.getDisplayMedia) {
      throw new Error("Tab/system audio capture is not supported in this browser.");
    }
    const displayStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    // We only need audio — stop the video track(s) immediately.
    displayStream.getVideoTracks().forEach((t) => t.stop());
    const audioTracks = displayStream.getAudioTracks();
    if (!audioTracks.length) {
      displayStream.getTracks().forEach((t) => t.stop());
      throw new Error(
        "No audio track was shared. Please check the option to also share tab/system audio when selecting what to share.",
      );
    }
    const stream = new MediaStream(audioTracks);
    // Auto-stop the session if the user clicks the browser's native "Stop sharing" control.
    audioTracks[0].onended = () => {
      if (state !== "idle") stop();
    };
    return stream;
  }
  // Explicit echo cancellation / noise suppression / auto gain control:
  // without this, the mic can pick up the speaker's own TTS playback as
  // echo, which the barge-in VAD then misreads as the user talking over
  // the TTS.
  return getSelectedInputStream({
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  });

}

async function startRecorder(): Promise<void> {
  micStream = await acquireInputStream();
  mediaRecorder = new MediaRecorder(micStream);


  mediaRecorder.ondataavailable = (e: BlobEvent) => {
    if (e.data.size > 0) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(e.data);
      } else if (connectionStatus === "reconnecting" || connectionStatus === "failed") {
        if (pendingAudioBlobs.length >= 100) {
          pendingAudioBlobs.shift();
          pendingAudioOverflowed = true;
        }
        pendingAudioBlobs.push(e.data);
      }
    }
  };

  mediaRecorder.start(100);

  // Barge-in only makes sense for microphone input: with tab/system audio,
  // the "loud" signal we'd be listening to is the source audio itself, not
  // the user's voice, so it would immediately (and repeatedly) self-trigger.
  if ($barge.checked && $audioSource() === "microphone") startBargeVad(micStream);
}

// ---------------------------------------------------------------------------
// Audio playback (Web Audio API)
// ---------------------------------------------------------------------------
function playPcmChunk(chunk: Uint8Array): void {
  if (!audioCtx) return;
  const evenLen = chunk.byteLength - (chunk.byteLength % 2);
  const int16 = new Int16Array(chunk.buffer as ArrayBuffer, chunk.byteOffset, evenLen / 2);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
  const buffer = audioCtx.createBuffer(1, float32.length, TTS_SAMPLE_RATE);
  buffer.getChannelData(0).set(float32);
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  const startAt = Math.max(audioCtx.currentTime, nextPlayTime);
  source.start(startAt);
  nextPlayTime = startAt + buffer.duration;
  activeSources.push(source);
  source.onended = () => {
    const i = activeSources.indexOf(source);
    if (i !== -1) activeSources.splice(i, 1);
  };
}

function interruptTtsAudio(): void {
  for (const s of activeSources) {
    try { s.stop(); } catch { /* already stopped */ }
  }
  activeSources = [];
  if (audioCtx) nextPlayTime = audioCtx.currentTime;
}

function handleTtsAudio(chunk: Uint8Array): void {
  playPcmChunk(chunk);
  if (state !== "playing-file" || !fileAudio || fileTtsHeard) return;
  fileTtsHeard = true;
  fileAudio.volume = 0.1;
}

// ---------------------------------------------------------------------------
// Barge-in VAD
// ---------------------------------------------------------------------------
function startBargeVad(stream: MediaStream): void {
  const vadCtx = new AudioContext();
  const source = vadCtx.createMediaStreamSource(stream);
  const analyser = vadCtx.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.4;
  source.connect(analyser);
  bargeAnalyser = analyser;
  bargeArray = new Uint8Array(new ArrayBuffer(analyser.fftSize));
  $bargeMeter.classList.remove("hidden");
  bargeHoldSince = 0;
  bargeArmed = false;
  bargeTtsStartedAt = 0;
  wasTtsAudible = false;
  tickBarge();
}

function tickBarge(): void {
  if (!bargeAnalyser || !bargeArray) return;
  bargeAnalyser.getByteTimeDomainData(bargeArray);
  let sum = 0;
  for (let i = 0; i < bargeArray.length; i++) {
    const v = (bargeArray[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / bargeArray.length);
  const pct = Math.min(100, Math.round(rms * 200));
  $bargeBar.style.width = `${pct}%`;

  const now = performance.now();
  const ttsAudible =
    activeSources.length > 0 || (state === "playing-file" && fileAudio != null && !fileAudio.paused);

  // A new TTS chunk just started playing after silence: remember when, so we
  // can suppress barge-in for a short grace window (the onset "pop" of TTS
  // audio can be picked up by the mic as echo and misread as the user
  // talking over the TTS).
  if (ttsAudible && !wasTtsAudible) bargeTtsStartedAt = now;
  wasTtsAudible = ttsAudible;
  const withinStartGrace = now - bargeTtsStartedAt < BARGE_TTS_START_GRACE_MS;

  if (rms > BARGE_RMS_THRESHOLD && ttsAudible && !withinStartGrace) {
    if (bargeHoldSince === 0) bargeHoldSince = now;
    if (now - bargeHoldSince >= BARGE_HOLD_MS && !bargeArmed) {
      bargeArmed = true;
      $bargeBar.classList.add("armed");
      triggerBargeIn();
    }
  } else if (rms <= BARGE_RMS_THRESHOLD * 0.6 || withinStartGrace) {
    bargeHoldSince = 0;
    if (bargeArmed) {
      bargeArmed = false;
      $bargeBar.classList.remove("armed");
    }
  }

  bargeRaf = requestAnimationFrame(tickBarge);
}

function triggerBargeIn(): void {
  interruptTtsAudio();
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({ type: "barge" }));
    } catch { /* ws closed */ }
  }
  setStatus("Barge-in: interrupted TTS");
}

function stopBargeVad(): void {
  if (bargeRaf) cancelAnimationFrame(bargeRaf);
  bargeRaf = null;
  bargeAnalyser = null;
  bargeArray = null;
  $bargeMeter.classList.add("hidden");
  $bargeBar.classList.remove("armed");
  bargeArmed = false;
  bargeHoldSince = 0;
}

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------
async function resetSession(): Promise<void> {
  if (audioCtx && audioCtx.state !== "closed") {
    await audioCtx.close().catch(() => undefined);
  }
  const nextAudioCtx = new AudioContext({ sampleRate: TTS_SAMPLE_RATE });
  audioCtx = nextAudioCtx;
  const devId = $outputDevice.value;
  try {
    await setAudioOutputDevice(nextAudioCtx, devId);
  } catch (error) {
    $outputDevice.value = "default";
    saveDeviceId(OUTPUT_DEVICE_KEY, "default");
    await setAudioOutputDevice(nextAudioCtx, "default").catch(() => undefined);
    setStatus(`Selected speaker unavailable; switched to System Default (${(error as Error).message})`);
  }
  nextPlayTime = 0;
  utterances = [];
  currentUtt = newUtt();
  activeSources = [];
  render();
}

async function start(): Promise<void> {
  setState("recording");
  manualStopRequested = false;

  try {
    await resetSession();
    setConnectionStatus("connected");
    await openWebSocket();
    await startRecorder();
  } catch (err) {
    console.error(err);
    setState("idle", `Failed to start: ${(err as Error).message}`);
    cleanup();
  }
}


async function playFile(): Promise<void> {
  const url = $audioUrl.value.trim();
  if (!url) {
    setStatus("Enter an audio URL");
    return;
  }

  setState("playing-file");
  fileTtsHeard = false;

  try {
    await resetSession();
    fileAudio = new Audio(url);
    fileAudio.volume = 1.0;
    await new Promise<void>((resolve, reject) => {
      fileAudio!.addEventListener("loadedmetadata", () => resolve(), { once: true });
      fileAudio!.addEventListener("error", () => reject(new Error("audio load failed")), { once: true });
    });

    await openWebSocket({
      audio_url: url,
      audio_duration: String(fileAudio.duration),
    });

    await fileAudio.play();
  } catch (err) {
    console.error(err);
    setState("idle", `Failed to play file: ${(err as Error).message}`);
    cleanup();
  }
}


function stop(): void {
  manualStopRequested = true;
  setState("idle");
  setConnectionStatus("idle");
  cleanup();
}

function cleanup(): void {
  // Auto-save completed utterances to session history
  saveToHistory([...utterances, currentUtt], $mode(), $targetLang.value);

  pendingAudioBlobs = [];
  pendingAudioOverflowed = false;

  sendTranscriptSnapshot();

  stopBargeVad();
  if (mediaRecorder) {
    if (mediaRecorder.state !== "inactive") {
      try { mediaRecorder.stop(); } catch { /* already stopped */ }
    }
    mediaRecorder.stream?.getTracks().forEach((t) => t.stop());
  }
  mediaRecorder = null;
  micStream = null;
  if (fileAudio) {
    fileAudio.pause();
    fileAudio = null;
  }
  interruptTtsAudio();
  if (ws) {
    try { ws.close(); } catch { /* already closed */ }
    ws = null;
  }
}

function setState(s: AppState, message?: string): void {
  state = s;
  const busy = s !== "idle";
  if (busy) {
    $actionLabel.textContent = "Stop";
    $actionBtn.dataset.state = "running";
    $actionBtn.disabled = false;
  } else {
    $actionBtn.dataset.state = "idle";
    $actionLabel.textContent = mode === "file" ? "Play audio file" : "Start talking";
    $actionBtn.disabled = mode === "file" && !$audioUrl.value.trim();
  }
  $modeToggle.disabled = busy;
  $audioUrl.disabled = busy;
  $targetLang.disabled = busy;
  $langA.disabled = busy;
  $langB.disabled = busy;
  $voice.disabled = busy;
  $voiceB.disabled = busy;
  $diarization.disabled = busy;
  $langId.disabled = busy;
  $tts.disabled = busy;
  $inputDevice.disabled = busy;
  $outputDevice.disabled = busy;
  $btnTestInput.disabled = busy;
  $btnTestOutput.disabled = busy;
  document.querySelectorAll<HTMLInputElement>("input[name=mode]").forEach((r) => (r.disabled = busy));
  document.querySelectorAll<HTMLInputElement>("input[name=audio-source]").forEach((r) => (r.disabled = busy));
  syncBargeAvailability();
  if (message !== undefined) setStatus(message);


  else if (s === "recording") setStatus("Listening…");
  else if (s === "playing-file") setStatus("Playing audio…");
  else setStatus("Ready");
}


// Barge-in only applies to Microphone input (see startRecorder). Keep the
// checkbox/meter in sync with the selected audio source: disable + uncheck
// it for Tab/System audio, and simply re-enable (without forcing it back on)
// for Microphone.
function syncBargeAvailability(): void {
  const isTab = $audioSource() === "tab";
  if (isTab) {
    $barge.checked = false;
    $barge.disabled = true;
    $bargeHint.classList.remove("hidden");
    $bargeMeter.classList.add("hidden");
  } else {
    $barge.disabled = state !== "idle";
    $bargeHint.classList.add("hidden");
  }
}

document.querySelectorAll<HTMLInputElement>("input[name=audio-source]").forEach((r) =>
  r.addEventListener("change", syncBargeAvailability),
);

function setMode(m: AppMode): void {
  mode = m;
  $actionRow.dataset.mode = m;
  $audioSourceBlock.classList.toggle("hidden", m !== "mic");
  if (state === "idle") setState("idle");
  syncBargeAvailability();
}


function setStatus(msg: string): void {
  $status.textContent = msg;
}

function setConnectionStatus(s: ConnectionStatus): void {
  connectionStatus = s;
  $connectionDot.classList.toggle("hidden", s === "idle");
  $connectionDot.classList.toggle("green", s === "connected");
  $connectionDot.classList.toggle("yellow", s === "reconnecting");
  $connectionDot.classList.toggle("red", s === "failed");
  $btnRetry.classList.toggle("hidden", s !== "failed");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderUtterance(u: Utterance, col: HTMLDivElement, side: "original" | "translation"): void {
  const final = u[`${side}Final`];
  const partial = u[`${side}Partial`];
  if (!final && !partial) return;

  const div = document.createElement("div");
  div.className = "utterance";

  const labels: string[] = [];
  if (side === "original") {
    if ($diarization.checked && u.speaker != null) labels.push(`Speaker ${u.speaker}`);
    if ($langId.checked && u.language) labels.push(u.language);
  } else if ($langId.checked) {
    if ($mode() === "one_way") labels.push($targetLang.value);
    else labels.push(u.language ? otherLang(u.language) : $langB.value);
  }
  if (labels.length) {
    const lbl = document.createElement("div");
    lbl.className = "label";
    lbl.textContent = labels.join(" · ");
    div.appendChild(lbl);
  }

  if (final) {
    const finalSpan = document.createElement("span");
    finalSpan.textContent = final;
    div.appendChild(finalSpan);
  }
  if (partial) {
    const partialSpan = document.createElement("span");
    partialSpan.className = "partial";
    partialSpan.textContent = partial;
    div.appendChild(partialSpan);
  }

  col.appendChild(div);
}

function otherLang(spoken: string): string {
  if (spoken === $langA.value) return $langB.value;
  if (spoken === $langB.value) return $langA.value;
  return spoken || "";
}

function render(): void {
  $originalCol.innerHTML = "";
  $translationCol.innerHTML = "";
  const all = [...utterances, currentUtt];
  for (const u of all) {
    renderUtterance(u, $originalCol, "original");
    renderUtterance(u, $translationCol, "translation");
  }
  syncRowHeights();
  $originalCol.scrollTop = $originalCol.scrollHeight;
  $translationCol.scrollTop = $translationCol.scrollHeight;
  refreshDownloadButtons();
}

function syncRowHeights(): void {
  const o = $originalCol.children;
  const t = $translationCol.children;
  for (const el of o) (el as HTMLElement).style.minHeight = "";
  for (const el of t) (el as HTMLElement).style.minHeight = "";
  const n = Math.min(o.length, t.length);
  for (let i = 0; i < n; i++) {
    const h = Math.max((o[i] as HTMLElement).offsetHeight, (t[i] as HTMLElement).offsetHeight);
    (o[i] as HTMLElement).style.minHeight = `${h}px`;
    (t[i] as HTMLElement).style.minHeight = `${h}px`;
  }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
$actionBtn.addEventListener("click", () => {
  if (state !== "idle") {
    stop();
  } else if (mode === "file") {
    void playFile();
  } else {
    void start();
  }
});

$modeToggle.addEventListener("click", () => {
  if (state === "idle") setMode(mode === "file" ? "mic" : "file");
});

$audioUrl.addEventListener("input", () => {
  if (state === "idle" && mode === "file") {
    $actionBtn.disabled = !$audioUrl.value.trim();
  }
});

setMode("file");

// ---------------------------------------------------------------------------
// Transcript download (client-side, no PII sent back)
// ---------------------------------------------------------------------------
function collectFinalUtterances(): Utterance[] {
  return [...utterances, currentUtt].filter(
    (u) => u.originalFinal || u.translationFinal || u.originalPartial || u.translationPartial,
  );
}

function transcriptTimestamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadTranscriptJson(): void {
  const items = collectFinalUtterances();
  if (!items.length) {
    setStatus("Nothing to save yet");
    return;
  }
  const payload = {
    session_id: sessionId,
    mode: $mode(),
    saved_at: new Date().toISOString(),
    utterances: items.map((u) => ({
      speaker: u.speaker,
      language: u.language,
      original: u.originalFinal,
      translation: u.translationFinal,
    })),
  };
  downloadBlob(
    new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
    `transcript-${transcriptTimestamp()}.json`,
  );
  setStatus("Saved JSON transcript");
}

function csvEscape(value: unknown): string {
  if (value == null) return "";
  const s = String(value);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function downloadTranscriptCsv(): void {
  const items = collectFinalUtterances();
  if (!items.length) {
    setStatus("Nothing to save yet");
    return;
  }
  const header = ["#", "speaker", "language", "original", "translation"];
  const rows = items.map((u, i) => [
    i + 1,
    u.speaker ?? "",
    u.language ?? "",
    u.originalFinal ?? "",
    u.translationFinal ?? "",
  ]);
  const csv = [header, ...rows].map((r) => r.map(csvEscape).join(",")).join("\r\n");
  downloadBlob(
    new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" }),
    `transcript-${transcriptTimestamp()}.csv`,
  );
  setStatus("Saved CSV transcript");
}

function refreshDownloadButtons(): void {
  const has = collectFinalUtterances().length > 0;
  $dlJson.disabled = !has;
  $dlCsv.disabled = !has;
}

$dlJson.addEventListener("click", downloadTranscriptJson);
$dlCsv.addEventListener("click", downloadTranscriptCsv);

// ---------------------------------------------------------------------------
// Dark mode
// ---------------------------------------------------------------------------
function applyTheme(theme: string): void {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("soniox-theme", theme); } catch { /* storage disabled */ }
}

(function initTheme(): void {
  let saved: string | null;
  try { saved = localStorage.getItem("soniox-theme"); } catch { saved = null; }
  if (!saved) saved = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(saved);
})();

$themeToggle.addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  applyTheme(cur === "dark" ? "light" : "dark");
});

// ---------------------------------------------------------------------------
// Expose globals for inline HTML onclick handlers
// ---------------------------------------------------------------------------
(window as unknown as Record<string, unknown>)["toggleHistoryPanel"] = toggleHistoryPanel;
(window as unknown as Record<string, unknown>)["renderHistoryPanel"] = renderHistoryPanel;
(window as unknown as Record<string, unknown>)["sessionHistory"] = sessionHistory;

// ---------------------------------------------------------------------------
// PWA Service Worker
// ---------------------------------------------------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((r) => console.log("[SW] registered:", r.scope))
      .catch((err) => console.warn("[SW] registration failed:", err));
  });
}
