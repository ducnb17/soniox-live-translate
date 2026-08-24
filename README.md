# Soniox Live Translate

> **Dịch giọng nói theo thời gian thực** — nghe tiếng Anh, đọc lại bằng tiếng Việt ngay lập tức.
>
> **Real-time speech-to-speech translation** — hear English, speak back Vietnamese instantly.

[![Latest Release](https://img.shields.io/github/v/release/ducnb17/soniox-live-translate?label=latest&color=blue)](https://github.com/ducnb17/soniox-live-translate/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)](https://github.com/ducnb17/soniox-live-translate/releases/latest)

---

## 🇻🇳 Hướng dẫn sử dụng (Tiếng Việt)

### Soniox Live Translate là gì?

Phần mềm **dịch giọng nói theo thời gian thực** chạy trên máy tính Windows. Bạn nói tiếng Anh vào mic → phần mềm nhận diện giọng nói → dịch sang tiếng Việt → đọc lại ngay lập tức bằng giọng AI.

Ứng dụng phổ biến:
- 🎧 Dịch live stream, podcast, video tiếng Anh sang tiếng Việt
- 📞 Phiên dịch cuộc gọi, hội nghị trực tuyến
- 🎓 Học tiếng Anh — nghe bản gốc + nghe bản dịch
- 🗣️ Hỗ trợ giao tiếp hai chiều Anh–Việt

---

### 📋 Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10/11 (64-bit) |
| RAM | Tối thiểu 4GB |
| Internet | Bắt buộc (kết nối Soniox API) |
| Microphone | Bất kỳ micro nào (USB, 3.5mm, headset) |
| Tài khoản | [Soniox](https://console.soniox.com) (miễn phí 10 credit/tuần) |

---

### ⚡ Cài đặt nhanh (5 phút)

#### Bước 1 — Tải phần mềm
Vào trang [Releases](https://github.com/ducnb17/soniox-live-translate/releases/latest), tải file **`SonioxLiveTranslate-Setup-x.x.x.exe`**.

#### Bước 2 — Cài đặt
Chạy file `.exe` vừa tải. Nếu Windows hiện cảnh báo **"Windows protected your PC"**:
1. Nhấn **More info** (Thông tin thêm)
2. Nhấn **Run anyway** (Chạy dù sao)

> ⚠️ Cảnh báo này xuất hiện vì phần mềm chưa có chữ ký số thương mại. Mã nguồn hoàn toàn mở tại repo này.

#### Bước 3 — Lấy Soniox API Key (miễn phí)
1. Truy cập [console.soniox.com](https://console.soniox.com)
2. Đăng ký tài khoản (hoặc đăng nhập Google)
3. Vào mục **API Keys** → nhấn **Create Key**
4. Copy key (dạng `soniox_...`)

> 💡 Tài khoản miễn phí có **10 credit/tuần** — đủ để dùng thử khoảng 1–2 giờ.

#### Bước 4 — Mở phần mềm và cấu hình
1. Mở **Soniox Live Translate** từ Desktop hoặc Start Menu
2. Cửa sổ trình duyệt tự mở tại `http://localhost:8766`
3. Nhấn **⚙️ Settings** (Cài đặt) ở góc trên phải
4. Trong mục **STT (Speech-to-Text)**, nhập API Key Soniox → nhấn **Test & Save**
5. Đợi thông báo ✅ xanh

---

### 🎯 Sử dụng cơ bản

#### Dịch một chiều (One-way mode) — dùng phổ biến nhất

1. Mở Settings → chọn tab **Translation**
2. **Mode**: chọn **One-way**
3. **Source language**: English (hoặc ngôn ngữ bạn muốn nhận diện)
4. **Target language**: Vietnamese
5. Quay lại màn hình chính → nhấn nút **▶ Start**
6. Nói tiếng Anh vào micro (hoặc phát video/audio tiếng Anh)
7. Phần mềm tự động dịch và đọc bằng tiếng Việt

#### Dịch hai chiều (Two-way mode) — dành cho hội thoại

1. Settings → **Mode**: chọn **Two-way**
2. **Language A**: Vietnamese, **Language B**: English
3. Nhấn Start → nói tiếng Anh thì nghe dịch tiếng Việt, nói tiếng Việt thì nghe dịch tiếng Anh

---

### 🔊 Chọn TTS Provider (giọng đọc)

Vào **Settings → TTS (Text-to-Speech)**:

| Provider | Giá | Giọng tiếng Việt | Ghi chú |
|---|---|---|---|
| **Edge TTS** | ✅ Miễn phí | HoaiMy (nữ), NamMinh (nam) | Khuyên dùng cho người mới |
| Soniox | Tính theo credit | Maya, Adrian | Độ trễ thấp nhất |
| Google Cloud TTS | Trả phí | Chirp3-HD | Chất lượng cao |
| OpenAI TTS | Trả phí | Alloy, Nova... | Giọng tự nhiên |
| Azure Neural | Trả phí | HoaiMy HD... | Microsoft |
| ElevenLabs | Trả phí | Tùy chọn | Clone giọng |
| Amazon Polly | Trả phí | Neural voices | AWS |

**Để dùng Edge TTS miễn phí:**
1. Settings → TTS → chọn **"Edge TTS (free, online)"**
2. Chọn giọng: **vi-VN-HoaiMyNeural** (nữ) hoặc **vi-VN-NamMinhNeural** (nam)
3. Nhấn **Save** — không cần nhập API key

---

### 🎤 Chọn nguồn âm thanh đầu vào

Trong màn hình chính, mục **Input device**:

| Lựa chọn | Dùng khi nào |
|---|---|
| **Microphone (mặc định)** | Nói trực tiếp, phiên dịch hội thoại |
| **VB-Cable / Stereo Mix** | Dịch audio từ ứng dụng khác (YouTube, Zoom, Skype) |
| **URL / File** | Dịch file âm thanh hoặc video online |

> 💡 Để dịch live stream / YouTube: cài [VB-Cable](https://vb-audio.com/Cable/) (miễn phí), chọn VB-Cable làm output trong Windows Sound, rồi chọn **VB-Cable [Loopback]** trong Input device của phần mềm.

---

### ⌨️ Phím tắt

| Phím | Chức năng |
|---|---|
| `Space` | Bắt đầu / Dừng dịch |
| `Esc` | Dừng ngay lập tức |
| `Ctrl+H` | Xem lịch sử dịch |
| `Ctrl+,` | Mở Settings |

---

### ❓ Xử lý sự cố thường gặp

**Phần mềm không khởi động / trình duyệt không mở:**
→ Thử mở thủ công: trình duyệt → `http://localhost:8766`

**TTS chỉ đọc được 1 câu rồi dừng:**
→ Cập nhật lên phiên bản mới nhất (đã fix từ v1.0.0)

**Lỗi "API key invalid":**
→ Kiểm tra lại API key tại [console.soniox.com](https://console.soniox.com)
→ Đảm bảo còn credit trong tài khoản

**Không nghe thấy giọng dịch:**
→ Settings → TTS → chọn đúng Output device
→ Kiểm tra âm lượng hệ thống

**Độ trễ cao (>3 giây):**
→ Kiểm tra kết nối internet
→ Thử đổi TTS provider sang Edge TTS (free) hoặc Soniox

---

## 🇬🇧 User Guide (English)

### What is Soniox Live Translate?

A **real-time speech-to-speech translation** desktop app for Windows. Speak English into your microphone → the app recognizes your speech → translates to Vietnamese → reads it back immediately using an AI voice.

Common use cases:
- 🎧 Translate live streams, podcasts, and English videos to Vietnamese
- 📞 Interpret phone calls and online meetings
- 🎓 Language learning — hear the original + the translation
- 🗣️ Two-way English–Vietnamese conversation support

---

### 📋 System Requirements

| Component | Requirement |
|---|---|
| OS | Windows 10/11 (64-bit) |
| RAM | 4GB minimum |
| Internet | Required (Soniox API) |
| Microphone | Any (USB, 3.5mm, headset) |
| Account | [Soniox](https://console.soniox.com) (free: 10 credits/week) |

---

### ⚡ Quick Setup (5 minutes)

#### Step 1 — Download
Go to [Releases](https://github.com/ducnb17/soniox-live-translate/releases/latest) and download **`SonioxLiveTranslate-Setup-x.x.x.exe`**.

#### Step 2 — Install
Run the `.exe` file. If Windows shows **"Windows protected your PC"**:
1. Click **More info**
2. Click **Run anyway**

> ⚠️ This warning appears because the app doesn't have a commercial code signing certificate. The source code is fully open in this repository.

#### Step 3 — Get a Soniox API Key (free)
1. Go to [console.soniox.com](https://console.soniox.com)
2. Sign up (or log in with Google)
3. Go to **API Keys** → click **Create Key**
4. Copy your key (format: `soniox_...`)

> 💡 The free plan includes **10 credits/week** — enough for roughly 1–2 hours of use.

#### Step 4 — Open and Configure
1. Open **Soniox Live Translate** from Desktop or Start Menu
2. A browser window opens automatically at `http://localhost:8766`
3. Click **⚙️ Settings** in the top right
4. Under **STT (Speech-to-Text)**, paste your Soniox API key → click **Test & Save**
5. Wait for the ✅ green confirmation

---

### 🎯 Basic Usage

#### One-way mode — most common

1. Settings → **Translation** tab
2. **Mode**: select **One-way**
3. **Source language**: English
4. **Target language**: Vietnamese
5. Go back to the main screen → click **▶ Start**
6. Speak English into your mic (or play English audio/video)
7. The app automatically translates and reads back in Vietnamese

#### Two-way mode — for conversations

1. Settings → **Mode**: select **Two-way**
2. **Language A**: Vietnamese, **Language B**: English
3. Click Start → speaking English plays the Vietnamese translation; speaking Vietnamese plays the English translation

---

### 🔊 Choosing a TTS Provider (voice)

Go to **Settings → TTS (Text-to-Speech)**:

| Provider | Cost | Vietnamese Voice | Notes |
|---|---|---|---|
| **Edge TTS** | ✅ Free | HoaiMy (female), NamMinh (male) | Recommended for beginners |
| Soniox | Credits | Maya, Adrian | Lowest latency |
| Google Cloud TTS | Paid | Chirp3-HD | High quality |
| OpenAI TTS | Paid | Alloy, Nova... | Natural voices |
| Azure Neural | Paid | HoaiMy HD... | Microsoft |
| ElevenLabs | Paid | Custom | Voice cloning |
| Amazon Polly | Paid | Neural voices | AWS |

**To use Edge TTS for free:**
1. Settings → TTS → select **"Edge TTS (free, online)"**
2. Select voice: **vi-VN-HoaiMyNeural** (female) or **vi-VN-NamMinhNeural** (male)
3. Click **Save** — no API key required

---

### 🎤 Choosing an Audio Input Source

In the main screen, under **Input device**:

| Option | When to use |
|---|---|
| **Microphone (default)** | Direct speech, live interpretation |
| **VB-Cable / Stereo Mix** | Translate audio from other apps (YouTube, Zoom, Skype) |
| **URL / File** | Translate an audio or video file / online stream |

> 💡 To translate live streams or YouTube: install [VB-Cable](https://vb-audio.com/Cable/) (free), set VB-Cable as the default playback device in Windows Sound, then select **VB-Cable [Loopback]** as the Input device in the app.

---

### ⌨️ Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Start / Stop translation |
| `Esc` | Stop immediately |
| `Ctrl+H` | View translation history |
| `Ctrl+,` | Open Settings |

---

### ❓ Troubleshooting

**App doesn't start / browser doesn't open:**
→ Open manually: browser → `http://localhost:8766`

**TTS reads only 1 sentence then stops:**
→ Update to the latest version (fixed in v1.0.0)

**"API key invalid" error:**
→ Double-check your key at [console.soniox.com](https://console.soniox.com)
→ Make sure you have remaining credits

**No audio output:**
→ Settings → TTS → verify the correct Output device is selected
→ Check Windows system volume

**High latency (>3 seconds):**
→ Check your internet connection
→ Try switching TTS to Edge TTS (free) or Soniox

---

### 🏗️ Architecture

```
Browser (microphone / tab audio / URL file)
   │  audio bytes (binary WebSocket)
   ▼
FastAPI /ws/translate ──► Soniox STT+translation (wss://stt-rt.soniox.com)
   │                          │ tokens (translation complete)
   │                          ▼
   │  translation text ──►  TTS queue ──► Selected TTS provider
   │                          │   (Soniox / Edge TTS / Google / OpenAI / ...)
   │                          ▼
   ◄── PCM s16le @ 24kHz ────┘   → Web Audio API playback
```

**Key technical features:**
- Per-sentence parallel TTS synthesis — no waiting between sentences
- Barge-in: new speech instantly interrupts current playback
- Automatic STT reconnection with audio buffering
- TTS audio cache — repeated phrases play instantly
- All API keys encrypted with Windows DPAPI

---

### 📄 License

MIT License — free for personal and commercial use.

---

### 🙏 Credits

Built with [Soniox](https://soniox.com) STT/TTS APIs · [Edge TTS](https://github.com/rany2/edge-tts) · [FastAPI](https://fastapi.tiangolo.com) · [Electron](https://www.electronjs.org)
