import {applyCleanupWhite, encodeCleanupPng} from "./cleanupImage";
import {loadLiteRt, loadAndCompile, Tensor} from "@litertjs/core";
import type {CompiledModel} from "@litertjs/core";
import {CPU_MODEL_CACHE, CPU_MODEL_PATH, CPU_RUNTIME_PATH, UNSUPPORTED_CLEANUP} from "../services/browserCleanupProtocol";
import type {CleanupReply, CleanupRequest} from "../services/browserCleanupProtocol";

const scope = self as unknown as DedicatedWorkerGlobalScope;
const send = (message: CleanupReply) => scope.postMessage(message);
let model: CompiledModel | undefined;
let initializing: Promise<void> | undefined;
class DownloadFailed extends Error {}
class Unsupported extends Error {}

async function modelBytes() {
  // One bounded allocation, no Response.clone/cache tee buffering on iOS.
  // The build verifies the pinned model hash; HTTPS serves only that revision.
  let response:Response|undefined;
  try {response=await (await caches.open(CPU_MODEL_CACHE)).match(CPU_MODEL_PATH);} catch { /* Optional disk cache. */ }
  response ??= await fetch(CPU_MODEL_PATH, {credentials:"omit",cache:"force-cache"});
  if (!response.ok || !response.body) throw new DownloadFailed();
  const bytes = new Uint8Array(176_240_148);
  const reader = response.body.getReader();
  let length = 0, previous = -1;
  while (true) {
    const {done,value} = await reader.read();
    if (done) break;
    if (length + value.length > bytes.length) {await reader.cancel(); throw new DownloadFailed();}
    bytes.set(value,length); length += value.length;
    const percent = Math.floor(length/bytes.length*100);
    if (percent !== previous) {previous = percent; send({type:"progress",progress:{phase:"loading",percent,downloadMB:176}});}
  }
  if (length !== bytes.length) throw new DownloadFailed();
  return bytes;
}
async function initialize() {
  if (typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function") throw new Unsupported();
  // LiteRT's compatibility loader uses importScripts: this is a classic worker,
  // built separately as an IIFE. No WebGPU request or ONNX asyncify on this path.
  await loadLiteRt(new URL(CPU_RUNTIME_PATH,location.origin).href,{jspi:false,threads:false});
  const bytes = await modelBytes();
  model = await loadAndCompile(bytes,{accelerator:"wasm",cpuOptions:{numThreads:1}});
}
async function clean(image: Blob) {
  if (!image.size || image.size > 5*1024*1024) throw new Error("Choose a reference smaller than 5 MiB.");
  let bitmap: ImageBitmap | undefined;
  let tensor: Tensor | undefined;
  let outputs: Tensor[] | undefined;
  try {
    // Decode at bounded width rather than retaining a full camera photo bitmap.
    bitmap = await createImageBitmap(image,{resizeWidth:1536,resizeQuality:"high"});
    const scale = Math.min(1,1536/Math.max(bitmap.width,bitmap.height));
    const width = Math.max(1,Math.round(bitmap.width*scale)), height = Math.max(1,Math.round(bitmap.height*scale));
    const canvas = new OffscreenCanvas(width,height), context = canvas.getContext("2d",{willReadFrequently:true});
    const small = new OffscreenCanvas(1024,1024), sc = small.getContext("2d",{willReadFrequently:true});
    if (!context || !sc) throw new Unsupported();
    context.drawImage(bitmap,0,0,width,height); bitmap.close(); bitmap = undefined;
    sc.fillStyle = "#808080"; sc.fillRect(0,0,1024,1024); sc.drawImage(canvas,0,0,1024,1024);
    const rgba = sc.getImageData(0,0,1024,1024).data, values = new Float32Array(3*1024*1024);
    for (let i=0;i<1024*1024;i++) for (let c=0;c<3;c++) values[c*1024*1024+i] = rgba[i*4+c]/255;
    tensor = new Tensor(values,[1,3,1024,1024]);
    outputs = await model!.run(tensor);
    const alpha = await outputs[0].data();
    if (alpha.length !== 1024*1024) throw new Unsupported();
    let min=Infinity,max=-Infinity;
    for (const value of alpha) {if (!Number.isFinite(value)) throw new Unsupported(); min=Math.min(min,value); max=Math.max(max,value);}
    if (max-min < .01) throw new Error("No clear subject was found. Use the original or another image.");
    const pixels = sc.createImageData(1024,1024);
    for (let i=0;i<alpha.length;i++) {
      const a = Math.round(255*(alpha[i]-min)/(max-min));
      pixels.data[i*4]=pixels.data[i*4+1]=pixels.data[i*4+2]=a; pixels.data[i*4+3]=255;
    }
    sc.putImageData(pixels,0,0);
    const mask = new OffscreenCanvas(width,height), mc = mask.getContext("2d",{willReadFrequently:true});
    if (!mc) throw new Unsupported();
    mc.drawImage(small,0,0,width,height);
    const matte=mc.getImageData(0,0,width,height).data, original=context.getImageData(0,0,width,height);
    applyCleanupWhite(original.data, matte);
    context.putImageData(original,0,0);
    return await encodeCleanupPng(canvas);
  } finally {bitmap?.close();tensor?.delete();outputs?.forEach(value=>value.delete());}
}
scope.onmessage = async ({data}: MessageEvent<CleanupRequest>) => {
  try {
    if (data.type === "initialize") {initializing ??= initialize(); await initializing; send({type:"ready"});}
    else {
      if (!model) throw new Unsupported();
      send({type:"progress",progress:{phase:"cleaning"}});
      send({type:"result",id:data.id,image:await clean(data.image)});
    }
  } catch (error) {
    const kind = error instanceof DownloadFailed ? "download" : error instanceof Unsupported ? "unsupported" : "image";
    send({type:"error",id:data.type === "clean" ? data.id : undefined,kind,
      message:kind === "download" ? "The cleanup model could not be downloaded. Use Original or try again." : kind === "unsupported" ? UNSUPPORTED_CLEANUP : error instanceof Error ? error.message : "Cleanup did not finish. Use Original."});
  }
};
