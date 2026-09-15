import {
  DEFAULT_SETTINGS,
  NETWORK_PACKET_SAMPLES,
  Pcm16Resampler,
  buildFinishTask,
  buildRunTask,
  buildWavBlob,
  bytesToBase64,
  createId,
  dashScopeEndpoint,
  friendlyMicrophoneError,
  normalizeSettings,
  parseDashScopeEvent,
  pcm16ToBytes,
  transcriptPreview,
} from "./web-core.js";

const pageOrder = ["home", "record", "files", "history", "settings"];
const phaseMeta = {
  IDLE: ["待机", "idle", "轻触下方按钮开始记录"],
  STARTING: ["连接中", "starting", "正在开启麦克风并连接云端识别"],
  RECORDING: ["转写中", "recording", "音频与实时文字由网页状态机持续处理"],
  PAUSED: ["已暂停", "paused", "已保存当前分段，可继续同一条记录"],
  STOPPING: ["收尾中", "stopping", "正在等待云端返回最后一句"],
  FINISHED: ["已完成", "finished", "录音和文稿已保存到历史记录"],
  ERROR: ["需处理", "error", "任务遇到问题，已完成内容仍会保留"],
};
const statusLabels = {
  PREPARING: "准备中", RECORDING: "录音中", TRANSCRIBING: "识别中",
  PAUSED: "已暂停", RECOVERABLE: "待继续", COMPLETED: "已完成", FAILED: "失败",
};
const nativeBridge = window.JustSpeakBridge;
const sessionRegistry = new Map();
const microphonePermissionRegistry = new Map();
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icon = name => `<svg aria-hidden="true"><use href="#${name}"/></svg>`;
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

let appState = loadAppState();
let activePage = "home";
let historyFilter = "ALL";
let selectedMedia = null;
let activeRuntime = null;
let detailTaskId = null;
let toastTimer = 0;
let mockTimer = 0;
let lastRenderedPhase = "";
let lastSettingsSignature = "";
let lastNativeTheme = null;

window.JustSpeakNative = {
  receiveEvent(name, payload) {
    let detail = {};
    try { detail = JSON.parse(payload); } catch (_) { detail = { message: payload }; }
    handleNativeEvent(name, detail);
  },
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function loadJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value ?? fallback;
  } catch (_) {
    return fallback;
  }
}

function loadAppState() {
  const nativeState = (() => {
    if (!nativeBridge) return {};
    try { return JSON.parse(nativeBridge.getInitialState()); } catch (_) { return {}; }
  })();
  const settings = normalizeSettings(loadJson("justspeak-settings-v2", DEFAULT_SETTINGS));
  const tasks = loadJson("justspeak-tasks-v2", []).map(task => {
    if (["PREPARING", "RECORDING", "TRANSCRIBING"].includes(task.status)) {
      return { ...task, status: "RECOVERABLE", errorMessage: "上次任务被系统中断，可保留已有文稿" };
    }
    return task;
  });
  return {
    hasCredential: Boolean(nativeState.hasCredential ?? !nativeBridge),
    appVersion: nativeState.appVersion || "browser-preview",
    settings,
    tasks: nativeBridge ? tasks : tasks.length ? tasks : mockTasks(),
    recording: idleRecording(),
  };
}

function idleRecording(overrides = {}) {
  return {
    phase: "IDLE",
    taskId: null,
    durationMillis: 0,
    amplitude: 0,
    segmentCount: 0,
    finalTranscript: "",
    interimTranscript: "",
    connectionState: "idle",
    errorMessage: null,
    ...overrides,
  };
}

function mockTasks() {
  return [
    {
      id: "preview-1", kind: "RECORDING", title: "产品讨论与后续安排",
      createdAtEpochMillis: Date.now() - 7200000, updatedAtEpochMillis: Date.now() - 7200000,
      status: "COMPLETED", durationMillis: 1642000, characterCount: 48,
      preview: "先完成 Android 安装测试，再继续长文件与后台场景。",
      transcript: "先完成 Android 安装测试，再继续长文件与后台场景。",
      segmentCount: 1, audioKeys: [],
    },
  ];
}

function persistSettings() {
  localStorage.setItem("justspeak-settings-v2", JSON.stringify(appState.settings));
}

function persistTasks() {
  const limit = appState.settings.historyLimit;
  const protectedTasks = appState.tasks.filter(task => task.status !== "COMPLETED");
  const completed = appState.tasks.filter(task => task.status === "COMPLETED")
    .sort((left, right) => right.updatedAtEpochMillis - left.updatedAtEpochMillis)
    .slice(0, limit);
  appState.tasks = [...protectedTasks, ...completed]
    .sort((left, right) => right.updatedAtEpochMillis - left.updatedAtEpochMillis);
  localStorage.setItem("justspeak-tasks-v2", JSON.stringify(appState.tasks));
}

function updateTask(taskId, changes, persist = true) {
  const index = appState.tasks.findIndex(task => task.id === taskId);
  if (index < 0) return null;
  appState.tasks[index] = {
    ...appState.tasks[index],
    ...changes,
    updatedAtEpochMillis: Date.now(),
  };
  if (persist) persistTasks();
  renderHome();
  renderHistory();
  if (detailTaskId === taskId) renderTaskDetail();
  return appState.tasks[index];
}

function handleNativeEvent(name, detail) {
  if (name === "toast") return showToast(detail.message);
  if (name === "microphonePermissionResult") {
    const pending = microphonePermissionRegistry.get(detail.requestId);
    if (pending) {
      microphonePermissionRegistry.delete(detail.requestId);
      pending.resolve(Boolean(detail.granted));
    }
    return;
  }
  if (name === "nativeReady" || name === "credentialChanged") {
    appState.hasCredential = Boolean(detail.hasCredential);
    renderSettings();
    renderRecording();
    return;
  }
  const runtime = detail.sessionId ? sessionRegistry.get(detail.sessionId) : null;
  if (!runtime) return;
  if (name === "asrSocketOpen") {
    const command = JSON.stringify(buildRunTask(runtime.cloudTaskId, appState.settings));
    if (!nativeBridge.sendAsrText(runtime.sessionId, command)) {
      failRuntime(runtime, "启动识别任务失败");
    }
    return;
  }
  if (name === "asrMessage") {
    handleAsrMessage(runtime, detail.message);
    return;
  }
  if (name === "asrSocketFailure") {
    failRuntime(runtime, friendlyNetworkError(detail.message));
    return;
  }
  if (name === "asrSocketClosed" && !runtime.finished.settled && !runtime.closing) {
    failRuntime(runtime, "识别连接提前关闭，已保存当前内容");
  }
}

function handleAsrMessage(runtime, message) {
  let event;
  try {
    event = parseDashScopeEvent(message);
  } catch (_) {
    failRuntime(runtime, "识别服务返回了无法解析的数据");
    return;
  }
  if (event.type === "started") {
    runtime.taskStarted = true;
    runtime.started.settled = true;
    runtime.started.resolve();
    flushNetworkQueue(runtime);
    if (runtime.kind === "RECORDING") {
      appState.recording.connectionState = "live";
      renderRecording();
    }
    return;
  }
  if (event.type === "result" && !event.isHeartbeat) {
    applyTranscriptEvent(runtime, event);
    return;
  }
  if (event.type === "finished") {
    runtime.finished.settled = true;
    runtime.finished.resolve();
    return;
  }
  if (event.type === "failed") {
    failRuntime(runtime, `${event.code} · ${event.message}`);
  }
}

function applyTranscriptEvent(runtime, event) {
  const task = appState.tasks.find(item => item.id === runtime.taskId);
  if (!task) return;
  if (event.isFinal && event.text) {
    runtime.finalSentences.set(event.sentenceId, event.text);
    runtime.interim = "";
  } else {
    runtime.interim = event.text;
  }
  const currentText = [...runtime.finalSentences.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(entry => entry[1])
    .filter(Boolean)
    .join("\n");
  const transcript = [runtime.baseTranscript, currentText].filter(Boolean).join("\n");
  const changes = {
    transcript,
    characterCount: transcript.replace(/\s/g, "").length,
    preview: transcriptPreview(transcript) || "正在聆听…",
    billedDurationSeconds: event.billedDuration ?? task.billedDurationSeconds ?? null,
  };
  updateTask(task.id, changes, event.isFinal);
  if (runtime.kind === "RECORDING") {
    appState.recording.finalTranscript = transcript;
    appState.recording.interimTranscript = runtime.interim;
    renderRecording();
  } else {
    renderMedia();
  }
}

function failRuntime(runtime, message) {
  if (runtime.failure) return;
  runtime.failure = message;
  runtime.started.settled = true;
  runtime.finished.settled = true;
  runtime.started.reject(new Error(message));
  runtime.finished.reject(new Error(message));
  if (runtime.kind === "RECORDING" && activeRuntime === runtime) {
    appState.recording.errorMessage = message;
    appState.recording.connectionState = "offline";
    renderRecording();
  }
}

function friendlyNetworkError(message = "") {
  if (/HTTP 401|HTTP 403/i.test(message)) return "API Key、地域或 Workspace 配置未通过鉴权";
  if (/timeout|timed out/i.test(message)) return "连接识别服务超时，请检查网络";
  return message || "识别连接意外中断";
}

function openCloudSession(runtime) {
  if (!nativeBridge) return Promise.resolve();
  const response = JSON.parse(nativeBridge.openAsrSession(JSON.stringify({
    endpoint: dashScopeEndpoint(appState.settings),
    workspaceId: appState.settings.workspaceId,
  })));
  if (response.error) throw new Error(response.error);
  runtime.sessionId = response.sessionId;
  sessionRegistry.set(runtime.sessionId, runtime);
  return withTimeout(runtime.started.promise, 20000, "连接识别服务超时");
}

function createRuntime(kind, task) {
  const started = deferred();
  const finished = deferred();
  started.settled = false;
  finished.settled = false;
  started.promise.catch(() => {});
  finished.promise.catch(() => {});
  return {
    kind,
    taskId: task.id,
    cloudTaskId: createId(),
    sessionId: null,
    started,
    finished,
    taskStarted: false,
    closing: false,
    failure: null,
    baseTranscript: task.transcript || "",
    finalSentences: new Map(),
    interim: "",
    networkQueue: [],
    pendingSamples: new Int16Array(0),
    pcmParts: [],
    capturedSamples: Math.round((task.durationMillis || 0) * 16),
    mediaStream: null,
    audioContext: null,
    processor: null,
    source: null,
    resampler: null,
    lastVisualUpdate: 0,
  };
}

async function startRecording(resume = false) {
  if (!nativeBridge) return startMockRecording(resume);
  if (!appState.hasCredential) {
    showToast("请先在设置中保存百炼 API Key");
    setPage("settings");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    showToast("当前 WebView 无法使用网页麦克风，请更新 Android System WebView");
    return;
  }
  let task = resume
    ? appState.tasks.find(item => item.id === appState.recording.taskId)
    : null;
  if (!task) {
    const now = Date.now();
    task = {
      id: createId(), kind: "RECORDING", title: formatTaskTitle("录音"),
      createdAtEpochMillis: now, updatedAtEpochMillis: now,
      status: "PREPARING", durationMillis: 0, characterCount: 0,
      preview: "正在准备网页麦克风", transcript: "", segmentCount: 0, audioKeys: [],
    };
    appState.tasks.unshift(task);
    persistTasks();
  }
  appState.recording = idleRecording({
    phase: "STARTING", taskId: task.id, durationMillis: task.durationMillis,
    segmentCount: task.segmentCount || 0, finalTranscript: task.transcript || "",
    connectionState: "connecting",
  });
  updateTask(task.id, { status: "PREPARING", errorMessage: null });
  renderAll();
  const runtime = createRuntime("RECORDING", task);
  activeRuntime = runtime;
  try {
    await ensureNativeMicrophonePermission();
    runtime.mediaStream = await acquireMicrophoneStream();
    await attachMicrophone(runtime);
    await openCloudSession(runtime);
    if (activeRuntime !== runtime) return;
    const nextSegment = (task.segmentCount || 0) + 1;
    updateTask(task.id, { status: "RECORDING", segmentCount: nextSegment, preview: "正在实时转写" });
    appState.recording.phase = "RECORDING";
    appState.recording.segmentCount = nextSegment;
    appState.recording.connectionState = "live";
    renderRecording();
  } catch (error) {
    const message = friendlyMicrophoneError(error);
    await closeCapture(runtime);
    closeCloudSession(runtime);
    updateTask(task.id, { status: "RECOVERABLE", errorMessage: message, preview: "启动失败，可检查配置后重试" });
    appState.recording = idleRecording({ phase: "ERROR", taskId: task.id, errorMessage: message });
    activeRuntime = null;
    renderAll();
    showToast(message);
  }
}

async function ensureNativeMicrophonePermission() {
  if (!nativeBridge?.requestMicrophonePermission) return;
  const requestId = createId();
  const pending = deferred();
  microphonePermissionRegistry.set(requestId, pending);
  nativeBridge.requestMicrophonePermission(requestId);
  try {
    const granted = await withTimeout(pending.promise, 30000, "等待麦克风授权超时，请重新点击开始录音");
    if (!granted) {
      const error = new Error("麦克风权限未开启，请在系统应用设置中允许 JustSpeak 使用麦克风");
      error.name = "NotAllowedError";
      throw error;
    }
  } finally {
    microphonePermissionRegistry.delete(requestId);
  }
}

async function acquireMicrophoneStream() {
  let lastError;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const track = stream.getAudioTracks()[0];
      if (!track) {
        stream.getTracks().forEach(item => item.stop());
        const error = new Error("没有检测到可用麦克风");
        error.name = "NotFoundError";
        throw error;
      }
      runQuietly(() => { track.contentHint = "speech"; });
      if (typeof track.applyConstraints === "function") {
        await track.applyConstraints({
          channelCount: { ideal: 1 },
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
        }).catch(() => {});
      }
      return stream;
    } catch (error) {
      lastError = error;
      if (["NotAllowedError", "SecurityError", "PermissionDeniedError", "NotFoundError"].includes(error.name)) break;
      if (attempt === 0) await delay(500);
    }
  }
  throw lastError || new Error("无法启动麦克风");
}

async function attachMicrophone(runtime) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const audioContext = new AudioContextClass();
  await audioContext.resume();
  const source = audioContext.createMediaStreamSource(runtime.mediaStream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  const mute = audioContext.createGain();
  mute.gain.value = 0;
  runtime.audioContext = audioContext;
  runtime.source = source;
  runtime.processor = processor;
  runtime.resampler = new Pcm16Resampler(audioContext.sampleRate);
  processor.onaudioprocess = event => {
    if (activeRuntime !== runtime) return;
    const input = event.inputBuffer.getChannelData(0);
    const samples = runtime.resampler.process(input);
    consumeSamples(runtime, samples, true);
    const now = performance.now();
    if (now - runtime.lastVisualUpdate > 160) {
      let peak = 0;
      for (let index = 0; index < input.length; index += 32) peak = Math.max(peak, Math.abs(input[index]));
      appState.recording.amplitude = Math.min(1, peak * 2.8);
      appState.recording.durationMillis = Math.round(runtime.capturedSamples / 16);
      renderRecording();
      runtime.lastVisualUpdate = now;
    }
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(audioContext.destination);
}

function consumeSamples(runtime, incoming, savePcm) {
  if (!incoming.length) return;
  const combined = new Int16Array(runtime.pendingSamples.length + incoming.length);
  combined.set(runtime.pendingSamples);
  combined.set(incoming, runtime.pendingSamples.length);
  let offset = 0;
  while (combined.length - offset >= NETWORK_PACKET_SAMPLES) {
    const packet = combined.slice(offset, offset + NETWORK_PACKET_SAMPLES);
    const bytes = pcm16ToBytes(packet);
    if (savePcm) runtime.pcmParts.push(bytes);
    sendAudioPacket(runtime, bytes);
    runtime.capturedSamples += packet.length;
    offset += NETWORK_PACKET_SAMPLES;
  }
  runtime.pendingSamples = combined.slice(offset);
}

function flushPendingSamples(runtime, savePcm = true) {
  if (!runtime.pendingSamples.length) return;
  const bytes = pcm16ToBytes(runtime.pendingSamples);
  if (savePcm) runtime.pcmParts.push(bytes);
  sendAudioPacket(runtime, bytes);
  runtime.capturedSamples += runtime.pendingSamples.length;
  runtime.pendingSamples = new Int16Array(0);
}

function sendAudioPacket(runtime, bytes) {
  if (!nativeBridge) return;
  if (!runtime.taskStarted) {
    runtime.networkQueue.push(bytes);
    if (runtime.networkQueue.length > 300) failRuntime(runtime, "识别连接准备时间过长");
    return;
  }
  if (!nativeBridge.sendAsrAudio(runtime.sessionId, bytesToBase64(bytes))) {
    failRuntime(runtime, "音频发送失败，已保留本地分段");
  }
}

function flushNetworkQueue(runtime) {
  const queued = runtime.networkQueue.splice(0);
  for (const bytes of queued) {
    if (!nativeBridge.sendAsrAudio(runtime.sessionId, bytesToBase64(bytes))) {
      failRuntime(runtime, "积压音频发送失败");
      break;
    }
  }
}

async function pauseRecording() {
  if (!nativeBridge) {
    clearInterval(mockTimer);
    const task = appState.tasks.find(item => item.id === appState.recording.taskId);
    if (task) updateTask(task.id, { status: "PAUSED", durationMillis: appState.recording.durationMillis, transcript: appState.recording.finalTranscript, preview: transcriptPreview(appState.recording.finalTranscript) || "浏览器模拟录音已暂停" });
    appState.recording.phase = "PAUSED";
    appState.recording.amplitude = 0;
    renderAll();
    return;
  }
  if (!activeRuntime || activeRuntime.kind !== "RECORDING") return;
  const runtime = activeRuntime;
  appState.recording.phase = "STOPPING";
  renderRecording();
  await closeCapture(runtime);
  const audioKey = await saveRuntimeAudio(runtime);
  await finishCloudSession(runtime);
  const task = appState.tasks.find(item => item.id === runtime.taskId);
  const audioKeys = audioKey ? [...(task.audioKeys || []), audioKey] : (task.audioKeys || []);
  const durationMillis = Math.round(runtime.capturedSamples / 16);
  updateTask(task.id, {
    status: "PAUSED", durationMillis, audioKeys,
    preview: transcriptPreview(task.transcript) || "已暂停，音频分段保存在 WebView 数据库",
    errorMessage: runtime.failure,
  });
  appState.recording = idleRecording({
    phase: "PAUSED", taskId: task.id, durationMillis,
    segmentCount: task.segmentCount || 1, finalTranscript: task.transcript || "",
  });
  activeRuntime = null;
  renderAll();
  showToast("录音已暂停，网页分段已保存");
}

async function stopRecording() {
  if (!nativeBridge && ["RECORDING", "PAUSED"].includes(appState.recording.phase)) {
    clearInterval(mockTimer);
    const task = appState.tasks.find(item => item.id === appState.recording.taskId);
    if (task) updateTask(task.id, { status: "COMPLETED", durationMillis: appState.recording.durationMillis, transcript: appState.recording.finalTranscript, preview: transcriptPreview(appState.recording.finalTranscript) || "浏览器模拟录音已完成" });
    appState.recording.phase = "FINISHED";
    appState.recording.amplitude = 0;
    renderAll();
    return;
  }
  if (!activeRuntime && appState.recording.phase === "PAUSED") {
    const task = appState.tasks.find(item => item.id === appState.recording.taskId);
    if (task) {
      updateTask(task.id, { status: "COMPLETED", preview: transcriptPreview(task.transcript) || "录音中没有识别到文字" });
      appState.recording = idleRecording({ phase: "FINISHED", taskId: task.id, finalTranscript: task.transcript || "" });
      renderAll();
    }
    return;
  }
  if (!activeRuntime || activeRuntime.kind !== "RECORDING") return;
  const runtime = activeRuntime;
  appState.recording.phase = "STOPPING";
  renderRecording();
  await closeCapture(runtime);
  const audioKey = await saveRuntimeAudio(runtime);
  await finishCloudSession(runtime);
  const task = appState.tasks.find(item => item.id === runtime.taskId);
  const audioKeys = audioKey ? [...(task.audioKeys || []), audioKey] : (task.audioKeys || []);
  const durationMillis = Math.round(runtime.capturedSamples / 16);
  updateTask(task.id, {
    status: runtime.failure ? "RECOVERABLE" : "COMPLETED",
    durationMillis, audioKeys,
    preview: transcriptPreview(task.transcript) || (runtime.failure ? "云端识别未完成，本地音频已保存" : "未检测到可转写语音"),
    errorMessage: runtime.failure,
  });
  appState.recording = idleRecording({
    phase: runtime.failure ? "ERROR" : "FINISHED", taskId: task.id,
    durationMillis, segmentCount: task.segmentCount || 1,
    finalTranscript: task.transcript || "", errorMessage: runtime.failure,
  });
  activeRuntime = null;
  renderAll();
  showToast(runtime.failure ? "文稿未完整，本地音频已保存" : "录音与文稿已保存");
}

async function closeCapture(runtime) {
  runtime.processor && (runtime.processor.onaudioprocess = null);
  runQuietly(() => runtime.source?.disconnect());
  runQuietly(() => runtime.processor?.disconnect());
  runtime.mediaStream?.getTracks().forEach(track => track.stop());
  if (runtime.audioContext && runtime.audioContext.state !== "closed") {
    await runtime.audioContext.close().catch(() => {});
  }
  flushPendingSamples(runtime);
}

async function finishCloudSession(runtime) {
  if (!nativeBridge || !runtime.sessionId) return;
  runtime.closing = true;
  if (runtime.taskStarted && !runtime.failure) {
    nativeBridge.sendAsrText(runtime.sessionId, JSON.stringify(buildFinishTask(runtime.cloudTaskId)));
    await withTimeout(runtime.finished.promise, 30000, "等待最后识别结果超时").catch(error => {
      runtime.failure ||= error.message;
    });
  }
  closeCloudSession(runtime);
}

function closeCloudSession(runtime) {
  if (!runtime.sessionId) return;
  sessionRegistry.delete(runtime.sessionId);
  nativeBridge?.closeAsrSession(runtime.sessionId);
  runtime.sessionId = null;
}

async function saveRuntimeAudio(runtime) {
  if (!runtime.pcmParts.length) return null;
  const key = `${runtime.taskId}:${Date.now()}`;
  try {
    await audioStorePut(key, new Blob(runtime.pcmParts, { type: "application/octet-stream" }));
    return key;
  } catch (_) {
    showToast("本地音频存储失败，文稿仍已保留");
    return null;
  }
}

async function prepareMedia(file) {
  if (!file) return;
  selectedMedia = { file, name: file.name, sizeBytes: file.size, type: file.type, progress: 0, status: "ready" };
  renderMedia();
}

async function transcribeSelectedMedia() {
  if (!selectedMedia?.file || activeRuntime) return;
  if (!nativeBridge) return runMockFileTask();
  if (!appState.hasCredential) {
    showToast("请先在设置中保存百炼 API Key");
    setPage("settings");
    return;
  }
  const now = Date.now();
  const task = {
    id: createId(), kind: "MEDIA_FILE", title: selectedMedia.name,
    createdAtEpochMillis: now, updatedAtEpochMillis: now,
    status: "PREPARING", durationMillis: 0, characterCount: 0,
    preview: "正在网页内解码音频", transcript: "", segmentCount: 1, audioKeys: [],
  };
  appState.tasks.unshift(task);
  persistTasks();
  const runtime = createRuntime("MEDIA_FILE", task);
  activeRuntime = runtime;
  selectedMedia.status = "decoding";
  renderAll();
  try {
    const fileBuffer = await selectedMedia.file.arrayBuffer();
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContextClass();
    const decoded = await context.decodeAudioData(fileBuffer.slice(0));
    await context.close();
    const mono = downmixAudioBuffer(decoded);
    const resampler = new Pcm16Resampler(decoded.sampleRate);
    const parts = [];
    for (let offset = 0; offset < mono.length; offset += 16384) {
      const output = resampler.process(mono.subarray(offset, offset + 16384));
      if (output.length) parts.push(output);
    }
    const totalSamples = parts.reduce((sum, part) => sum + part.length, 0);
    updateTask(task.id, { status: "TRANSCRIBING", durationMillis: Math.round(totalSamples / 16), preview: "正在云端识别" });
    selectedMedia.status = "transcribing";
    await openCloudSession(runtime);
    let sentSamples = 0;
    for (const part of parts) {
      let offset = 0;
      while (offset < part.length) {
        if (activeRuntime !== runtime) throw new Error("文件任务已取消");
        const packet = part.slice(offset, offset + NETWORK_PACKET_SAMPLES);
        sendAudioPacket(runtime, pcm16ToBytes(packet));
        sentSamples += packet.length;
        selectedMedia.progress = totalSamples ? sentSamples / totalSamples : 1;
        renderMedia();
        offset += packet.length;
        await delay(Math.max(24, packet.length / 16));
      }
    }
    await finishCloudSession(runtime);
    const latest = appState.tasks.find(item => item.id === task.id);
    updateTask(task.id, {
      status: runtime.failure ? "RECOVERABLE" : "COMPLETED",
      preview: transcriptPreview(latest.transcript) || (runtime.failure ? "识别未完成" : "未检测到可转写语音"),
      errorMessage: runtime.failure,
    });
    selectedMedia.status = runtime.failure ? "error" : "done";
    selectedMedia.progress = 1;
    activeRuntime = null;
    renderAll();
    openTaskDetail(task.id);
  } catch (error) {
    closeCloudSession(runtime);
    updateTask(task.id, { status: "FAILED", errorMessage: error.message, preview: "文件处理失败" });
    selectedMedia.status = "error";
    selectedMedia.error = error.message;
    activeRuntime = null;
    renderAll();
    showToast(error.message || "文件转写失败");
  }
}

function downmixAudioBuffer(buffer) {
  if (buffer.numberOfChannels === 1) return buffer.getChannelData(0).slice();
  const mono = new Float32Array(buffer.length);
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const input = buffer.getChannelData(channel);
    for (let index = 0; index < input.length; index += 1) mono[index] += input[index] / buffer.numberOfChannels;
  }
  return mono;
}

async function exportTaskAudio(task) {
  if (!task.audioKeys?.length) {
    showToast("这条记录没有可导出的本地音频");
    return;
  }
  try {
    const pcmParts = [];
    for (const key of task.audioKeys) {
      const blob = await audioStoreGet(key);
      if (blob) pcmParts.push(new Uint8Array(await blob.arrayBuffer()));
    }
    if (!pcmParts.length) throw new Error("本地音频已经不可用");
    downloadBlob(buildWavBlob(pcmParts), `${safeFileName(task.title)}.wav`);
    showToast("正在导出 WAV");
  } catch (error) {
    showToast(error.message || "WAV 导出失败");
  }
}

function renderAll() {
  renderHome();
  renderHistory();
  renderRecording();
  renderSettings();
  renderMedia();
  renderTaskDetail();
}

function renderHome() {
  const totalDurationMillis = appState.tasks.reduce((sum, task) => sum + (task.durationMillis || 0), 0);
  $("#usage-duration").textContent = String(Math.round(totalDurationMillis / 60000));
  $("#home-title").textContent = `${greeting()}，继续记录`;
  $("#recent-list").innerHTML = taskListHtml(appState.tasks.slice(0, 3), "第一条声音记录会出现在这里。");
}

function renderHistory() {
  const tasks = appState.tasks.filter(task => {
    if (historyFilter === "ALL") return true;
    if (historyFilter === "UNFINISHED") return task.status !== "COMPLETED";
    return task.kind === historyFilter;
  });
  $("#history-count").textContent = String(appState.tasks.length);
  $("#history-list").innerHTML = taskListHtml(tasks, "还没有符合条件的记录。");
}

function taskListHtml(tasks, emptyCopy) {
  if (!tasks.length) return `<div class="empty-state">${icon("i-cloud")}<p>${escapeHtml(emptyCopy)}</p></div>`;
  return tasks.map(task => {
    const statusClass = task.status === "RECORDING" || task.status === "TRANSCRIBING"
      ? "recording" : task.status === "COMPLETED" ? "completed" : "";
    const kindIcon = task.kind === "RECORDING" ? "i-mic" : "i-file";
    return `<button class="task-card pressable" data-task-id="${escapeHtml(task.id)}">
      <span class="task-icon ${task.kind === "MEDIA_FILE" ? "sky" : ""}">${icon(kindIcon)}</span>
      <span class="task-copy"><b>${escapeHtml(task.title)}</b><small>${escapeHtml(task.preview || "等待处理")}</small><em>${formatDate(task.updatedAtEpochMillis)} · ${formatDuration(task.durationMillis)}</em></span>
      <span class="task-status ${statusClass}">${escapeHtml(statusLabels[task.status] || task.status)}</span>
    </button>`;
  }).join("");
}

function renderRecording() {
  const recording = appState.recording;
  const [label, className, defaultCaption] = phaseMeta[recording.phase] || phaseMeta.IDLE;
  const status = $("#record-status");
  status.className = `status-chip ${className}`;
  status.innerHTML = `<i></i>${label}`;
  $("#record-time").textContent = formatDuration(recording.durationMillis);
  $("#record-caption").textContent = recording.errorMessage || (!appState.hasCredential && recording.phase === "IDLE" ? "配置云端凭据后即可开始" : defaultCaption);
  const segment = $("#segment-label");
  segment.hidden = !recording.segmentCount;
  segment.textContent = `${recording.segmentCount || 0} 个网页音频分段`;
  const orb = $("#record-orb");
  orb.className = `record-orb ${className}`;
  orb.style.setProperty("--amplitude", Math.max(0, Math.min(1, recording.amplitude || 0)).toFixed(3));
  const finalText = recording.finalTranscript || "";
  $("#live-final").textContent = finalText;
  $("#live-final").hidden = !finalText;
  $("#live-interim").textContent = recording.interimTranscript || "";
  $("#live-interim").hidden = !recording.interimTranscript;
  $("#live-empty").hidden = Boolean(finalText || recording.interimTranscript);
  $("#cloud-state").textContent = ({ live: "云端在线", connecting: "连接中", offline: "连接中断" })[recording.connectionState] || "等待开始";
  if (lastRenderedPhase !== recording.phase) {
    $("#record-controls").innerHTML = recordingControls(recording.phase);
    lastRenderedPhase = recording.phase;
  }
}

function recordingControls(phase) {
  if (phase === "RECORDING") return `
    <button class="control-button pressable" data-record-action="pause">${icon("i-pause")}暂停</button>
    <button class="control-button stop pressable" data-record-action="stop">${icon("i-stop")}结束</button>`;
  if (phase === "PAUSED") return `
    <button class="control-button pressable" data-record-action="resume">${icon("i-play")}继续录音</button>
    <button class="control-button stop pressable" data-record-action="stop">${icon("i-stop")}保存结束</button>`;
  if (["STARTING", "STOPPING"].includes(phase)) return `<button class="primary-button" disabled><span class="button-spinner"></span>请稍候</button>`;
  const text = appState.hasCredential ? (phase === "FINISHED" ? "开始新录音" : "开始录音") : "先去配置";
  return `<button class="primary-button pressable" data-record-action="start">${icon(appState.hasCredential ? "i-mic" : "i-settings")}${text}</button>`;
}

function renderMedia() {
  const picker = $("#media-picker");
  const card = $("#media-selected");
  const progress = $("#file-progress");
  picker.hidden = Boolean(selectedMedia);
  card.hidden = !selectedMedia;
  if (!selectedMedia) {
    progress.hidden = true;
    $("#start-file").disabled = true;
    return;
  }
  card.innerHTML = `${icon("i-file")}<span class="media-copy"><b>${escapeHtml(selectedMedia.name)}</b><small>${formatBytes(selectedMedia.sizeBytes)} · ${mediaStatusCopy(selectedMedia)}</small></span><button class="avatar-button pressable" data-clear-media aria-label="移除文件">×</button>`;
  const processing = ["decoding", "transcribing"].includes(selectedMedia.status);
  progress.hidden = !processing;
  progress.style.setProperty("--progress", `${Math.round((selectedMedia.progress || 0) * 100)}%`);
  progress.querySelector("span").textContent = selectedMedia.status === "decoding" ? "网页内解码中" : `云端转写 ${Math.round((selectedMedia.progress || 0) * 100)}%`;
  $("#start-file").disabled = processing;
  $("#start-file").innerHTML = processing ? `<span class="button-spinner"></span>处理中` : `${icon("i-play")}${selectedMedia.status === "done" ? "再次转写" : "开始转写"}`;
}

function mediaStatusCopy(media) {
  if (media.status === "done") return "转写完成";
  if (media.status === "error") return media.error || "处理失败";
  if (media.status === "transcribing") return "正在发送标准 PCM";
  if (media.status === "decoding") return "正在解析音轨";
  return "已准备";
}

function renderSettings() {
  const status = $("#credential-status");
  status.className = `status-chip ${appState.hasCredential ? "completed" : "idle"}`;
  status.innerHTML = `<i></i>${appState.hasCredential ? "已保护" : "未配置"}`;
  const signature = JSON.stringify(appState.settings);
  if (signature !== lastSettingsSignature) {
    const form = $("#settings-form");
    Object.entries(appState.settings).forEach(([key, value]) => {
      const field = form.elements[key];
      if (!field) return;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else field.value = value ?? "";
    });
    lastSettingsSignature = signature;
  }
  document.documentElement.dataset.theme = appState.settings.darkTheme ? "dark" : "light";
  if (lastNativeTheme !== appState.settings.darkTheme) {
    nativeBridge?.setSystemTheme(appState.settings.darkTheme);
    lastNativeTheme = appState.settings.darkTheme;
  }
}

function openTaskDetail(taskId) {
  detailTaskId = taskId;
  renderTaskDetail();
  const backdrop = $("#detail-backdrop");
  backdrop.hidden = false;
  requestAnimationFrame(() => backdrop.classList.add("is-visible"));
}

function closeTaskDetail() {
  const backdrop = $("#detail-backdrop");
  backdrop.classList.remove("is-visible");
  setTimeout(() => { backdrop.hidden = true; detailTaskId = null; }, 220);
}

function renderTaskDetail() {
  if (!detailTaskId) return;
  const task = appState.tasks.find(item => item.id === detailTaskId);
  if (!task) return closeTaskDetail();
  $("#detail-title").textContent = task.title;
  $("#detail-meta").textContent = `${statusLabels[task.status] || task.status} · ${formatDuration(task.durationMillis)} · ${task.characterCount || 0} 字`;
  $("#detail-transcript").textContent = task.transcript || task.errorMessage || "这条任务还没有生成文字。";
  $("#detail-audio").hidden = !task.audioKeys?.length;
}

function handleClick(event) {
  const nav = event.target.closest("[data-page]");
  if (nav) return setPage(nav.dataset.page);
  const go = event.target.closest("[data-go]");
  if (go) return setPage(go.dataset.go);
  const action = event.target.closest("[data-record-action]")?.dataset.recordAction;
  if (action === "start") return startRecording(appState.recording.phase === "ERROR" && Boolean(appState.recording.taskId));
  if (action === "pause") return pauseRecording();
  if (action === "resume") return startRecording(true);
  if (action === "stop") return stopRecording();
  if (event.target.closest("#scenario-button")) return openScenarioSheet();
  const scenario = event.target.closest("[data-scenario]");
  if (scenario) {
    $$("[data-scenario]").forEach(button => button.classList.toggle("is-active", button === scenario));
    $("#scenario-button span").textContent = scenario.dataset.scenario;
    closeScenarioSheet();
    return;
  }
  if (event.target.id === "sheet-backdrop") return closeScenarioSheet();
  if (event.target.id === "detail-backdrop" || event.target.closest("[data-close-detail]")) return closeTaskDetail();
  if (event.target.closest("#media-picker")) return $("#media-input").click();
  if (event.target.closest("[data-clear-media]")) { selectedMedia = null; renderMedia(); return; }
  if (event.target.closest("#start-file")) return transcribeSelectedMedia();
  const taskId = event.target.closest("[data-task-id]")?.dataset.taskId;
  if (taskId) return openTaskDetail(taskId);
  const filter = event.target.closest("[data-filter]");
  if (filter) {
    historyFilter = filter.dataset.filter;
    $$("[data-filter]").forEach(button => button.classList.toggle("is-active", button === filter));
    renderHistory();
    return;
  }
  const detailAction = event.target.closest("[data-detail-action]")?.dataset.detailAction;
  if (detailAction) handleDetailAction(detailAction);
}

function handleDetailAction(action) {
  const task = appState.tasks.find(item => item.id === detailTaskId);
  if (!task) return;
  if (action === "copy") return nativeBridge ? nativeBridge.copyText(task.transcript || "") : copyBrowser(task.transcript || "");
  if (action === "share") return nativeBridge?.shareText(task.title, task.transcript || "") || copyBrowser(task.transcript || "");
  if (action === "txt") return nativeBridge
    ? nativeBridge.exportText(`${safeFileName(task.title)}.txt`, task.transcript || "")
    : downloadBlob(new Blob([task.transcript || ""], { type: "text/plain;charset=utf-8" }), `${safeFileName(task.title)}.txt`);
  if (action === "audio") return exportTaskAudio(task);
}

function bindSettings() {
  const form = $("#settings-form");
  form.addEventListener("submit", event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    data.darkTheme = form.elements.darkTheme.checked;
    data.historyLimit = Number(data.historyLimit || 50);
    appState.settings = normalizeSettings(data);
    persistSettings();
    lastSettingsSignature = "";
    renderAll();
    showToast("网页设置已保存");
  });
  form.elements.darkTheme.addEventListener("change", event => {
    appState.settings.darkTheme = event.target.checked;
    persistSettings();
    renderSettings();
  });
  $("#save-key").addEventListener("click", () => {
    const value = $("#api-key").value.trim();
    if (!value) return showToast("请输入 API Key");
    if (!nativeBridge) {
      appState.hasCredential = true;
      renderAll();
      showToast("浏览器预览：凭据状态已模拟");
      return;
    }
    nativeBridge.saveApiKey(value);
    $("#api-key").value = "";
  });
  $("#clear-key").addEventListener("click", () => {
    if (nativeBridge) nativeBridge.clearApiKey();
    else { appState.hasCredential = false; renderAll(); }
  });
}

function setPage(page, withHaptic = true) {
  if (!pageOrder.includes(page) || page === activePage) return;
  activePage = page;
  $$("[data-screen]").forEach(screen => screen.classList.toggle("is-active", screen.dataset.screen === page));
  $$("[data-page]").forEach(button => button.classList.toggle("is-active", button.dataset.page === page));
  $("#screen-stage").scrollTo({ top: 0, behavior: "instant" });
  if (withHaptic) nativeBridge?.haptic();
}

function bindSwipeNavigation() {
  const stage = $("#screen-stage");
  let startX = 0;
  let startY = 0;
  let allowed = false;
  stage.addEventListener("pointerdown", event => {
    allowed = event.pointerType !== "mouse" && !event.target.closest("button,input,textarea,select,summary");
    startX = event.clientX;
    startY = event.clientY;
  }, { passive: true });
  stage.addEventListener("pointerup", event => {
    if (!allowed) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (Math.abs(dx) < 58 || Math.abs(dx) < Math.abs(dy) * 1.35) return;
    const target = pageOrder.indexOf(activePage) + (dx < 0 ? 1 : -1);
    if (target >= 0 && target < pageOrder.length) setPage(pageOrder[target]);
  }, { passive: true });
}

function openScenarioSheet() {
  const backdrop = $("#sheet-backdrop");
  backdrop.hidden = false;
  requestAnimationFrame(() => backdrop.classList.add("is-visible"));
}

function closeScenarioSheet() {
  const backdrop = $("#sheet-backdrop");
  backdrop.classList.remove("is-visible");
  setTimeout(() => { backdrop.hidden = true; }, 220);
}

function showToast(message) {
  if (!message) return;
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

function createWave() {
  $("#live-wave").innerHTML = Array.from({ length: 15 }, (_, index) => {
    const bar = .22 + Math.abs(Math.sin(index * 1.17)) * .78;
    return `<i style="--bar:${bar.toFixed(2)};--index:${index}"></i>`;
  }).join("");
}

function startMockRecording(resume) {
  clearInterval(mockTimer);
  const now = Date.now();
  let task = resume ? appState.tasks.find(item => item.id === appState.recording.taskId) : null;
  if (!task) {
    task = { id: createId(), kind: "RECORDING", title: formatTaskTitle("录音预览"), createdAtEpochMillis: now, updatedAtEpochMillis: now, status: "RECORDING", durationMillis: 0, characterCount: 0, preview: "浏览器模拟转写中", transcript: "", segmentCount: 1, audioKeys: [] };
    appState.tasks.unshift(task);
  }
  appState.recording = idleRecording({ phase: "STARTING", taskId: task.id, durationMillis: task.durationMillis, segmentCount: task.segmentCount, finalTranscript: task.transcript, connectionState: "connecting" });
  renderAll();
  setTimeout(() => {
    appState.recording.phase = "RECORDING";
    appState.recording.connectionState = "live";
    mockTimer = setInterval(() => {
      appState.recording.durationMillis += 200;
      appState.recording.amplitude = .08 + Math.random() * .68;
      const seconds = appState.recording.durationMillis / 1000;
      appState.recording.interimTranscript = seconds > 1.4 ? "这是一段浏览器中的实时转写预览…" : "正在聆听…";
      if (seconds > 4) {
        appState.recording.finalTranscript = "这是一段浏览器中的实时转写预览。";
        appState.recording.interimTranscript = "";
        updateTask(task.id, { transcript: appState.recording.finalTranscript, preview: appState.recording.finalTranscript, characterCount: 17 }, false);
      }
      renderRecording();
    }, 200);
    renderRecording();
  }, 520);
}

function runMockFileTask() {
  selectedMedia.status = "transcribing";
  let progress = 0;
  const timer = setInterval(() => {
    progress += .08;
    selectedMedia.progress = Math.min(1, progress);
    renderMedia();
    if (progress >= 1) {
      clearInterval(timer);
      selectedMedia.status = "done";
      const now = Date.now();
      const task = { id: createId(), kind: "MEDIA_FILE", title: selectedMedia.name, createdAtEpochMillis: now, updatedAtEpochMillis: now, status: "COMPLETED", durationMillis: 96000, characterCount: 25, preview: "浏览器预览已完成文件转写交互。", transcript: "浏览器预览已完成文件转写交互。安装 APK 后会连接真实服务。", segmentCount: 1, audioKeys: [] };
      appState.tasks.unshift(task);
      renderAll();
      openTaskDetail(task.id);
    }
  }, 120);
}

function withTimeout(promise, milliseconds, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(message)), milliseconds)),
  ]);
}

function delay(milliseconds) { return new Promise(resolve => setTimeout(resolve, milliseconds)); }
function runQuietly(action) { try { action(); } catch (_) { /* resource already closed */ } }

function formatDuration(milliseconds = 0) {
  const total = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map(value => String(value).padStart(2, "0")).join(":");
}

function formatDate(timestamp) {
  if (!timestamp) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
}

function formatBytes(bytes) {
  if (bytes == null) return "大小未知";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 11) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function formatTaskTitle(suffix) {
  const date = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()).replace(/\//g, "-");
  return `${date} ${suffix}`;
}

function safeFileName(value) { return String(value || "JustSpeak-文稿").replace(/[\\/:*?"<>|]/g, "_"); }

function copyBrowser(text) {
  navigator.clipboard?.writeText(text).then(() => showToast("文稿已复制")).catch(() => showToast("浏览器不允许访问剪贴板"));
}

function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function audioDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("justspeak-audio-v1", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("segments");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function audioStorePut(key, blob) {
  const database = await audioDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("segments", "readwrite");
    transaction.objectStore("segments").put(blob, key);
    transaction.oncomplete = () => { database.close(); resolve(); };
    transaction.onerror = () => { database.close(); reject(transaction.error); };
  });
}

async function audioStoreGet(key) {
  const database = await audioDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("segments", "readonly");
    const request = transaction.objectStore("segments").get(key);
    request.onsuccess = () => { database.close(); resolve(request.result || null); };
    request.onerror = () => { database.close(); reject(request.error); };
  });
}

function init() {
  createWave();
  document.addEventListener("click", handleClick);
  bindSettings();
  bindSwipeNavigation();
  $("#media-input").addEventListener("change", event => prepareMedia(event.target.files?.[0]));
  renderAll();
  if (!nativeBridge) setTimeout(() => showToast("浏览器预览模式 · 云端能力使用模拟数据"), 480);
}

init();
