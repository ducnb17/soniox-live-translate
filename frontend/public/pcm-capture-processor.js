/**
 * AudioWorklet processor: captures raw audio from the mic/tab,
 * resamples to 16 kHz mono, and posts PCM Int16 buffers to the main thread.
 *
 * Runs on the audio rendering thread — keep logic minimal and allocation-free
 * where possible.
 */

const TARGET_SAMPLE_RATE = 16000;

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    // sampleRate is the AudioContext's native rate (e.g. 44100 or 48000).
    this._inputRate = sampleRate; // AudioWorklet global
    this._ratio = this._inputRate / TARGET_SAMPLE_RATE;
    // Fractional accumulator for the resampling position.
    this._resampleOffset = 0;
    // Whether the main thread asked us to mute (send silence).
    this._muted = false;

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'mute') {
        this._muted = !!event.data.value;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;

    // Take channel 0 (mono).
    const channelData = input[0];
    if (!channelData || channelData.length === 0) return true;

    if (this._muted) {
      // Send a silence frame so the backend keeps receiving data and doesn't
      // time-out, but STT won't see speech.
      const silenceCount = Math.ceil(channelData.length / this._ratio);
      const silence = new Int16Array(silenceCount); // already zeros
      this.port.postMessage(silence.buffer, [silence.buffer]);
      return true;
    }

    // Down-sample from inputRate → 16 kHz using linear interpolation.
    const outputSamples = [];
    let pos = this._resampleOffset;

    while (pos < channelData.length) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = channelData[idx];
      const s1 = idx + 1 < channelData.length ? channelData[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;

      // Clamp to [-1, 1] and convert to Int16.
      const clamped = Math.max(-1, Math.min(1, sample));
      outputSamples.push(clamped * 0x7fff);

      pos += this._ratio;
    }

    // Save fractional remainder for next call.
    this._resampleOffset = pos - channelData.length;

    if (outputSamples.length > 0) {
      const pcm = new Int16Array(outputSamples);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }

    return true;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
