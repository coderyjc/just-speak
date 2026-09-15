import test from "node:test";
import assert from "node:assert/strict";
import {
  Pcm16Resampler,
  buildFinishTask,
  buildRunTask,
  buildWavBlob,
  dashScopeEndpoint,
  friendlyMicrophoneError,
  parseDashScopeEvent,
  pcm16ToBytes,
  transcriptPreview,
} from "../../app/src/main/assets/web-core.js";

test("workspace endpoint follows selected region", () => {
  assert.equal(
    dashScopeEndpoint({ region: "beijing", workspaceId: "ws-demo" }),
    "wss://ws-demo.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
  );
  assert.equal(
    dashScopeEndpoint({ region: "singapore", workspaceId: "ws-demo" }),
    "wss://ws-demo.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference",
  );
});

test("run and finish commands match DashScope duplex protocol", () => {
  const run = buildRunTask("task-1", { language: "zh", asrPrompt: "JustSpeak" });
  assert.equal(run.header.action, "run-task");
  assert.equal(run.header.streaming, "duplex");
  assert.equal(run.payload.parameters.sample_rate, 16000);
  assert.deepEqual(run.payload.parameters.language_hints, ["zh"]);
  assert.equal(run.payload.input.context[0].content[0].text, "JustSpeak");
  assert.equal(buildFinishTask("task-1").header.action, "finish-task");
});

test("server result parser separates interim and final sentences", () => {
  const event = parseDashScopeEvent(JSON.stringify({
    header: { event: "result-generated", task_id: "task-1" },
    payload: {
      output: { sentence: { sentence_id: 3, text: "测试完成", sentence_end: true, begin_time: 10, end_time: 900 } },
      usage: { duration: 1 },
    },
  }));
  assert.equal(event.type, "result");
  assert.equal(event.text, "测试完成");
  assert.equal(event.isFinal, true);
  assert.equal(event.billedDuration, 1);
});

test("48 kHz samples are converted to 16 kHz PCM16", () => {
  const input = new Float32Array(48001);
  for (let index = 0; index < input.length; index += 1) input[index] = Math.sin(index / 12);
  const output = new Pcm16Resampler(48000).process(input);
  assert.equal(output.length, 16000);
  const bytes = pcm16ToBytes(new Int16Array([0x1234, -2]));
  assert.deepEqual([...bytes], [0x34, 0x12, 0xfe, 0xff]);
});

test("WAV export writes a valid mono PCM header", async () => {
  const blob = buildWavBlob([new Uint8Array([1, 2, 3, 4])]);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), "RIFF");
  assert.equal(new DataView(bytes.buffer).getUint32(24, true), 16000);
  assert.equal(new DataView(bytes.buffer).getUint32(40, true), 4);
  assert.equal(transcriptPreview("  一段   文稿  "), "一段 文稿");
});

test("microphone startup errors become actionable Chinese guidance", () => {
  assert.equal(
    friendlyMicrophoneError({ name: "NotReadableError", message: "Could not start audio source" }),
    "麦克风启动失败，请关闭占用麦克风的通话或录音应用，并检查系统麦克风总开关",
  );
  assert.match(friendlyMicrophoneError({ name: "NotAllowedError" }), /系统应用设置/);
});
