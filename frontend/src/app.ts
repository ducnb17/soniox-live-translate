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
import {
  cleanupConversations,
  deleteConversation,
  fetchConversation,
  fetchConversationExport,
  fetchConversationPage,
  fetchRetentionStats,
  type ConversationExportFormat,
  type ConversationSummary,
} from "./conversation-api";
import { addTtsUsage, emptyTtsUsage, formatTtsCostHint } from "./tts-usage";
import { resolveTtsChunkSchedule } from "./tts-playback";
import { StrictLineAudioQueue } from "./tts-line-queue";
import { SttSessionController } from "./stt-session";
import { TtsSessionController, type TtsRuntimeState } from "./tts-session";


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
const $barge = $<HTMLInputElement>("barge");
const $bargeHint = $<HTMLParagraphElement>("barge-hint");
const $contextJson = $<HTMLTextAreaElement>("context-json");
const $actionRow = document.querySelector<HTMLDivElement>(".action-row")!;
const $actionBtn = $<HTMLButtonElement>("action");
const $actionLabel = $actionBtn.querySelector<HTMLSpanElement>(".btn-label")!;
const $actionTtsBtn = $<HTMLButtonElement>("action-tts");
const $modeToggle = $<HTMLButtonElement>("mode-toggle");
const $audioUrl = $<HTMLInputElement>("audio-url");
const $transcriptFeed = $<HTMLDivElement>("transcript-feed");
const $status = $<HTMLSpanElement>("status");
const $delayStatus = $<HTMLSpanElement>("delay-status");
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
const $sttDelay = $<HTMLInputElement>("stt-delay-seconds");
const $sttDelayValue = $<HTMLSpanElement>("stt-delay-seconds-value");
const $ttsDelay = $<HTMLInputElement>("tts-delay-seconds");
const $ttsDelayValue = $<HTMLSpanElement>("tts-delay-seconds-value");
const $ttsPlaybackRate = $<HTMLInputElement>("tts-playback-rate");
const $ttsPlaybackRateValue = $<HTMLSpanElement>("tts-playback-rate-value");
const $ttsApiKey = $<HTMLInputElement>("tts-api-key");
const $ttsApiKeyRow = $<HTMLDivElement>("tts-api-key-row");
const $btnSaveTtsKey = $<HTMLButtonElement>("btn-save-tts-key");
const $ttsCostHint = $<HTMLParagraphElement>("tts-cost-hint");
const $ttsTierBadge = $<HTMLSpanElement>("tts-tier-badge");
const $ttsProviderDescription = $<HTMLSpanElement>("tts-provider-description");
const $ttsTestStatus = $<HTMLSpanElement>("tts-test-status");
const $ttsKeyLink = $<HTMLAnchorElement>("tts-key-link");
const $sttProvider = $<HTMLSelectElement>("stt-provider-select");
const $sttApiKey = $<HTMLInputElement>("stt-api-key");
const $sttApiKeyRow = $<HTMLDivElement>("stt-api-key-row");
const $btnTestSttKey = $<HTMLButtonElement>("btn-test-stt-key");
const $sttTestStatus = $<HTMLSpanElement>("stt-test-status");
const $sttTierBadge = $<HTMLSpanElement>("stt-tier-badge");
const $sttProviderDescription = $<HTMLSpanElement>("stt-provider-description");
const $sttKeyLink = $<HTMLAnchorElement>("stt-key-link");
const $translationProvider = $<HTMLSelectElement>("translation-provider-select");
const $translationApiKey = $<HTMLInputElement>("translation-api-key");
const $translationApiKeyRow = $<HTMLDivElement>("translation-api-key-row");
const $btnTestTranslationKey = $<HTMLButtonElement>("btn-test-translation-key");
const $translationTestStatus = $<HTMLSpanElement>("translation-test-status");
const $translationTierBadge = $<HTMLSpanElement>("translation-tier-badge");
const $translationProviderDescription = $<HTMLSpanElement>("translation-provider-description");
const $translationKeyLink = $<HTMLAnchorElement>("translation-key-link");
const $historyPanel = $<HTMLDivElement>("history-panel");
const $historyList = $<HTMLDivElement>("history-list");
const $historySearch = $<HTMLInputElement>("history-search");
const $historySearchButton = $<HTMLButtonElement>("history-search-button");
const $historyLoadMore = $<HTMLButtonElement>("history-load-more");
const $retentionDays = $<HTMLInputElement>("retention-days");
const $retentionCleanup = $<HTMLButtonElement>("retention-cleanup");
const $retentionStats = $<HTMLSpanElement>("retention-stats");

function moveSettingsContent(): void {
  const general = $<HTMLElement>("settings-general");
  const ttsPanel = $<HTMLElement>("settings-tts");
  const display = $<HTMLElement>("settings-display");
  for (const id of [
    "translation-mode-block", "one-way-block", "two-way-block",
    "general-options-block", "device-block", "context-block", "audio-url-block",
  ]) general.appendChild($(id));
  const footer = document.querySelector<HTMLElement>(".sidebar-footer");
  if (footer) general.appendChild(footer);
  for (const id of ["voice-a-block", "voice-b-block", "tts-provider-block"]) {
    ttsPanel.appendChild($(id));
  }
  for (const id of ["session-info", "download-block"]) display.appendChild($(id));
  const history = document.querySelector<HTMLElement>(".history-section");
  if (history) display.appendChild(history);
  document.getElementById("settings-staging")?.remove();
}

moveSettingsContent();

document.querySelectorAll<HTMLButtonElement>("[data-settings-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.settingsTab;
    document.querySelectorAll<HTMLElement>("[data-settings-tab]").forEach((item) => {
      item.classList.toggle("active", item.dataset.settingsTab === tab);
    });
    document.querySelectorAll<HTMLElement>("[data-settings-panel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.settingsPanel === tab);
    });
  });
});

document.querySelectorAll<HTMLButtonElement>("[data-secret-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.secretTarget || "") as HTMLInputElement | null;
    if (input) input.type = input.type === "password" ? "text" : "password";
  });
});

void fetch("/api/version")
  .then((response) => response.json())
  .then((data: { version?: string }) => {
    $("app-version").textContent = data.version || "unknown";
  })
  .catch(() => { $("app-version").textContent = "unknown"; });

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
interface TtsProviderInfo {
  id: string;
  name: string;
  description: string;
  requires_api_key: boolean;
  tier: "free" | "cheap" | "premium";
  pricing_url: string;
  approximate_cost_per_1m_chars: number;
  has_api_key: boolean;
}

interface ProviderInfo {
  id: string;
  name: string;
  description: string;
  requires_api_key: boolean;
  tier: "free" | "cheap" | "premium";
  pricing_url: string;
  signup_url?: string;
  has_api_key: boolean;
}

let ttsProviders: TtsProviderInfo[] = [];
let currentTtsProvider = "soniox";
let ttsSessionUsage = emptyTtsUsage();
// Versioned so this release migrates the former 1.5 s default to Soniox's
// low-latency 500 ms endpoint recommendation without touching other settings.
const STT_DELAY_SECONDS_KEY = "sttDelaySecondsRtV1";
const TTS_DELAY_SECONDS_KEY = "ttsDelaySeconds";
const TTS_PLAYBACK_RATE_KEY = "ttsPlaybackRate";

function readSttDelaySeconds(): number {
  let saved: string | null = null;
  try { saved = localStorage.getItem(STT_DELAY_SECONDS_KEY); } catch { /* storage disabled */ }
  const value = Number(saved ?? "0.5");
  return Number.isFinite(value) && value >= 0.5 && value <= 10 ? value : 0.5;
}

function updateSttDelaySelection(): void {
  $sttDelayValue.textContent = $sttDelay.value;
  try { localStorage.setItem(STT_DELAY_SECONDS_KEY, $sttDelay.value); } catch { /* storage disabled */ }
  updateDelayStatusIndicator();
}

function currentSttDelaySeconds(): number {
  const value = $sttDelay.valueAsNumber;
  return Number.isFinite(value) && value >= 0.5 && value <= 10 ? value : 0.5;
}

function readTtsDelaySeconds(): number {
  let saved: string | null = null;
  try { saved = localStorage.getItem(TTS_DELAY_SECONDS_KEY); } catch { /* storage disabled */ }
  const value = Number(saved ?? "0");
  return Number.isFinite(value) && value >= 0 && value <= 10 ? value : 0;
}

function updateTtsDelaySelection(): void {
  $ttsDelayValue.textContent = $ttsDelay.value;
  try { localStorage.setItem(TTS_DELAY_SECONDS_KEY, $ttsDelay.value); } catch { /* storage disabled */ }
  updateDelayStatusIndicator();
}

function currentTtsDelaySeconds(): number {
  const value = $ttsDelay.valueAsNumber;
  return Number.isFinite(value) && value >= 0 && value <= 10 ? value : 0;
}

function readTtsPlaybackRate(): number {
  let saved: string | null = null;
  try { saved = localStorage.getItem(TTS_PLAYBACK_RATE_KEY); } catch { /* storage disabled */ }
  const value = Number(saved ?? "1");
  return Number.isFinite(value) && value >= 0.25 && value <= 2 ? value : 1;
}

function currentTtsPlaybackRate(): number {
  const value = $ttsPlaybackRate.valueAsNumber;
  return Number.isFinite(value) && value >= 0.25 && value <= 2 ? value : 1;
}

function formatTtsPlaybackRate(value: number): string {
  return `${value.toFixed(2).replace(/0$/, "")}x`;
}

function updateTtsPlaybackRateSelection(): void {
  $ttsPlaybackRateValue.textContent = formatTtsPlaybackRate($ttsPlaybackRate.valueAsNumber);
  try { localStorage.setItem(TTS_PLAYBACK_RATE_KEY, $ttsPlaybackRate.value); } catch { /* storage disabled */ }
}

$sttDelay.value = String(readSttDelaySeconds());
$sttDelayValue.textContent = $sttDelay.value;
$sttDelay.addEventListener("input", updateSttDelaySelection);
$ttsDelay.value = String(readTtsDelaySeconds());
$ttsDelayValue.textContent = $ttsDelay.value;
$ttsDelay.addEventListener("input", updateTtsDelaySelection);
$ttsPlaybackRate.value = String(readTtsPlaybackRate());
$ttsPlaybackRateValue.textContent = formatTtsPlaybackRate($ttsPlaybackRate.valueAsNumber);
$ttsPlaybackRate.addEventListener("input", updateTtsPlaybackRateSelection);

function updateTtsCostHint(): void {
  const provider = ttsProviders.find((item) => item.id === currentTtsProvider);
  if (!provider) {
    $ttsCostHint.textContent = "";
    return;
  }
  $ttsCostHint.textContent = formatTtsCostHint(
    provider.approximate_cost_per_1m_chars,
    ttsSessionUsage,
  );
}

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
    await onTtsProviderChange(cfg.current_voice || "");
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

async function onTtsProviderChange(savedVoice = ""): Promise<void> {
  const pid = $ttsProvider.value;
  currentTtsProvider = pid;
  const provider = ttsProviders.find(p => p.id === pid);

  // Show/hide API key row
  $ttsApiKeyRow.style.display = "block";
  $ttsApiKeyRow.classList.toggle("provider-no-key", !provider?.requires_api_key);
  $ttsTierBadge.textContent = provider?.tier || "";
  $ttsTierBadge.dataset.tier = provider?.tier || "";
  $ttsProviderDescription.textContent = provider?.description || "";
  $ttsKeyLink.href = provider?.pricing_url || "#";

  updateTtsCostHint();

  // Load voices
  await loadTtsVoices(pid, savedVoice);
}

async function loadTtsVoices(providerId: string, savedVoice = ""): Promise<void> {
  $ttsVoice.innerHTML = '<option value="">Loading...</option>';
  try {
    const lang = $mode() === "two_way" ? $langB.value : $targetLang.value;
    const resp = await fetch(`/api/tts/providers/${providerId}/voices?lang=${lang}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const voices = await resp.json() as Array<{ id: string; name: string }>;
    $ttsVoice.innerHTML = "";
    for (const v of voices) {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = v.name;
      $ttsVoice.appendChild(opt);
    }
    if (savedVoice && voices.some((voice) => voice.id === savedVoice)) {
      $ttsVoice.value = savedVoice;
    }
  } catch {
    $ttsVoice.innerHTML = '<option value="">Error loading</option>';
  }
}

async function saveTtsSelection(): Promise<void> {
  const response = await fetch("/api/tts/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_id: $ttsProvider.value, voice: $ttsVoice.value }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}

$ttsProvider.addEventListener("change", () => {
  void (async () => {
    await onTtsProviderChange();
    await saveTtsSelection();
  })().catch((error: unknown) => setStatus(`Không thể lưu TTS provider: ${(error as Error).message}`));
});

$ttsVoice.addEventListener("change", () => {
  void saveTtsSelection().catch((error: unknown) => {
    setStatus(`Không thể lưu TTS voice: ${(error as Error).message}`);
  });
});

$btnSaveTtsKey.addEventListener("click", async () => {
  const pid = $ttsProvider.value;
  const key = $ttsApiKey.value.trim();
  const provider = ttsProviders.find((item) => item.id === pid);
  if (!key && provider?.requires_api_key) {
    $ttsTestStatus.textContent = "❌ API key is required";
    return;
  }
  try {
    $ttsTestStatus.textContent = "Testing…";
    const response = await fetch(`/api/tts/providers/${pid}/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    const result = await response.json() as { ok: boolean; message: string };
    $ttsTestStatus.textContent = `${result.ok ? "✅" : "❌"} ${result.message}`;
    if (result.ok) {
      setStatus("TTS connection verified and key saved");
      $ttsApiKey.value = "";
      await loadTtsProviders();
    }
  } catch (error) {
    $ttsTestStatus.textContent = `❌ ${(error as Error).message}`;
  }
});

let sttProviders: ProviderInfo[] = [];
let translationProviders: ProviderInfo[] = [];

function populateProviderSelect(select: HTMLSelectElement, providers: ProviderInfo[]): void {
  select.innerHTML = "";
  for (const provider of providers) {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = `${provider.name}${provider.has_api_key ? " [key]" : ""}`;
    select.appendChild(option);
  }
}

function showProviderMeta(
  select: HTMLSelectElement,
  providers: ProviderInfo[],
  keyRow: HTMLElement,
  badge: HTMLElement,
  description: HTMLElement,
  link: HTMLAnchorElement,
): void {
  const provider = providers.find((item) => item.id === select.value);
  keyRow.classList.remove("hidden");
  keyRow.classList.toggle("provider-no-key", !provider?.requires_api_key);
  badge.textContent = provider?.tier || "";
  badge.dataset.tier = provider?.tier || "";
  description.textContent = provider?.description || "";
  link.href = provider?.signup_url || provider?.pricing_url || "#";
}

async function loadDomainProviders(
  domain: "stt" | "translation",
  select: HTMLSelectElement,
): Promise<ProviderInfo[]> {
  const [providersResponse, configResponse] = await Promise.all([
    fetch(`/api/${domain}/providers`),
    fetch(`/api/${domain}/config`),
  ]);
  if (!providersResponse.ok || !configResponse.ok) throw new Error(`Could not load ${domain} providers`);
  const providers = await providersResponse.json() as ProviderInfo[];
  const config = await configResponse.json() as { current_provider?: string };
  populateProviderSelect(select, providers);
  select.value = config.current_provider || "soniox";
  return providers;
}

async function testDomainProvider(
  domain: "stt" | "translation",
  select: HTMLSelectElement,
  input: HTMLInputElement,
  status: HTMLElement,
): Promise<void> {
  status.textContent = "Testing…";
  try {
    const response = await fetch(`/api/${domain}/providers/${select.value}/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: input.value.trim() }),
    });
    const result = await response.json() as { ok: boolean; message: string };
    status.textContent = `${result.ok ? "✅" : "❌"} ${result.message}`;
    if (result.ok) {
      input.value = "";
      setStatus(`${domain === "stt" ? "STT" : "Translation"} connection verified and key saved`);
    }
  } catch (error) {
    status.textContent = `❌ ${(error as Error).message}`;
  }
}

async function loadSpeechProviders(): Promise<void> {
  try {
    sttProviders = await loadDomainProviders("stt", $sttProvider);
    showProviderMeta($sttProvider, sttProviders, $sttApiKeyRow, $sttTierBadge, $sttProviderDescription, $sttKeyLink);
    translationProviders = await loadDomainProviders("translation", $translationProvider);
    showProviderMeta(
      $translationProvider, translationProviders, $translationApiKeyRow,
      $translationTierBadge, $translationProviderDescription, $translationKeyLink,
    );
  } catch {
    window.setTimeout(() => { void loadSpeechProviders(); }, 3000);
  }
}

$sttProvider.addEventListener("change", () => {
  $sttTestStatus.textContent = "";
  showProviderMeta($sttProvider, sttProviders, $sttApiKeyRow, $sttTierBadge, $sttProviderDescription, $sttKeyLink);
});
$translationProvider.addEventListener("change", () => {
  $translationTestStatus.textContent = "";
  showProviderMeta(
    $translationProvider, translationProviders, $translationApiKeyRow,
    $translationTierBadge, $translationProviderDescription, $translationKeyLink,
  );
});
$btnTestSttKey.addEventListener("click", () => {
  void testDomainProvider("stt", $sttProvider, $sttApiKey, $sttTestStatus);
});
$btnTestTranslationKey.addEventListener("click", () => {
  void testDomainProvider(
    "translation", $translationProvider, $translationApiKey, $translationTestStatus,
  );
});

void loadSpeechProviders();

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
  if (ttsProviders.length) void loadTtsVoices(currentTtsProvider, $ttsVoice.value);
}

document.querySelectorAll<HTMLInputElement>("input[name=mode]").forEach((r) =>
  r.addEventListener("change", syncMode),
);
syncMode();
$targetLang.addEventListener("change", () => {
  if (ttsProviders.length) void loadTtsVoices(currentTtsProvider, $ttsVoice.value);
});
$langB.addEventListener("change", () => {
  if (ttsProviders.length && $mode() === "two_way") {
    void loadTtsVoices(currentTtsProvider, $ttsVoice.value);
  }
});

const DEFAULT_AUDIO_URL = "https://soniox.com/media/examples/spanish_weather_report.mp3";
$audioUrl.value = new URLSearchParams(location.search).get("audio") || DEFAULT_AUDIO_URL;

// ---------------------------------------------------------------------------
// Conversation history (SQLite REST API)
// ---------------------------------------------------------------------------
const HISTORY_PAGE_SIZE = 10;
const RETENTION_DAYS_KEY = "soniox-retention-days";
let historyItems: ConversationSummary[] = [];
let historyOffset = 0;
let historyHasMore = false;
let historyLoading = false;
let historyQuery = "";
let historyRequestId = 0;

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function readRetentionDays(): number {
  let saved = "30";
  try { saved = localStorage.getItem(RETENTION_DAYS_KEY) || "30"; } catch { /* storage disabled */ }
  const parsed = Number(saved);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 3650 ? parsed : 30;
}

function renderHistoryPanel(): void {
  if (!historyItems.length) {
    $historyList.innerHTML = historyLoading
      ? '<p class="history-empty">Đang tải…</p>'
      : '<p class="history-empty">Không có hội thoại phù hợp.</p>';
  } else {
    $historyList.innerHTML = historyItems.map((entry) => {
      const date = new Date(entry.started_at).toLocaleString("vi-VN", {
        dateStyle: "short",
        timeStyle: "short",
      });
      const arrow = entry.mode === "two_way" ? "↔" : "→";
      const preview = entry.preview ? escapeHtml(entry.preview.slice(0, 100)) : "(chưa có nội dung)";
      return `<div class="history-item">
        <div class="history-meta">
          <span class="history-date">${escapeHtml(date)}</span>
          <span class="history-badge">${arrow}&nbsp;${escapeHtml(entry.target_lang.toUpperCase())}</span>
          <span class="history-count">${entry.segment_count} câu</span>
        </div>
        <div class="history-preview">${preview}</div>
        <div class="history-actions">
          <button class="hbtn hbtn-view" data-id="${escapeHtml(entry.id)}">Xem</button>
          <button class="hbtn hbtn-export" data-format="txt" data-id="${escapeHtml(entry.id)}">TXT</button>
          <button class="hbtn hbtn-export" data-format="srt" data-id="${escapeHtml(entry.id)}">SRT</button>
          <button class="hbtn hbtn-export" data-format="json" data-id="${escapeHtml(entry.id)}">JSON</button>
          <button class="hbtn hbtn-del" data-id="${escapeHtml(entry.id)}">🗑</button>
        </div>
      </div>`;
    }).join("");
  }

  $historyLoadMore.classList.toggle("hidden", !historyHasMore);
  $historyLoadMore.disabled = historyLoading;
  $historySearchButton.disabled = historyLoading;

  $historyList.querySelectorAll<HTMLButtonElement>(".hbtn-view").forEach((button) => {
    button.addEventListener("click", () => { void viewConversation(button.dataset.id || ""); });
  });
  $historyList.querySelectorAll<HTMLButtonElement>(".hbtn-export").forEach((button) => {
    button.addEventListener("click", () => {
      void exportSavedConversation(
        button.dataset.id || "",
        button.dataset.format as ConversationExportFormat,
      );
    });
  });
  $historyList.querySelectorAll<HTMLButtonElement>(".hbtn-del").forEach((button) => {
    button.addEventListener("click", () => { void removeSavedConversation(button.dataset.id || ""); });
  });
}

async function loadHistory(reset: boolean): Promise<void> {
  if (historyLoading && !reset) return;
  const requestId = ++historyRequestId;
  historyLoading = true;
  if (reset) {
    historyItems = [];
    historyOffset = 0;
    historyHasMore = false;
  }
  renderHistoryPanel();
  try {
    const page = await fetchConversationPage(historyQuery, historyOffset, HISTORY_PAGE_SIZE);
    if (requestId !== historyRequestId) return;
    historyItems.push(...page.items);
    historyOffset += page.items.length;
    historyHasMore = page.hasMore;
  } catch (error) {
    if (requestId !== historyRequestId) return;
    setStatus(`Không thể tải lịch sử: ${(error as Error).message}`);
  } finally {
    if (requestId !== historyRequestId) return;
    historyLoading = false;
    renderHistoryPanel();
  }
}

async function viewConversation(id: string): Promise<void> {
  try {
    const conversation = await fetchConversation(id);
    utterances = conversation.segments
      .filter((segment) => segment.is_final === 1)
      .map((segment) => {
        const speaker = segment.speaker_label === null ? Number.NaN : Number(segment.speaker_label);
        return {
          speaker: Number.isFinite(speaker) ? speaker : null,
          language: segment.source_lang,
          originalFinal: segment.original_text,
          originalPartial: "",
          translationFinal: segment.translated_text || "",
          translationPartial: "",
        };
      });
    currentUtt = newUtt();
    render();
    setStatus(`Đã mở hội thoại ${id} (${utterances.length} câu)`);
  } catch (error) {
    setStatus(`Không thể mở hội thoại: ${(error as Error).message}`);
  }
}

async function exportSavedConversation(id: string, format: ConversationExportFormat): Promise<void> {
  try {
    const exported = await fetchConversationExport(id, format);
    downloadBlob(exported.blob, exported.filename);
    setStatus(`Đã tải ${exported.filename}`);
  } catch (error) {
    setStatus(`Export ${format.toUpperCase()} thất bại: ${(error as Error).message}`);
  }
}

async function removeSavedConversation(id: string): Promise<void> {
  if (!window.confirm("Xóa vĩnh viễn hội thoại này?")) return;
  try {
    await deleteConversation(id);
    await loadHistory(true);
    await refreshRetentionStats();
    setStatus("Đã xóa hội thoại");
  } catch (error) {
    setStatus(`Không thể xóa hội thoại: ${(error as Error).message}`);
  }
}

async function refreshRetentionStats(): Promise<void> {
  try {
    const stats = await fetchRetentionStats();
    $retentionStats.textContent = `${stats.conversations} hội thoại · ${stats.segments} câu · ${stats.db_size_mb} MB`;
  } catch {
    $retentionStats.textContent = "Không đọc được thống kê lưu trữ";
  }
}

async function runRetentionCleanup(): Promise<void> {
  const days = Math.max(1, Math.min(3650, Number($retentionDays.value) || 30));
  $retentionDays.value = String(days);
  try { localStorage.setItem(RETENTION_DAYS_KEY, String(days)); } catch { /* storage disabled */ }
  if (!window.confirm(`Xóa các hội thoại đã kết thúc quá ${days} ngày?`)) return;
  $retentionCleanup.disabled = true;
  try {
    const deleted = await cleanupConversations(days);
    await loadHistory(true);
    await refreshRetentionStats();
    setStatus(`Đã dọn ${deleted} hội thoại cũ`);
  } catch (error) {
    setStatus(`Dọn lịch sử thất bại: ${(error as Error).message}`);
  } finally {
    $retentionCleanup.disabled = false;
  }
}

function toggleHistoryPanel(): void {
  const button = document.getElementById("btn-history-toggle");
  const open = $historyPanel.classList.toggle("open");
  if (button) button.textContent = open ? "Lịch sử ▲" : "Lịch sử ▼";
  if (open) {
    void loadHistory(true);
    void refreshRetentionStats();
  }
}

$retentionDays.value = String(readRetentionDays());
$historyLoadMore.addEventListener("click", () => { void loadHistory(false); });
$retentionCleanup.addEventListener("click", () => { void runRetentionCleanup(); });
document.getElementById("history-search-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  historyQuery = $historySearch.value.trim();
  void loadHistory(true);
});

// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------
let mode: AppMode = "file";
let state: AppState = "idle";
let mediaRecorder: MediaRecorder | null = null;
let audioCtx: AudioContext | null = null;
const FADE_MS = 8;
let nextPlayTime = 0;
let currentPlayingLineId: number | null = null;
let utterances: Utterance[] = [];
let currentUtt = newUtt();
let fileAudio: HTMLAudioElement | null = null;
let sessionId: string | null = null;
let connectionStatus: ConnectionStatus = "idle";
let pendingAudioBlobs: Blob[] = [];
let pendingAudioOverflowed = false;
let manualStopRequested = false;
let lastWebSocketParams: Record<string, string> = {};
let manualRetryInProgress = false;
let resumeTranscriptOnNextSession = false;
let feedAutoScroll = true;

// Read-mode TTS queue: deliberately unbounded so backlog increases latency,
// never data loss. A line becomes eligible to start streaming as soon as it
// is next in sequence — playback of its chunks starts immediately and keeps
// pace with arrival, instead of waiting for the whole line to be generated.
const lineAudioQueue = new StrictLineAudioQueue<Uint8Array>();
let nextLineIdToPlay: number | null = null;
let activeLineSources: AudioBufferSourceNode[] = [];
let playbackEpoch = 0;
let lastRegisteredLineId = 0;
let minimumAcceptedLineId = 1;
let backendSessionDone = false;
let audioLineReadyCount = 0;
let audioLinePlayedCount = 0;
let interruptedAudioLineCount = 0;
let averageLineAudioSeconds = 3;
// Line ID used purely for Web Audio gap-detection (whether to insert the
// configured inter-line delay). Decoupled from currentPlayingLineId, which
// is the "a line is actively streaming" busy flag.
let lastScheduledLineId: number | null = null;
// Chunks of the active line that have been scheduled for playback but have
// not yet finished playing (onEnded not fired).
let activeLinePendingChunks = 0;
// True once the final chunk of the active line has been scheduled.
let activeLineDoneScheduling = false;
// Running total of the active line's audio duration, for averageLineAudioSeconds.
let activeLineAudioSeconds = 0;
const skippedRealtimeUtterances = new Set<number>();

// Barge-in VAD state
let bargeAnalyser: AnalyserNode | null = null;
let bargeArray: Uint8Array<ArrayBuffer> | null = null;
let bargeRaf: number | null = null;
let bargeHoldSince = 0;
let bargeArmed = false;
let micStream: MediaStream | null = null;
// Timestamp of the most recent empty -> non-empty transition of
// activeLineSources (i.e. a new TTS line started playing after silence).
// Used to suppress barge-in during the initial grace window, since the
// onset "pop" of TTS audio can be picked up by the mic as echo.
let bargeTtsStartedAt = 0;
let wasTtsAudible = false;

const sttSession = new SttSessionController({
  onMessage: handleSttWebSocketMessage,
  onState: (runtimeState, event) => {
    if (runtimeState === "error" && !manualStopRequested) {
      console.log("STT WebSocket closed", event?.code, event?.reason);
    }
  },
});

const ttsSession = new TtsSessionController({
  onState: updateTtsButton,
  onAudio: (audio, meta) => {
    handleTtsAudio(new Uint8Array(audio), meta.line_id, meta.line_audio_end);
  },
  onError: (message) => showTtsErrorBanner(message),
  onReset: () => interruptTtsAudio(),
  onMessage: (message) => {
    if (message.type === "tts_fallback") {
      setStatus(
        `${String(message.from_provider)} TTS failed; using ${String(message.to_provider)}: ` +
        String(message.reason || "unknown error"),
      );
    } else if (message.type === "tts_usage") {
      ttsSessionUsage = addTtsUsage(
        ttsSessionUsage,
        message as unknown as NonNullable<SonioxSttResponse["tts_usage"]>,
      );
      updateTtsCostHint();
    }
  },
});


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
  if (sttSession.isOpen()) {
    for (const blob of pendingAudioBlobs) {
      try { if (!sttSession.sendAudio(blob)) break; } catch { break; }
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
    ...extraParams,
  });
  const sttDelaySeconds = currentSttDelaySeconds();
  params.set("stt_delay_ms", String(Math.round(sttDelaySeconds * 1000)));

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
  params.set("stt_provider", $sttProvider.value || "soniox");
  params.set("translation_provider", $translationProvider.value || "soniox");

  const url = `${proto}//${location.host}/ws/stt?${params}`;
  return sttSession.connect(url);
}

function handleSttWebSocketMessage(event: MessageEvent): void {
    if (typeof event.data !== "string") {
      console.warn("Ignored unexpected binary data on the STT WebSocket");
      return;
    }
    {
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
        sttSession.markReconnecting();
        setConnectionStatus("reconnecting");
        setStatus(`Đang kết nối lại… (lần ${data.attempt}/${data.max_attempts})`);
        return;
      }

      if (data.reconnected) {
        sttSession.markConnected();
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

      if (data.translation_error) {
        setStatus(`Translation failed: ${data.translation_error.message}`);
        return;
      }

      if (data.type === "line_ready") {
        handleLineReady(data);
        return;
      }

      if (data.type === "translation_chunk") {
        handleTranslationChunk(data);
        return;
      }

      if (data.type === "translation_end") {
        handleTranslationEnd(data);
        return;
      }

      if (data.type === "stt_state") return;

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
        backendSessionDone = true;
        maybeFinishSessionAfterAudio();
        return;
      }
      handleSttResult(data);
    }
  }

function sendTranscriptSnapshot(): void {
  if (!sttSession.isOpen()) return;
  const snapshot = [...utterances, currentUtt].filter(
    (u) =>
      u.originalFinal ||
      u.translationFinal ||
      u.originalPartial ||
      u.translationPartial,
  );
  if (!snapshot.length) return;
  try {
    sttSession.sendJson({ type: "utterances", utterances: snapshot });
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
      currentUtt = newUtt();
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

function handleLineReady(data: SonioxSttResponse): void {
  const original = data.original_text || "";
  const translated = data.translated_text || "";
  if (!original && !translated) return;
  const lineId = data.line_id;
  const sentToTts = !usesRealtimeSonioxPipeline()
    && typeof lineId === "number"
    && Boolean(translated)
    && ttsSession.speak({
    requestId: `${sessionId || "session"}:${lineId}`,
    lineId,
    text: translated,
    direction: data.target_lang || data.direction || data.lang || $targetLang.value,
    voice: ttsVoiceForDirection(data.direction || data.target_lang || undefined),
  });
  if (typeof lineId === "number" && sentToTts) {
    registerTtsAudioLine(lineId);
  }
  utterances.push({
    speaker: data.speaker ?? null,
    language: data.lang || null,
    originalFinal: original,
    originalPartial: "",
    translationFinal: translated,
    translationPartial: "",
  });
  if (!data.is_endpoint) currentUtt = newUtt();
  render();
}

function usesRealtimeSonioxPipeline(): boolean {
  return currentTtsProvider === "soniox" && $translationProvider.value === "soniox";
}

function ttsVoiceForDirection(direction?: string): string {
  return currentTtsProvider === "soniox"
    && $mode() === "two_way"
    && direction === $langA.value
    ? $voiceB.value
    : ($ttsVoice.value || $voice.value);
}

function realtimeRequestId(utteranceId: number): string {
  return `${sessionId || "session"}:rt:${utteranceId}`;
}

function handleTranslationChunk(data: SonioxSttResponse): void {
  if (!usesRealtimeSonioxPipeline()) return;
  const utteranceId = data.utterance_id ?? data.line_id;
  const sequence = data.sequence;
  const text = data.text || "";
  const direction = data.direction || data.target_lang || $targetLang.value;
  if (
    typeof utteranceId !== "number"
    || typeof sequence !== "number"
    || !text
    || !direction
  ) return;
  if (skippedRealtimeUtterances.has(utteranceId)) return;
  const result = ttsSession.streamText({
    requestId: realtimeRequestId(utteranceId),
    lineId: utteranceId,
    text,
    direction,
    voice: ttsVoiceForDirection(direction),
    sequence,
  });
  // If the first token arrives before the TTS configure ACK, do not begin
  // speaking halfway through that utterance.  Resume cleanly at the next one.
  if (!result && sequence === 1) {
    skippedRealtimeUtterances.add(utteranceId);
    return;
  }
  if (result === "started") registerTtsAudioLine(utteranceId);
}

function handleTranslationEnd(data: SonioxSttResponse): void {
  const utteranceId = data.utterance_id ?? data.line_id;
  if (typeof utteranceId !== "number") return;
  if (!skippedRealtimeUtterances.has(utteranceId) && usesRealtimeSonioxPipeline()) {
    ttsSession.endStream(realtimeRequestId(utteranceId));
  }
  skippedRealtimeUtterances.delete(utteranceId);
}

function registerTtsAudioLine(lineId: number): void {
  lineAudioQueue.registerLine(lineId);
  lastRegisteredLineId = Math.max(lastRegisteredLineId, lineId);
  audioLineReadyCount += 1;
  if (nextLineIdToPlay === null && currentPlayingLineId === null) {
    nextLineIdToPlay = lineAudioQueue.firstLineId;
  }
  logTtsLineProgress();
  updateDelayStatusIndicator();
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
      if (sttSession.isOpen()) {
        sttSession.sendAudio(e.data);
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
function playPcmChunk(
  chunk: Uint8Array,
  lineId: number,
  onEnded: () => void,
): AudioBufferSourceNode | null {
  if (!audioCtx) return null;
  const evenLen = chunk.byteLength - (chunk.byteLength % 2);
  const int16 = new Int16Array(chunk.buffer as ArrayBuffer, chunk.byteOffset, evenLen / 2);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
  const buffer = audioCtx.createBuffer(1, float32.length, TTS_SAMPLE_RATE);
  buffer.getChannelData(0).set(float32);
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  const playbackRate = currentTtsPlaybackRate();
  // playbackRate thay đổi cả cao độ giọng nói (pitch); muốn giữ nguyên cao độ cần AudioWorklet/time-stretch riêng — chưa làm ở đây.
  source.playbackRate.value = playbackRate;
  const gainNode = audioCtx.createGain();
  source.connect(gainNode);
  gainNode.connect(audioCtx.destination);
  // Gap-detection uses lastScheduledLineId (the last line ID actually handed
  // to Web Audio), which is deliberately decoupled from currentPlayingLineId
  // (the "a line is actively streaming" busy flag) — the busy flag is set
  // *before* the first chunk of a line is scheduled, so comparing against it
  // here would never detect the very first chunk of a new line as new.
  const schedule = resolveTtsChunkSchedule(
    audioCtx.currentTime,
    nextPlayTime,
    lastScheduledLineId,
    lineId,
    currentTtsDelaySeconds(),
  );
  if (schedule.isNewLine) lastScheduledLineId = schedule.currentLineId;
  const startAt = schedule.startAt;
  const playbackDuration = buffer.duration / playbackRate;
  const endAt = startAt + playbackDuration;
  const fadeDuration = Math.min(FADE_MS / 1000, playbackDuration / 2);
  if (fadeDuration > 0) {
    gainNode.gain.setValueAtTime(0, startAt);
    gainNode.gain.linearRampToValueAtTime(1, startAt + fadeDuration);
    gainNode.gain.setValueAtTime(1, endAt - fadeDuration);
    gainNode.gain.linearRampToValueAtTime(0, endAt);
  }
  source.start(startAt);
  // Ensure the AudioContext is running. If it was auto-suspended (browser
  // policy or Electron idle), onended never fires and the queue stalls.
  if (audioCtx.state === "suspended") {
    void audioCtx.resume();
  }
  nextPlayTime = endAt;
  activeLineSources.push(source);
  source.onended = () => {
    const i = activeLineSources.indexOf(source);
    if (i !== -1) activeLineSources.splice(i, 1);
    onEnded();
  };
  return source;
}

function interruptTtsAudio(): void {
  playbackEpoch += 1;
  const interruptedIds = lineAudioQueue.lineCount + (currentPlayingLineId === null ? 0 : 1);
  interruptedAudioLineCount += interruptedIds;
  lineAudioQueue.clear();
  for (const s of activeLineSources) {
    try { s.stop(); } catch { /* already stopped */ }
  }
  activeLineSources = [];
  if (audioCtx) nextPlayTime = audioCtx.currentTime;
  currentPlayingLineId = null;
  activeLinePendingChunks = 0;
  activeLineDoneScheduling = false;
  activeLineAudioSeconds = 0;
  lastScheduledLineId = null;
  minimumAcceptedLineId = lastRegisteredLineId + 1;
  nextLineIdToPlay = minimumAcceptedLineId;
  updateDelayStatusIndicator();
}

function handleTtsAudio(
  chunk: Uint8Array,
  lineId: number,
  lineAudioEnd: boolean,
): void {
  if (lineId < minimumAcceptedLineId) return;
  lineAudioQueue.addChunk(lineId, chunk, lineAudioEnd);
  if (nextLineIdToPlay === null) nextLineIdToPlay = lineAudioQueue.firstLineId;
  streamActiveLine();
  updateDelayStatusIndicator();
}

/**
 * Activate the next queued line (if none is active) and schedule every
 * chunk that has arrived for the active line so far. Unlike the old
 * batch-and-wait approach, this is safe to call every time a single new
 * chunk arrives — playback of a line starts on its first chunk instead of
 * waiting for the whole line to finish generating.
 */
function streamActiveLine(): void {
  if (!audioCtx) return;
  if (currentPlayingLineId === null) {
    if (nextLineIdToPlay === null) nextLineIdToPlay = lineAudioQueue.firstLineId;
    const line = lineAudioQueue.takeReady(nextLineIdToPlay);
    if (!line) return;
    currentPlayingLineId = line.lineId;
    activeLinePendingChunks = 0;
    activeLineDoneScheduling = false;
    activeLineAudioSeconds = 0;
  }
  const lineId = currentPlayingLineId;
  const epoch = playbackEpoch;
  let next = lineAudioQueue.takeNextChunk();
  while (next !== null) {
    const { chunk, isLast } = next;
    // A terminated Soniox stream can carry no final PCM payload.  The
    // backend sends a zero-byte end marker so FIFO state still advances;
    // do not hand that marker to Web Audio (zero-frame buffers are invalid).
    if (chunk.byteLength === 0) {
      if (isLast) activeLineDoneScheduling = true;
      next = lineAudioQueue.takeNextChunk();
      continue;
    }
    activeLinePendingChunks += 1;
    activeLineAudioSeconds += pcmChunkDurationSeconds(chunk);
    playPcmChunk(chunk, lineId, () => {
      if (epoch !== playbackEpoch) return;
      activeLinePendingChunks -= 1;
      maybeFinishActiveLine();
    });
    if (isLast) activeLineDoneScheduling = true;
    next = lineAudioQueue.takeNextChunk();
  }
  maybeFinishActiveLine();
}

/** Once the active line's last chunk has finished playing, retire it and start the next. */
function maybeFinishActiveLine(): void {
  if (currentPlayingLineId === null) return;
  if (!activeLineDoneScheduling || activeLinePendingChunks > 0) return;
  const lineId = currentPlayingLineId;
  const audioSeconds = activeLineAudioSeconds;
  if (!lineAudioQueue.finishLine(lineId)) return;
  audioLinePlayedCount += 1;
  averageLineAudioSeconds = averageLineAudioSeconds * 0.8 + audioSeconds * 0.2;
  currentPlayingLineId = null;
  nextLineIdToPlay = lineAudioQueue.firstLineId ?? lineId + 1;
  logTtsLineProgress();
  updateDelayStatusIndicator();
  streamActiveLine();
  maybeFinishSessionAfterAudio();
}

function pcmChunkDurationSeconds(chunk: Uint8Array): number {
  return chunk.byteLength / 2 / TTS_SAMPLE_RATE / currentTtsPlaybackRate();
}

function maybeFinishSessionAfterAudio(): void {
  if (!backendSessionDone) return;
  if (currentPlayingLineId !== null || lineAudioQueue.lineCount > 0) return;
  logTtsLineProgress(true);
  stop();
}

function logTtsLineProgress(force = false): void {
  const accounted = audioLinePlayedCount + interruptedAudioLineCount;
  const reachedMilestone =
    (audioLineReadyCount > 0 && audioLineReadyCount % 10 === 0) ||
    (accounted > 0 && accounted % 10 === 0);
  if (!force && !reachedMilestone) return;
  const summary =
    `line_ready(audio)=${audioLineReadyCount}, played=${audioLinePlayedCount}, ` +
    `interrupted=${interruptedAudioLineCount}, queued=${lineAudioQueue.lineCount}`;
  if (force && audioLineReadyCount !== accounted) {
    console.error(`[TTS line audit] MISMATCH: ${summary}`);
  } else {
    console.log(`[TTS line audit] ${summary}`);
  }
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
  // activeLineSources includes sources scheduled to start in the future, so
  // barge-in remains available during the configured playback delay.
  const ttsAudible = ttsSession.state === "on" && activeLineSources.length > 0;

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
  ttsSession.cancelAll();
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

function ttsWebSocketUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/tts`;
}

async function toggleTts(): Promise<void> {
  if (ttsSession.desiredEnabled) {
    const retryAfterError = ttsSession.state === "error";
    interruptTtsAudio();
    ttsSession.disable();
    if (!retryAfterError) {
      setStatus(state === "idle" ? "TTS off" : "TTS stopped; STT is still running");
      return;
    }
  }
  try {
    await ttsSession.enable(
      ttsWebSocketUrl(),
      {
        provider: $ttsProvider.value || "soniox",
        voice: $ttsVoice.value || $voice.value,
        voiceB: $voiceB.value,
        mode: $mode(),
        targetLang: $mode() === "one_way" ? $targetLang.value : $langB.value,
        langA: $langA.value,
        langB: $langB.value,
        realtimeStreaming: usesRealtimeSonioxPipeline(),
      },
      state !== "idle" && sttSession.isOpen(),
    );
  } catch (error) {
    showTtsErrorBanner((error as Error).message);
  }
}

function updateTtsButton(runtimeState: TtsRuntimeState): void {
  const label = $actionTtsBtn.querySelector<HTMLSpanElement>(".btn-label-tts")!;
  const labels: Record<TtsRuntimeState, string> = {
    off: "+ Read",
    waiting_for_stt: "Read: waiting for STT",
    connecting: "Read: connecting...",
    on: "Stop reading",
    stopping: "Read: stopping...",
    error: "Read: retry",
  };
  label.textContent = labels[runtimeState];
  $actionTtsBtn.dataset.state = runtimeState;
  $actionTtsBtn.disabled = false;
  const providerLocked = runtimeState === "connecting" || runtimeState === "on" || runtimeState === "stopping";
  $ttsProvider.disabled = providerLocked;
  $ttsVoice.disabled = providerLocked;
  $ttsApiKey.disabled = providerLocked;
  $btnSaveTtsKey.disabled = providerLocked;
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
  currentPlayingLineId = null;
  activeLinePendingChunks = 0;
  activeLineDoneScheduling = false;
  activeLineAudioSeconds = 0;
  lastScheduledLineId = null;
  lineAudioQueue.clear();
  nextLineIdToPlay = null;
  activeLineSources = [];
  playbackEpoch += 1;
  lastRegisteredLineId = 0;
  minimumAcceptedLineId = 1;
  backendSessionDone = false;
  audioLineReadyCount = 0;
  audioLinePlayedCount = 0;
  interruptedAudioLineCount = 0;
  averageLineAudioSeconds = 3;
  utterances = [];
  currentUtt = newUtt();
  feedAutoScroll = true;
  ttsSessionUsage = emptyTtsUsage();
  updateTtsCostHint();
  render();
}

async function start(): Promise<void> {
  setState("recording");
  manualStopRequested = false;

  try {
    await resetSession();
    setConnectionStatus("connected");
    await openWebSocket();
    await ttsSession.onSttStarted();
    await startRecorder();
  } catch (err) {
    if (manualStopRequested) return;
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
  manualStopRequested = false;

  try {
    await resetSession();
    fileAudio = new Audio(url);
    await new Promise<void>((resolve, reject) => {
      fileAudio!.addEventListener("loadedmetadata", () => resolve(), { once: true });
      fileAudio!.addEventListener("error", () => reject(new Error("audio load failed")), { once: true });
    });

    await openWebSocket({
      audio_url: url,
      audio_duration: String(fileAudio.duration),
    });
    await ttsSession.onSttStarted();

    await fileAudio.play();
  } catch (err) {
    if (manualStopRequested) return;
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
  pendingAudioBlobs = [];
  pendingAudioOverflowed = false;
  skippedRealtimeUtterances.clear();

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
  ttsSession.onSttStopped();
  sttSession.close();
  if ($historyPanel.classList.contains("open")) {
    // The backend flushes its final segment batch as the WebSocket session
    // closes. A short delay avoids racing that transaction.
    window.setTimeout(() => { void loadHistory(true); }, 300);
  }
}

function setState(s: AppState, message?: string): void {
  state = s;
  const busy = s !== "idle";
  $actionBtn.dataset.state = busy ? "running" : "idle";
  $actionLabel.textContent = busy
    ? "Stop STT"
    : (mode === "file" ? "Play audio file" : "Start STT");
  $actionBtn.disabled = !busy && mode === "file" && !$audioUrl.value.trim();
  $modeToggle.disabled = busy;
  $audioUrl.disabled = busy;
  $targetLang.disabled = busy;
  $langA.disabled = busy;
  $langB.disabled = busy;
  $voice.disabled = busy;
  $voiceB.disabled = busy;
  $diarization.disabled = busy;
  $langId.disabled = busy;
  $inputDevice.disabled = busy;
  $outputDevice.disabled = busy;
  $btnTestInput.disabled = busy;
  $btnTestOutput.disabled = busy;
  document.querySelectorAll<HTMLInputElement>("input[name=mode]").forEach((r) => (r.disabled = busy));
  document.querySelectorAll<HTMLInputElement>("input[name=audio-source]").forEach((r) => (r.disabled = busy));
  syncBargeAvailability();
  updateTtsButton(ttsSession.state);
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
  updateDelayStatusIndicator();
}

/* ─── TTS error banner (sticky, must be dismissed) ─── */
const $ttsErrorBanner = document.getElementById("tts-error-banner") as HTMLDivElement | null;
const $ttsErrorText = document.querySelector<HTMLSpanElement>(".tts-error-text");
const $ttsErrorClose = document.querySelector<HTMLButtonElement>(".tts-error-close");

function showTtsErrorBanner(message: string): void {
  if (!$ttsErrorBanner || !$ttsErrorText) return;
  $ttsErrorText.textContent = message;
  $ttsErrorBanner.classList.remove("hidden");
}

if ($ttsErrorClose) {
  $ttsErrorClose.addEventListener("click", () => {
    $ttsErrorBanner?.classList.add("hidden");
  });
}

function updateDelayStatusIndicator(): void {
  const scheduledPlaybackSeconds = audioCtx
    ? Math.max(0, nextPlayTime - audioCtx.currentTime)
    : 0;
  const queuedAudioSeconds = lineAudioQueue.estimatedAudioSeconds(
    pcmChunkDurationSeconds,
    averageLineAudioSeconds,
  );
  const queuedLineDelaySeconds = lineAudioQueue.lineCount * currentTtsDelaySeconds();
  const totalDelaySeconds =
    currentSttDelaySeconds() +
    scheduledPlaybackSeconds +
    queuedAudioSeconds +
    queuedLineDelaySeconds;
  const visible = state !== "idle" && totalDelaySeconds > 0;
  const formattedDelay = Number.isInteger(totalDelaySeconds)
    ? String(totalDelaySeconds)
    : totalDelaySeconds.toFixed(1);
  $delayStatus.textContent = visible ? `Đang xử lý (~${formattedDelay}s)…` : "";
  $delayStatus.classList.toggle("hidden", !visible);
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
function hasUtteranceText(u: Utterance): boolean {
  return Boolean(u.originalFinal || u.originalPartial || u.translationFinal || u.translationPartial);
}

function renderFeedLine(u: Utterance, interim = false): void {
  if (!hasUtteranceText(u)) return;
  const speaker = u.speaker ?? 0;
  const line = document.createElement("div");
  line.className = `feed-line speaker-${Math.abs(speaker) % 5}${interim ? " interim" : ""}`;

  const label = document.createElement("div");
  label.className = "speaker-label";
  label.append(`Speaker ${u.speaker ?? "—"}: `);
  const language = document.createElement("span");
  language.className = "lang-tag";
  language.textContent = u.language || "auto";
  label.appendChild(language);
  line.appendChild(label);

  const original = document.createElement("div");
  original.className = "original-text";
  original.textContent = `${u.originalFinal}${u.originalPartial}`;
  line.appendChild(original);

  const translated = document.createElement("div");
  translated.className = "translated-text";
  translated.textContent = `${u.translationFinal}${u.translationPartial}`;
  line.appendChild(translated);
  $transcriptFeed.appendChild(line);
}

function render(): void {
  const previousScrollTop = $transcriptFeed.scrollTop;
  const shouldScroll = feedAutoScroll;
  $transcriptFeed.innerHTML = "";
  for (const utterance of utterances) renderFeedLine(utterance);
  renderFeedLine(currentUtt, true);
  if (shouldScroll) {
    $transcriptFeed.scrollTop = $transcriptFeed.scrollHeight;
  } else {
    $transcriptFeed.scrollTop = previousScrollTop;
  }
  refreshDownloadButtons();
}

$transcriptFeed.addEventListener("scroll", () => {
  const distanceFromBottom =
    $transcriptFeed.scrollHeight - $transcriptFeed.scrollTop - $transcriptFeed.clientHeight;
  feedAutoScroll = distanceFromBottom < 48;
});

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

$actionTtsBtn.addEventListener("click", () => {
  void toggleTts();
});

$modeToggle.addEventListener("click", () => {
  if (state === "idle") setMode(mode === "file" ? "mic" : "file");
});

$audioUrl.addEventListener("input", () => {
  if (state === "idle" && mode === "file") {
    const hasUrl = Boolean($audioUrl.value.trim());
    $actionBtn.disabled = !hasUrl;
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
