/*
 * AudioWorklet do RMS VAD.
 * Acumula os blocos nativos do Chromium até 1024 frames, preservando o
 * comportamento histórico do VAD sem depender do ScriptProcessorNode da UI.
 */
class RmsVadProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const requested = Number(options?.processorOptions?.blockSize || 1024);
    this.blockSize = Number.isFinite(requested) && requested >= 128
      ? Math.round(requested)
      : 1024;
    this.buffer = new Float32Array(this.blockSize);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs?.[0]?.[0];
    if (!input?.length) return true;

    let srcOffset = 0;
    while (srcOffset < input.length) {
      const available = this.blockSize - this.offset;
      const count = Math.min(available, input.length - srcOffset);
      this.buffer.set(input.subarray(srcOffset, srcOffset + count), this.offset);
      this.offset += count;
      srcOffset += count;

      if (this.offset === this.blockSize) {
        const chunk = this.buffer;
        let sum = 0;
        for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
        const rms = Math.sqrt(sum / chunk.length);
        this.port.postMessage(
          { type: "audio-block", rms, frames: chunk.length, chunk },
          [chunk.buffer],
        );
        this.buffer = new Float32Array(this.blockSize);
        this.offset = 0;
      }
    }

    return true;
  }
}

registerProcessor("rms-vad-processor", RmsVadProcessor);
