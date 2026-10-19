import * as ort from "onnxruntime-web/webgpu";
import {MODEL_PATH, RUNTIME_PATH, UNSUPPORTED_CLEANUP} from "../services/browserCleanupProtocol";
import type {CleanupRequest, CleanupReply} from "../services/browserCleanupProtocol";

const scope = self as unknown as DedicatedWorkerGlobalScope;
let session: ort.InferenceSession | null = null;
let initializing: Promise<void> | null = null;
const send = (message: CleanupReply) => scope.postMessage(message);
class Unsupported extends Error {}
class DownloadFailed extends Error {}

async function modelBytes() {
  // Cache Storage is optional (private mode/quota pressure must not break cleanup).
  let cache: Cache | undefined;
  try {cache = await caches.open("recastfx-reference-model-v1");} catch { /* HTTP caching remains available. */ }
  let response = await cache?.match(MODEL_PATH).catch(() => undefined);
  if (!response) {
    try {response = await fetch(MODEL_PATH, {credentials: "omit", cache: "force-cache"});}
    catch {throw new DownloadFailed();}
    if (!response.ok || !response.body) throw new DownloadFailed();
    const copy = response.clone();
    void cache?.put(MODEL_PATH, copy).catch(() => undefined);
  }
  if (!response.body) throw new DownloadFailed();
  const reader = response.body.getReader();
  const bytes = new Uint8Array(98_484_532);
  let length = 0, previousPercent = -1;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 98_484_532) {await reader.cancel(); throw new DownloadFailed();}
    bytes.set(value, length - value.byteLength);
    const percent = Math.floor(length / 98_484_532 * 100);
    if (percent !== previousPercent) {send({type: "progress", progress: {phase: "loading", percent}}); previousPercent = percent;}
  }
  if (length !== 98_484_532) {void cache?.delete(MODEL_PATH); throw new DownloadFailed();}
  // Also validate cached bytes before executing them as a graph.
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(n => n.toString(16).padStart(2,"0")).join("");
  if (hash !== "eff9216bb2f9d3f023d9c2b7196845a7485739ab1f231593633e4d2344ffc516") {
    void cache?.delete(MODEL_PATH); throw new DownloadFailed();
  }
  return bytes;
}

async function initialize() {
  if (session) return;
  if (!navigator.gpu || typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function") throw new Unsupported();
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter || !adapter.features.has("shader-f16")) throw new Unsupported();
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = {
    mjs: new URL(RUNTIME_PATH + "ort-wasm-simd-threaded.asyncify.mjs", location.origin).href,
    wasm: new URL(RUNTIME_PATH + "ort-wasm-simd-threaded.asyncify.wasm", location.origin).href,
  };
  const bytes = await modelBytes();
  try {session = await ort.InferenceSession.create(bytes, {executionProviders: ["webgpu"], graphOptimizationLevel: "all"});}
  catch {throw new Unsupported();}
}

async function clean(image: Blob) {
  if (!image.size || image.size > 5*1024*1024) throw new Error("Choose a reference smaller than 5 MiB.");
  let bitmap: ImageBitmap;
  try {bitmap = await createImageBitmap(image);} catch {throw new Error("That reference could not be opened. Use the original or another image.");}
  const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale), height = Math.round(bitmap.height * scale);
  if (!width || !height || bitmap.width > 5760 || bitmap.height > 5760) {bitmap.close(); throw new Error("This reference is too large. Use the original or a smaller image.");}
  const original = new OffscreenCanvas(width, height);
  const context = original.getContext("2d", {willReadFrequently: true});
  const small = new OffscreenCanvas(512, 512);
  const smallContext = small.getContext("2d", {willReadFrequently: true});
  if (!context || !smallContext) {bitmap.close(); throw new Unsupported();}
  context.drawImage(bitmap, 0, 0, width, height); bitmap.close();
  smallContext.fillStyle = "#808080"; smallContext.fillRect(0,0,512,512); smallContext.drawImage(original,0,0,512,512);
  const rgba = smallContext.getImageData(0,0,512,512).data;
  const input = new Float32Array(3 * 512 * 512);
  const mean = [.485,.456,.406], std = [.229,.224,.225];
  for (let i=0;i<512*512;i++) for (let c=0;c<3;c++) input[c*512*512+i]=(rgba[i*4+c]/255-mean[c])/std[c];
  const tensor = new ort.Tensor("float32", input, [1,3,512,512]);
  let outputs: ort.InferenceSession.ReturnType;
  try {outputs = await session!.run({input_image: tensor});}
  catch {throw new Unsupported();}
  finally {tensor.dispose();}
  try {
    const output = outputs[session!.outputNames[0]];
    if (output.type !== "float32" || output.size !== 512*512) throw new Unsupported();
    const logits = await output.getData() as Float32Array;
    const matte = smallContext.createImageData(512,512);
    let foreground = 0;
    for (let i=0;i<logits.length;i++) {
      if (!Number.isFinite(logits[i])) throw new Unsupported();
      const alpha = Math.round(255/(1+Math.exp(-logits[i])));
      if (alpha > 127) foreground++;
      matte.data[i*4]=matte.data[i*4+1]=matte.data[i*4+2]=alpha;matte.data[i*4+3]=255;
    }
    if (foreground < 16) throw new Error("No clear subject was found. Use the original or another image.");
    smallContext.putImageData(matte,0,0);
    const mask = new OffscreenCanvas(width,height);
    const maskContext = mask.getContext("2d", {willReadFrequently:true});
    if (!maskContext) throw new Unsupported();
    maskContext.drawImage(small,0,0,width,height);
    const alpha = maskContext.getImageData(0,0,width,height).data;
    const pixels = context.getImageData(0,0,width,height);
    for (let i=0;i<pixels.data.length;i+=4) {
      const a=alpha[i]/255*pixels.data[i+3]/255;
      for(let c=0;c<3;c++) pixels.data[i+c]=Math.round(pixels.data[i+c]*a+128*(1-a));
      pixels.data[i+3]=255;
    }
    context.putImageData(pixels,0,0);
    return await original.convertToBlob({type:"image/jpeg",quality:.95});
  } finally {for (const output of Object.values(outputs)) output.dispose();}
}

scope.onmessage = async ({data}: MessageEvent<CleanupRequest>) => {
  try {
    if (data.type === "initialize") {
      initializing ??= initialize(); await initializing; send({type:"ready"});
    } else {
      if (!session) throw new Unsupported();
      send({type:"progress",progress:{phase:"cleaning"}});
      send({type:"result",id:data.id,image:await clean(data.image)});
    }
  } catch (error) {
    const kind = error instanceof Unsupported ? "unsupported" : error instanceof DownloadFailed ? "download" : "image";
    send({type:"error", id:data.type==="clean"?data.id:undefined,kind,message:kind==="unsupported"?UNSUPPORTED_CLEANUP:kind==="download"?"The cleanup model could not be downloaded. Use Original or try again.":error instanceof Error?error.message:"Cleanup failed. Use the original image."});
  }
};
