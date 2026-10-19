// Pin the browser model and runtime. Large assets are build inputs, never Git files.
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {build} from 'esbuild';
const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const revision='962e67969ec6acec9c19fab76acee4f5e22e990c';
const directory=path.join(frontend,'public/charSwap-runtime/models/birefnet-lite-512',revision);
const target=path.join(directory,'model_fp16.onnx');
const sha='eff9216bb2f9d3f023d9c2b7196845a7485739ab1f231593633e4d2344ffc516';
async function valid(){try{const data=await readFile(target);return data.length===98484532&&createHash('sha256').update(data).digest('hex')===sha;}catch{return false;}}
await mkdir(directory,{recursive:true});
if(!await valid()){
 const response=await fetch(`https://huggingface.co/studioludens/birefnet-lite-512/resolve/${revision}/onnx/model_fp16.onnx`);
 if(!response.ok)throw Error('Browser cleanup model download failed: '+response.status);
 const bytes=Buffer.from(await response.arrayBuffer());
 if(bytes.length!==98484532||createHash('sha256').update(bytes).digest('hex')!==sha)throw Error('Browser cleanup model checksum mismatch');
 await writeFile(target,bytes);
}
for(const file of ['LICENSE','config.json','preprocessor_config.json']){
 const response=await fetch(`https://huggingface.co/studioludens/birefnet-lite-512/resolve/${revision}/${file}`);
 if(!response.ok)throw Error('Missing browser cleanup model attribution/config: '+file);
 await writeFile(path.join(directory,file),await response.text());
}
const runtime=path.join(frontend,'public/charSwap-runtime/ml/ort-1.31.0-8d85527a0');
await mkdir(runtime,{recursive:true});
for(const file of ['ort-wasm-simd-threaded.asyncify.mjs','ort-wasm-simd-threaded.asyncify.wasm'])
 await copyFile(path.join(frontend,'node_modules/onnxruntime-web/dist',file),path.join(runtime,file));
const license=await fetch('https://raw.githubusercontent.com/microsoft/onnxruntime/8d85527a0/LICENSE');
if(!license.ok)throw Error('Missing ONNX Runtime license');
await writeFile(path.join(runtime,'LICENSE'),await license.text());
console.log('Browser cleanup assets ready: pinned 98.5 MB model + local ONNX runtime.');

// iOS uses ORMBG + LiteRT CPU, which was verified on a physical iPhone.
const cpuRevision='8dfdacc4ebceafd4d55195fdbd3baf8470610518';
const cpuDirectory=path.join(frontend,'public/charSwap-runtime/models/ormbg',cpuRevision);
await mkdir(cpuDirectory,{recursive:true});
const cpuTarget=path.join(cpuDirectory,'ormbg.tflite');
const cpuSha='f6e359cf35c55e7bf216eb3141f6d76828667a5e8e67a929f53e9f7f38fae030';
async function validCpu(){try{const b=await readFile(cpuTarget);return b.length===176240148&&createHash('sha256').update(b).digest('hex')===cpuSha;}catch{return false;}}
if(!await validCpu()){
 const r=await fetch(`https://huggingface.co/litert-community/ormbg-LiteRT/resolve/${cpuRevision}/ormbg.tflite`);
 if(!r.ok)throw Error('CPU cleanup model download failed: '+r.status);
 const b=Buffer.from(await r.arrayBuffer());
 if(b.length!==176240148||createHash('sha256').update(b).digest('hex')!==cpuSha)throw Error('CPU cleanup model checksum mismatch');
 await writeFile(cpuTarget,b);
}
const cpuRuntime=path.join(frontend,'public/charSwap-runtime/ml/litert-2.5.3-cpu-v4-white');
await mkdir(cpuRuntime,{recursive:true});
for(const file of ['litert_wasm_compat_internal.js','litert_wasm_compat_internal.wasm','litert_wasm_internal.js','litert_wasm_internal.wasm'])
 await copyFile(path.join(frontend,'node_modules/@litertjs/core/wasm',file),path.join(cpuRuntime,file));
await build({entryPoints:[path.join(frontend,'src/features/charSwap/workers/referenceCleanup.cpu.ts')],bundle:true,format:'iife',platform:'browser',target:'es2022',outfile:path.join(cpuRuntime,'referenceCleanup.cpu.js')});
const cpuReadme=await fetch(`https://huggingface.co/litert-community/ormbg-LiteRT/resolve/${cpuRevision}/README.md`);
if(!cpuReadme.ok)throw Error('Missing ORMBG model attribution');
await writeFile(path.join(cpuDirectory,'README.md'),await cpuReadme.text());
const apache=await fetch('https://www.apache.org/licenses/LICENSE-2.0.txt');
if(!apache.ok)throw Error('Missing Apache license');
const apacheText=await apache.text();
await writeFile(path.join(cpuDirectory,'LICENSE'),apacheText);
await writeFile(path.join(cpuRuntime,'LICENSE'),apacheText);
await writeFile(path.join(cpuRuntime,'NOTICE'),'LiteRT.js 2.5.3, Copyright Google LLC. https://github.com/google-ai-edge/LiteRT\n');
console.log('iOS cleanup assets ready: pinned 176 MB ORMBG model + CPU-only LiteRT worker.');
