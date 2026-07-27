/**
 * Async TTS queue pattern borrowed from my-translator.
 *
 * The STT/translate pipeline should never wait for TTS.  This module
 * provides a provider-agnostic text queue: callers push translated text,
 * and the queue calls an async synthesizer, then feeds base64 audio
 * chunks into a player.  Multiple lines can be synthesized ahead of
 * playback so TTS does not block the transcript stream.
 */

export interface AsyncTtsQueueConfig {
    /** Async synthesizer: text -> base64 audio (e.g. MP3). */
    synthesize: (text: string) => Promise<string | null>;
    /** Player callback: receives base64 audio and whether it is the final chunk. */
    onAudioChunk: (base64Audio: string, isFinal: boolean) => void;
    onError?: (message: string) => void;
    onLineStarted?: (text: string) => void;
    onLineFinished?: (text: string) => void;
}

interface QueueItem {
    text: string;
}

export class AsyncTtsQueue {
    private readonly config: AsyncTtsQueueConfig;
    private queue: QueueItem[] = [];
    private running = false;
    private enabled = true;

    constructor(config: AsyncTtsQueueConfig) {
        this.config = config;
    }

    setEnabled(enabled: boolean): void {
        this.enabled = enabled;
        if (!enabled) {
            this.queue = [];
        }
    }

    speak(text: string): void {
        if (!this.enabled || !text?.trim()) return;
        this.queue.push({ text: text.trim() });
        if (!this.running) {
            void this._processQueue();
        }
    }

    private async _processQueue(): Promise<void> {
        if (this.queue.length === 0) {
            this.running = false;
            return;
        }

        this.running = true;
        const item = this.queue.shift()!;
        this.config.onLineStarted?.(item.text);

        try {
            const base64Audio = await this.config.synthesize(item.text);
            if (base64Audio) {
                this.config.onAudioChunk(base64Audio, true);
            }
        } catch (error) {
            const message = `TTS failed: ${(error as Error).message}`;
            this.config.onError?.(message);
        } finally {
            this.config.onLineFinished?.(item.text);
            // Immediately start the next line — do not wait for playback to finish.
            void this._processQueue();
        }
    }

    clear(): void {
        this.queue = [];
    }
}