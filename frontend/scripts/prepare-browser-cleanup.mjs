// Pin the browser model and runtime. Large assets are build inputs, never Git files.
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
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
