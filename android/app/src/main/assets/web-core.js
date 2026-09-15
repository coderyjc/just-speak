export const TARGET_SAMPLE_RATE = 16000;
export const NETWORK_PACKET_SAMPLES = 1600;

export const DEFAULT_SETTINGS = Object.freeze({
  region: "beijing",
  language: "zh",
  workspaceId: "",
  asrModel: "qwen-audio-3.0-asr-flash-streaming",
  asrPrompt: "",
  asrWebSocketUrl: "",
  historyLimit: 50,
  darkTheme: false,
});

export function normalizeSettings(value = {}) {
  const merged = { ...DEFAULT_SETTINGS, ...value };
  return {
    region: merged.region === "singapore" ? "singapore" : "beijing",
    language: ["zh", "en", "auto"].includes(merged.language) ? merged.language : "zh",
    workspaceId: String(merged.workspaceId || "").trim(),
    asrModel: String(merged.asrModel || DEFAULT_SETTINGS.asrModel).trim() || DEFAULT_SETTINGS.asrModel,
    asrPrompt: String(merged.asrPrompt || "").trim().slice(0, 400),
    asrWebSocketUrl: String(merged.asrWebSocketUrl || "").trim(),
    historyLimit: Math.min(600, Math.max(10, Number(merged.historyLimit) || 50)),
    darkTheme: Boolean(merged.darkTheme),
  };
}

export function friendlyMicrophoneError(error = {}) {
  const name = String(error.name || "");
  const message = String(error.message || "").trim();
  if (["NotAllowedError", "SecurityError", "PermissionDeniedError"].includes(name)) {
    return "麦克风权限未开启，请在系统应用设置中允许 JustSpeak 使用麦克风";
  }
  if (["NotFoundError", "DevicesNotFoundError"].includes(name)) {
    return "没有检测到可用麦克风，请检查设备或蓝牙耳机连接";
  }
  if (
    ["NotReadableError", "AbortError", "TrackStartError"].includes(name)
    || /could not start audio source|device in use|audio source/i.test(message)
  ) {
    return "麦克风启动失败，请关闭占用麦克风的通话或录音应用，并检查系统麦克风总开关";
  }
  if (name === "OverconstrainedError") {
    return "当前设备不支持所需录音参数，请更新 Android System WebView 后重试";
  }
  if (/[\u3400-\u9fff]/.test(message)) return message;
  return "无法启动麦克风，请检查权限、系统麦克风开关和其他录音应用";
}

export function dashScopeEndpoint(settings) {
  const config = normalizeSettings(settings);
  if (config.asrWebSocketUrl) return config.asrWebSocketUrl;
  if (config.workspaceId) {
    const region = config.region === "singapore"
      ? "ap-southeast-1"
      : "cn-beijing";
    return `wss://${config.workspaceId}.${region}.maas.aliyuncs.com/api-ws/v1/inference`;
  }
  const host = config.region === "singapore"
    ? "dashscope-intl.aliyuncs.com"
    : "dashscope.aliyuncs.com";
  return `wss://${host}/api-ws/v1/inference`;
}

export function createId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, character => {
    const random = Math.floor(Math.random() * 16);
    const value = character === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

export function buildRunTask(taskId, settings) {
  const config = normalizeSettings(settings);
  const parameters = {
    format: "pcm",
    sample_rate: TARGET_SAMPLE_RATE,
    heartbeat: true,
    semantic_punctuation_enabled: false,
  };
  if (config.language !== "auto") parameters.language_hints = [config.language];
  const input = config.asrPrompt
    ? {
      context: [{
        role: "user",
        content: [{ type: "input_text", text: config.asrPrompt }],
      }],
    }
    : {};
  return {
    header: { action: "run-task", task_id: taskId, streaming: "duplex" },
    payload: {
      task_group: "audio",
      task: "asr",
      function: "recognition",
      model: config.asrModel,
      parameters,
      input,
    },
  };
}

export function buildFinishTask(taskId) {
  return {
    header: { action: "finish-task", task_id: taskId, streaming: "duplex" },
    payload: { input: {} },
  };
}

export function parseDashScopeEvent(message) {
  const value = typeof message === "string" ? JSON.parse(message) : message;
  const event = value?.header?.event || "unknown";
  if (event === "result-generated") {
    const sentence = value?.payload?.output?.sentence || {};
    return {
      type: "result",
      taskId: value?.header?.task_id || "",
      sentenceId: Number(sentence.sentence_id || 0),
      text: String(sentence.text || "").trim(),
      isFinal: Boolean(sentence.sentence_end),
      isHeartbeat: Boolean(sentence.heartbeat),
      beginTime: Number(sentence.begin_time || 0),
      endTime: sentence.end_time == null ? null : Number(sentence.end_time),
      billedDuration: value?.payload?.usage?.duration ?? null,
    };
  }
  if (event === "task-failed") {
    return {
      type: "failed",
      taskId: value?.header?.task_id || "",
      code: value?.header?.error_code || "ASR_ERROR",
      message: value?.header?.error_message || "云端识别失败",
    };
  }
  if (event === "task-started") return { type: "started", taskId: value?.header?.task_id || "" };
  if (event === "task-finished") return { type: "finished", taskId: value?.header?.task_id || "" };
  return { type: "unknown", event, raw: value };
}

export class Pcm16Resampler {
  constructor(inputRate, outputRate = TARGET_SAMPLE_RATE) {
    if (!(inputRate > 0) || !(outputRate > 0)) throw new Error("采样率必须大于 0");
    this.ratio = inputRate / outputRate;
    this.buffer = new Float32Array(0);
    this.position = 0;
  }

  process(input) {
    if (!input?.length) return new Int16Array(0);
    const combined = new Float32Array(this.buffer.length + input.length);
    combined.set(this.buffer);
    combined.set(input, this.buffer.length);
    if (combined.length < 2 || this.position >= combined.length - 1) {
      this.buffer = combined;
      return new Int16Array(0);
    }
    const outputLength = Math.ceil((combined.length - 1 - this.position) / this.ratio);
    const result = new Int16Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const sourcePosition = this.position + index * this.ratio;
      const left = Math.floor(sourcePosition);
      const fraction = sourcePosition - left;
      const sample = combined[left] + (combined[left + 1] - combined[left]) * fraction;
      const clipped = Math.max(-1, Math.min(1, sample));
      result[index] = clipped < 0 ? Math.round(clipped * 32768) : Math.round(clipped * 32767);
    }
    this.position += outputLength * this.ratio;
    const consumed = Math.min(Math.floor(this.position), combined.length - 1);
    this.buffer = combined.slice(consumed);
    this.position -= consumed;
    return result;
  }
}

export function pcm16ToBytes(samples) {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(index * 2, samples[index], true);
  }
  return bytes;
}

export function bytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

export function buildWavBlob(pcmParts, sampleRate = TARGET_SAMPLE_RATE) {
  const byteLength = pcmParts.reduce((total, part) => total + part.byteLength, 0);
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const writeAscii = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + byteLength, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, byteLength, true);
  return new Blob([header, ...pcmParts], { type: "audio/wav" });
}

export function transcriptPreview(text, maxLength = 72) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized;
}
