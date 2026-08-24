// popup.js — Soniox Live Translate Chrome Extension
// Web-only mode: STT via browser MediaRecorder + Soniox WebSocket API,
// TTS via Edge TTS (free) or Soniox TTS.

const SONIOX_STT_URL = "wss://stt-rt.soniox.com/transcribe-websocket";
const SONIOX_TTS_URL = "wss://tts-rt.soniox.com/tts-websocket";
const EDGE_TTS_API   = "https://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1";

// ── State ─────────────────────────────────────────────────────────────────
let isRunning   = false;
let mediaStream = null;
let mediaRec    = null;
let sttWs       = null;
let audioCtx    = null;
let apiKey      = "";
let srcLang     = "en";
let tgtLang     = "vi";
let ttsProvider = "edge";
let ttsVoice    = "vi-VN-HoaiMyNeural";

// ── DOM refs ──────────────────────────────────────────────────────────────
const $statusDot  = document.getElementById("statusDot");
const $statusText = document.getElementById("statusText");
const $mainBtn    = document.getElementById("mainBtn");
const $transcript = document.getElementById("transcript");
const $apiKey     = document.getElementById("apiKey");
const $saveKeyBtn = document.getElementById("saveKeyBtn");
const $srcLang    = document.getElementById("srcLang");
const $tgtLang    = document.getElementById("tgtLang");
const $swapBtn    = document.getElementById("swapBtn");
const $ttsProvider = document.getElementById("ttsProvider");
const $ttsVoice   = document.getElementById("ttsVoice");
const $ttsBadge   = document.getElementById("ttsBadge");

// ── Persist settings ──────────────────────────────────────────────────────
async function loadSettings() {
  const s = await chrome.storage.local.get(["apiKey","srcLang","tgtLang","ttsProvider","ttsVoice"]);
  if (s.apiKey)    { $apiKey.value = s.apiKey; apiKey = s.apiKey; }
  if (s.srcLang)   { $srcLang.value = s.srcLang; }
  if (s.tgtLang)   { $tgtLang.value = s.tgtLang; }
  if (s.ttsProvider) { $ttsProvider.value = s.ttsProvider; }
  if (s.ttsVoice)  { $ttsVoice.value = s.ttsVoice; }
  onTtsProviderChange();
  updateStatus("idle");
}

async function saveSettings() {
  await chrome.storage.local.set({
    apiKey: $apiKey.value.trim(),
    srcLang: $srcLang.value,
    tgtLang: $tgtLang.value,
    ttsProvider: $ttsProvider.value,
    ttsVoice: $ttsVoice.value,
  });
}

// ── Status helpers ────────────────────────────────────────────────────────
function updateStatus(state, msg) {
  $statusDot.className = "dot";
  if (state === "active") { $statusDot.classList.add("active"); $statusText.textContent = msg || "Listening…"; }
  else if (state === "error") { $statusDot.classList.add("error"); $statusText.textContent = msg || "Error"; }
  else { $statusText.textContent = msg || "Ready"; }
}

function appendTranscript(original, translated) {
  const existing = $transcript.querySelector("[style*='italic']");
  if (existing) existing.remove();
  const div = document.createElement("div");
  div.innerHTML = `<div class="original">${escHtml(original)}</div><div class="translated">${escHtml(translated)}</div>`;
  $transcript.appendChild(div);
  $transcript.scrollTop = $transcript.scrollHeight;
}

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── TTS provider UI ───────────────────────────────────────────────────────
const VOICES = {
  en: [ ["en-US-JennyNeural","Jenny (Female, US)"], ["en-US-GuyNeural","Guy (Male, US)"],
        ["en-GB-SoniaNeural","Sonia (Female, UK)"], ["en-GB-RyanNeural","Ryan (Male, UK)"] ],
  vi: [ ["vi-VN-HoaiMyNeural","HoaiMy (Female)"], ["vi-VN-NamMinhNeural","NamMinh (Male)"] ],
  ja: [ ["ja-JP-NanamiNeural","Nanami (Female)"], ["ja-JP-KeitaNeural","Keita (Male)"] ],
  zh: [ ["zh-CN-XiaoxiaoNeural","Xiaoxiao (Female)"], ["zh-CN-YunxiNeural","Yunxi (Male)"] ],
  ko: [ ["ko-KR-SunHiNeural","SunHi (Female)"], ["ko-KR-InJoonNeural","InJoon (Male)"] ],
  fr: [ ["fr-FR-DeniseNeural","Denise (Female)"], ["fr-FR-HenriNeural","Henri (Male)"] ],
  de: [ ["de-DE-KatjaNeural","Katja (Female)"], ["de-DE-ConradNeural","Conrad (Male)"] ],
  es: [ ["es-ES-ElviraNeural","Elvira (Female)"], ["es-ES-AlvaroNeural","Alvaro (Male)"] ],
};

function updateVoiceOptions() {
  const lang = $tgtLang.value;
  const options = VOICES[lang] || VOICES.en;
  $ttsVoice.innerHTML = options.map(([id,name]) => `<option value="${id}">${name}</option>`).join("");
}

function onTtsProviderChange() {
  const isFree = $ttsProvider.value === "edge";
  $ttsBadge.textContent = isFree ? "FREE" : "CREDIT";
  $ttsBadge.className = "badge" + (isFree ? "" : " paid");
}

$ttsProvider.addEventListener("change", () => { onTtsProviderChange(); saveSettings(); });
$tgtLang.addEventListener("change", () => { updateVoiceOptions(); saveSettings(); });
$srcLang.addEventListener("change", saveSettings);
$ttsVoice.addEventListener("change", saveSettings);
$swapBtn.addEventListener("click", () => {
  const tmp = $srcLang.value;
  $srcLang.value = $tgtLang.value;
  $tgtLang.value = tmp;
  updateVoiceOptions();
  saveSettings();
});

$saveKeyBtn.addEventListener("click", async () => {
  apiKey = $apiKey.value.trim();
  await saveSettings();
  updateStatus("idle", apiKey ? "API key saved ✓" : "No key — enter Soniox API key");
});

// ── Edge TTS ──────────────────────────────────────────────────────────────
async function synthesizeEdge(text, voice) {
  // Use edge-tts via a public service worker shim (no server needed).
  // We use the edge-tts npm approach: connect to Bing Read Aloud websocket.
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${EDGE_TTS_API}?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4&ConnectionId=${crypto.randomUUID().replace(/-/g,"")}`);
    const chunks = [];
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ws.send(`X-Timestamp:${new Date().toISOString()}\r\nContent-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}`);
      ws.send(`X-RequestId:${crypto.randomUUID().replace(/-/g,"")}\r\nX-Timestamp:${new Date().toISOString()}\r\nContent-Type:application/ssml+xml\r\nPath:ssml\r\n\r\n<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='${voice.slice(0,5)}'><voice name='${voice}'>${escHtml(text)}</voice></speak>`);
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        if (ev.data.includes("Path:turn.end")) { ws.close(); resolve(chunks); }
      } else {
        // Binary: 2-byte header length then header, then audio data
        const buf = ev.data;
        const view = new DataView(buf);
        const hlen = view.getUint16(0);
        const audio = buf.slice(2 + hlen);
        if (audio.byteLength > 0) chunks.push(audio);
      }
    };
    ws.onerror = (e) => reject(new Error("Edge TTS WS error"));
    ws.onclose = () => { if (chunks.length === 0) reject(new Error("Edge TTS: no audio")); };
  });
}

async function playSpeech(text) {
  if (!text.trim()) return;
  try {
    if (!audioCtx || audioCtx.state === "closed") audioCtx = new AudioContext();
    let audioData;
    if ($ttsProvider.value === "edge") {
      const chunks = await synthesizeEdge(text, $ttsVoice.value);
      const total = chunks.reduce((a,c) => a + c.byteLength, 0);
      const merged = new Uint8Array(total);
      let offset = 0;
      for (const c of chunks) { merged.set(new Uint8Array(c), offset); offset += c.byteLength; }
      audioData = merged.buffer;
    } else {
      // Soniox TTS — placeholder (requires backend proxy for key protection)
      console.warn("Soniox TTS in extension mode requires the desktop app backend.");
      return;
    }
    const decoded = await audioCtx.decodeAudioData(audioData);
    const src = audioCtx.createBufferSource();
    src.buffer = decoded;
    src.connect(audioCtx.destination);
    src.start();
  } catch (err) {
    console.error("TTS error:", err);
    updateStatus("active", `TTS error: ${err.message}`);
  }
}

// ── Soniox STT ────────────────────────────────────────────────────────────
function buildSttConfig() {
  return {
    api_key: apiKey,
    model: "en-us-lb-1",
    translation_config: {
      target_languages: [tgtLang],
    },
    enable_endpoint_detection: true,
    enable_streaming_translation: true,
    include_nonfinal: true,
    audio_format: "s16le",
    sample_rate: 16000,
    num_audio_channels: 1,
  };
}

function startSTT() {
  sttWs = new WebSocket(SONIOX_STT_URL);
  sttWs.onopen = () => {
    sttWs.send(JSON.stringify(buildSttConfig()));
    updateStatus("active");
  };
  sttWs.onmessage = async (ev) => {
    const data = JSON.parse(ev.data);
    if (data.error_code) {
      updateStatus("error", `STT error: ${data.error_message}`);
      stopTranslation();
      return;
    }
    const tokens = data.tokens || [];
    const isFinal = tokens.some(t => t.is_final);
    if (!isFinal) return;

    // Get original text
    const original = tokens.filter(t => !t.translation_lang).map(t => t.text).join("").trim();
    // Get translation
    const translated = tokens.filter(t => t.translation_lang === tgtLang).map(t => t.text).join("").trim();

    if (original || translated) {
      appendTranscript(original, translated);
      if (translated) await playSpeech(translated);
    }
  };
  sttWs.onerror = () => updateStatus("error", "Connection failed — check API key");
  sttWs.onclose = () => { if (isRunning) updateStatus("error", "Disconnected"); };
}

// ── Start / Stop ──────────────────────────────────────────────────────────
async function startTranslation() {
  apiKey = $apiKey.value.trim();
  if (!apiKey) { updateStatus("error", "Enter Soniox API key first"); return; }

  srcLang = $srcLang.value;
  tgtLang = $tgtLang.value;
  ttsProvider = $ttsProvider.value;
  ttsVoice = $ttsVoice.value;

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch {
    updateStatus("error", "Microphone access denied");
    return;
  }

  isRunning = true;
  $mainBtn.textContent = "■ Stop";
  $mainBtn.className = "btn btn-stop";

  startSTT();

  // Feed raw PCM to STT WebSocket
  const ctx = new AudioContext({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(mediaStream);
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([`
    class PCMProcessor extends AudioWorkletProcessor {
      process(inputs) {
        const ch = inputs[0]?.[0];
        if (ch) {
          const pcm = new Int16Array(ch.length);
          for (let i=0; i<ch.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, ch[i]*32768));
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
        }
        return true;
      }
    }
    registerProcessor('pcm-processor', PCMProcessor);
  `], { type: "application/javascript" })));
  const worklet = new AudioWorkletNode(ctx, "pcm-processor");
  worklet.port.onmessage = (e) => {
    if (sttWs?.readyState === WebSocket.OPEN) sttWs.send(e.data);
  };
  src.connect(worklet);
  worklet.connect(ctx.destination);
}

function stopTranslation() {
  isRunning = false;
  sttWs?.close();
  mediaStream?.getTracks().forEach(t => t.stop());
  $mainBtn.textContent = "▶ Start Translation";
  $mainBtn.className = "btn btn-start";
  updateStatus("idle", "Stopped");
}

$mainBtn.addEventListener("click", () => {
  if (isRunning) stopTranslation(); else startTranslation();
});

// ── Init ──────────────────────────────────────────────────────────────────
loadSettings();
updateVoiceOptions();
