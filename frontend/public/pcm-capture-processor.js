/**
 * AudioWorklet processor: captures raw audio from the mic/tab,
 * resamples to 16 kHz mono, and posts PCM Int16 buffers to the main thread.
 *
 * Runs on the audio rendering thread — keep logic minimal and allocation-free
 * where possible.
 *
 * Mute modes (sent from main thread via port.postMessage):
 *   { type: 'mute',  value: true|false }   — full mute / unmute (tab-capture / digital loop)
 *   { type: 'duck',  value: 0.0–1.0 }      — apply gain factor (mic mode while TTS plays)
 *     1.0  = no attenuation (normal capture)
 *     0.15 = heavy duck (-16 dB) so STT keeps running but TTS echo is greatly reduced
 *     0.0  = silence (equivalent to mute)
 *
 * For microphone mode we duck rather than hard-mute so the STT stream keeps
 * receiving audio even while TTS is speaking — the browser's built-in AEC
 * (echoCancellation) removes most of the TTS echo before it reaches here, and
 * the duck gain suppresses whatever residual remains. STT therefore stays live
 * and translation does not stall until TTS finishes.
 *
 * For tab/system-audio capture we continue to send silence because the TTS
 * signal is a perfect digital copy of what the mic would receive — there is
 * no AEC to help, so the only safe option is to block it entirely.
 */

const TARGET_SAMPLE_RATE = 16000;

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this._inputRate = sampleRate; // AudioWorklet global
    this._ratio = this._inputRate / TARGET_SAMPLE_RATE;
    this._resampleOffset = 0;
    // Gain applied to each sample before sending (1.0 = full, 0.0 = silence).
    this._gain = 1.0;
    // Hard mute flag (tab-capture mode — overrides _gain completely).
    this._hardMuted = false;

    this.port.onmessage = (event) => {
      if (!event.data) return;
      if (event.data.type === 'mute') {
        this._hardMuted = !!event.data.value;
        if (!this._hardMuted) this._gain = 1.0;
      } else if (event.data.type === 'duck') {
        const g = Number(event.data.value);
        this._gain = (Number.isFinite(g) && g >= 0 && g <= 1) ? g : 1.0;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const channelData = input[0];
    if (!channelData || channelData.length === 0) return true;

    if (this._hardMuted) {
      // Full silence — tab/digital-loop mode; STT keeps the connection open.
      const silenceCount = Math.ceil(channelData.length / this._ratio);
      const silence = new Int16Array(silenceCount);
      this.port.postMessage(silence.buffer, [silence.buffer]);
      return true;
    }

    // Down-sample from inputRate → 16 kHz with linear interpolation and gain.
    const gain = this._gain;
    const outputSamples = [];
    let pos = this._resampleOffset;

    while (pos < channelData.length) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = channelData[idx];
      const s1 = idx + 1 < channelData.length ? channelData[idx + 1] : s0;
      const sample = (s0 + (s1 - s0) * frac) * gain;
      const clamped = Math.max(-1, Math.min(1, sample));
      outputSamples.push(clamped * 0x7fff);
      pos += this._ratio;
    }

    this._resampleOffset = pos - channelData.length;

    if (outputSamples.length > 0) {
      const pcm = new Int16Array(outputSamples);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }

    return true;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
