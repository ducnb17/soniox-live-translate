/** Overlap-add (OLA) time-stretch / time-compression for speech audio.
 *
 * Changes tempo while preserving pitch — no chipmunk effect, no detune
 * phase artefacts.  Works well for spoken TTS at 0.5x – 2.0x.
 */
export function stretchSamples(input: Float32Array, rate: number): Float32Array {
  if (Math.abs(rate - 1) < 1e-6) return new Float32Array(input);
  if (rate <= 0.05 || !Number.isFinite(rate)) return new Float32Array(0);

  const windowSize = 1024;
  const hopAnalysis = Math.max(1, windowSize >> 2); // 75 % overlap

  // Tiny chunks – fall back to linear resampling so we don't get silence.
  if (input.length < windowSize) {
    const outLen = Math.max(1, Math.round(input.length / rate));
    const result = new Float32Array(outLen);
    for (let i = 0; i < outLen; i += 1) {
      const srcIdx = Math.min(input.length - 1, Math.floor(i * rate));
      result[i] = input[srcIdx];
    }
    return result;
  }

  const outputLength = Math.max(1, Math.round(input.length / rate));
  const output = new Float32Array(outputLength);
  const sumWeights = new Float32Array(outputLength);

  // Hann window
  const window = new Float32Array(windowSize);
  for (let i = 0; i < windowSize; i += 1) {
    window[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / windowSize));
  }

  // Overlap-add synthesis
  for (
    let inPos = 0;
    inPos + windowSize <= input.length;
    inPos += hopAnalysis
  ) {
    const outPos = Math.round(inPos / rate);
    if (outPos + windowSize > outputLength) break;
    for (let i = 0; i < windowSize; i += 1) {
      const w = window[i];
      output[outPos + i] += input[inPos + i] * w;
      sumWeights[outPos + i] += w;
    }
  }

  // Normalise by the accumulated window sum
  for (let i = 0; i < outputLength; i += 1) {
    if (sumWeights[i] > 1e-4) {
      output[i] /= sumWeights[i];
    }
  }

  return output;
}
