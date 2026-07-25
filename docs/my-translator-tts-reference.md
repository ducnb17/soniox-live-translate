# TTS song song từ my-translator — tham khảo cho soniox-live-translate

Repo `my-translator` (Tauri + web frontend) xử lý TTS hoàn toàn bất đồng bộ ở frontend.
Bên dưới là code/logic/thuật toán cần lấy về.

## 1. Callback khi có bản dịch (`src/js/app.js`)

```js
sonioxClient.onTranslation = (text) => {
    this.transcriptUI.addTranslation(text);
    const src = this._sonioxOriginalQueue.shift() || '';
    sessionStore.addSegment(src, text);
    this._speakIfEnabled(text);
};
```

Không `await` TTS. Chỉ gọi `speak(text)` — TTS queue xử lý sau.

```js
_speakIfEnabled(text) {
    if (this.ttsEnabled && text?.trim()) {
        this._getActiveTTS().speak(text);
    }
}
```

## 2. TTS provider queue (`src/js/edge-tts.js` — mẫu điển hình)

```js
class EdgeTTSRust {
    constructor() {
        this._queue = [];
        this._isSpeaking = false;
        this.onAudioChunk = null;   // (base64Audio, isFinal) => {}
    }

    speak(text) {
        if (!text?.trim()) return;
        this._queue.push(text.trim());
        if (!this._isSpeaking) {
            this._processQueue();
        }
    }

    async _processQueue() {
        if (this._queue.length === 0) {
            this._isSpeaking = false;
            return;
        }

        this._isSpeaking = true;
        const text = this._queue.shift();

        try {
            const base64Audio = await invoke('edge_tts_speak', { text, voice, rate });
            if (this.onAudioChunk) {
                this.onAudioChunk(base64Audio, true);
            }
        } catch (err) {
            this.onError?.(`Edge TTS: ${err}`);
        }

        // Tiếp tục queue tiếp theo, không đợi audio phát xong
        this._processQueue();
    }
}
```

## 3. Audio player queue (`src/js/audio-player.js`)

```js
class AudioPlayer {
    constructor() {
        this._queue = [];           // AudioBuffer queue
        this._isPlaying = false;
        this._nextStartTime = 0;
        this._playbackRate = 1.0;
    }

    async enqueue(base64Audio) {
        // decode base64 → MP3 → AudioBuffer
        const binaryStr = atob(base64Audio);
        const bytes = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);

        const audioBuffer = await this.audioContext.decodeAudioData(bytes.buffer.slice(0));
        this._queue.push(audioBuffer);
        this._scheduleNext();
    }

    _scheduleNext() {
        if (this._queue.length === 0 || !this.audioContext) {
            this._isPlaying = false;
            return;
        }

        const buffer = this._queue.shift();
        const source = this.audioCtx.createBufferSource();
        source.buffer = buffer;
        source.playbackRate.value = this._playbackRate;
        source.connect(this.audioCtx.destination);

        const startTime = Math.max(this.audioCtx.currentTime, this._nextStartTime);
        source.start(startTime);
        this._nextStartTime = startTime + buffer.duration / this._playbackRate;

        source.onended = () => this._scheduleNext();
    }
}
```

## 4. Điểm then chốt để STT+translate không bị TTS đọc block

- **STT callback không await TTS** — chỉ push text vào queue.
- **TTS queue xử lý async riêng** — mỗi line độc lập.
- **Audio player schedule theo thời gian** — không busy-wait; nhiều buffer có thể được decode/enqueue trong khi buffer trước đang phát.
- **Không có logic mute capture khi TTS đang phát** — my-translator dựa vào user dùng tai nghe hoặc tự tránh feedback. Soniox-live-translate thì cần mute/duck capture vì STT chạy liên tục.

## 5. Áp dụng vào soniox-live-translate

soniox-live-translate đã có backend Python chạy STT và TTS song song qua `asyncio.TaskGroup`.
Vấn đề freeze ở virtual loopback là do **TTS echo quay lại STT input**, không phải TTS queue bị block.

Hướng áp dụng:
- Giữ nguyên backend TaskGroup.
- Ở frontend, khi dùng virtual loopback, **hard-mute capture** khi TTS audible (giống tab capture).
- Nếu muốn dùng external TTS provider, thêm `AsyncTtsQueue` để gọi `/api/tts` bất đồng bộ thay vì chờ audio stream từ backend.