export interface SonioxToken {
  text?: string;
  is_final?: boolean;
  speaker?: number | null;
  language?: string;
  source_language?: string;
  translation_status?: "translation" | "original";
}

export interface SonioxSttResponse {
  type?: string;
  line_id?: number;
  byte_length?: number;
  line_audio_end?: boolean;
  tokens?: SonioxToken[];
  finished?: boolean;
  error_code?: string | number;
  error_type?: string;
  error_message?: string;
  session_done?: boolean;
  session_id?: string;
  barge_ack?: boolean;
  reconnecting?: boolean;
  attempt?: number;
  max_attempts?: number;
  downtime_start?: number;
  reconnected?: boolean;
  downtime_ms?: number;
  downtime_text?: string;
  buffered_audio_bytes?: number;
  dropped_audio_bytes?: number;
  reconnect_failed?: boolean;
  max_retries?: number;
  error_message_plain?: string;
  tts_fallback?: {
    from_provider: string;
    to_provider: string;
    reason: string;
  };
  tts_error?: {
    provider_id: string;
    message: string;
  };
  tts_usage?: {
    provider_id: string;
    voice_id: string;
    characters: number;
    estimated_cost_usd: number;
  };
  translation_error?: { message: string };
  speaker?: number | null;
  original_text?: string;
  translated_text?: string;
  lang?: string | null;
  is_endpoint?: boolean;
}

export interface Utterance {
  speaker: number | null;
  language: string | null;
  originalFinal: string;
  originalPartial: string;
  translationFinal: string;
  translationPartial: string;
}

export interface ConfigResponse {
  voices: string[];
  languages: { code: string; name: string }[];
  configured: boolean;
}

export interface SetupPayload {
  soniox_api_key: string;
  host?: string;
  port?: number;
}

export type AppMode = "file" | "mic";
export type AppState = "idle" | "recording" | "playing-file";
export type TranslationMode = "one_way" | "two_way";
export type AudioSource = "microphone" | "tab";

export interface DeviceOption {
  deviceId: string;
  label: string;
  kind: "audioinput" | "audiooutput";
}

export type ConnectionStatus = "connected" | "reconnecting" | "failed" | "idle";

export interface DeviceOption {
  deviceId: string;
  label: string;
  kind: "audioinput" | "audiooutput";
}

export const TTS_SAMPLE_RATE = 24000;

export const BARGE_RMS_THRESHOLD = 0.05;
export const BARGE_HOLD_MS = 220;
// Grace period after a new TTS line starts playing (activeLineSources goes
// empty -> non-empty) during which barge-in is suppressed. This avoids the
// initial "pop" of TTS audio (picked up as echo by the mic) from
// immediately self-triggering a barge-in.
export const BARGE_TTS_START_GRACE_MS = 400;


export const LANGUAGES: [string, string][] = [
  ["af", "Afrikaans"], ["sq", "Albanian"], ["ar", "Arabic"], ["az", "Azerbaijani"],
  ["eu", "Basque"], ["be", "Belarusian"], ["bn", "Bengali"], ["bs", "Bosnian"],
  ["bg", "Bulgarian"], ["ca", "Catalan"], ["zh", "Chinese"], ["hr", "Croatian"],
  ["cs", "Czech"], ["da", "Danish"], ["nl", "Dutch"], ["en", "English"],
  ["et", "Estonian"], ["fi", "Finnish"], ["fr", "French"], ["gl", "Galician"],
  ["de", "German"], ["el", "Greek"], ["gu", "Gujarati"], ["he", "Hebrew"],
  ["hi", "Hindi"], ["hu", "Hungarian"], ["id", "Indonesian"], ["it", "Italian"],
  ["ja", "Japanese"], ["kn", "Kannada"], ["kk", "Kazakh"], ["ko", "Korean"],
  ["lv", "Latvian"], ["lt", "Lithuanian"], ["mk", "Macedonian"], ["ms", "Malay"],
  ["ml", "Malayalam"], ["mr", "Marathi"], ["no", "Norwegian"], ["fa", "Persian"],
  ["pl", "Polish"], ["pt", "Portuguese"], ["pa", "Punjabi"], ["ro", "Romanian"],
  ["ru", "Russian"], ["sr", "Serbian"], ["sk", "Slovak"], ["sl", "Slovenian"],
  ["es", "Spanish"], ["sw", "Swahili"], ["sv", "Swedish"], ["tl", "Tagalog"],
  ["ta", "Tamil"], ["te", "Telugu"], ["th", "Thai"], ["tr", "Turkish"],
  ["uk", "Ukrainian"], ["ur", "Urdu"], ["vi", "Vietnamese"], ["cy", "Welsh"],
];

export const VOICES: string[] = [
  "Adrian", "Claire", "Daniel", "Emma", "Grace", "Jack",
  "Kenji", "Maya", "Mina", "Nina", "Noah", "Owen",
];
