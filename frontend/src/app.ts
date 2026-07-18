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
} from "./types";


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
function openWebSocket(extraParams: Record<string, string> = {}): Promise<void> {
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

  const url = `${proto}//${location.host}/ws/translate?${params}`;
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.onmessage = (event: MessageEvent) => {
    if (typeof event.data === "string") {
      const data: SonioxSttResponse = JSON.parse(event.data);
      if (data.session_id) {
        sessionId = data.session_id;
        showSessionInfo(sessionId);
        return;
      }
      if (data.error_code || data.error_message) {
        console.error("Server error:", data.error_code, data.error_message);
        setState("idle", data.error_message || `Server error: ${data.error_code}`);
        cleanup();
        return;
      }
      if (data.barge_ack) return;
      handleSttResult(data);
    } else {
      handleTtsAudio(new Uint8Array(event.data as ArrayBuffer));
    }
  };

  ws.onclose = (event: CloseEvent) => {
    if (state !== "idle") {
      console.warn("WebSocket closed unexpectedly", event.code, event.reason);
      setState(
        "idle",
        `Connection closed unexpectedly (code ${event.code}${event.reason ? ": " + event.reason : ""})`,
      );
      cleanup();
    }
  };

  return new Promise<void>((resolve, reject) => {
    ws!.onopen = () => resolve();
    ws!.onerror = () => reject(new Error("WebSocket error"));
  });
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
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

}

async function startRecorder(): Promise<void> {
  micStream = await acquireInputStream();
  mediaRecorder = new MediaRecorder(micStream);


  mediaRecorder.ondataavailable = (e: BlobEvent) => {
    if (e.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(e.data);
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
function resetSession(): void {
  audioCtx = new AudioContext({ sampleRate: TTS_SAMPLE_RATE });
  nextPlayTime = 0;
  utterances = [];
  currentUtt = newUtt();
  activeSources = [];
  render();
}

async function start(): Promise<void> {
  setState("recording");
  resetSession();

  try {
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
  resetSession();
  fileTtsHeard = false;

  try {
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
  setState("idle");
  cleanup();
}

function cleanup(): void {
  // Auto-save completed utterances to session history
  saveToHistory([...utterances, currentUtt], $mode(), $targetLang.value);

  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      const snapshot = [...utterances, currentUtt].filter(
        (u) =>
          u.originalFinal ||
          u.translationFinal ||
          u.originalPartial ||
          u.translationPartial,
      );
      ws.send(JSON.stringify({ type: "utterances", utterances: snapshot }));
    } catch { /* ws closed */ }
  }

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
