// One request-local, sandboxed worker around Meta's unmodified public codec.
import { decodeMaskToRaster, InvalidSegmentationMaskError } from './vendor/meta_sam_parser/dist/index.js';
import { Buffer } from 'node:buffer';

const limit = 8 * 1024 * 1024;
const output = new TextEncoder();
async function reply(value) {
  const bytes = output.encode(JSON.stringify(value) + '\n');
  let offset = 0;
  while (offset < bytes.length) offset += await Deno.stdout.write(bytes.subarray(offset));
}

await reply({ protocol: 1, package: '@meta-sam/parser', version: '0.1.0' });
let pending = '';
for await (const chunk of Deno.stdin.readable.pipeThrough(new TextDecoderStream('utf-8', { fatal: true }))) {
  pending += chunk;
  // Bound the incomplete request too; never retain arbitrary stdin data.
  if (pending.length > limit) throw new Error('Request exceeds codec limit');
  let newline;
  while ((newline = pending.indexOf('\n')) >= 0) {
    const line = pending.slice(0, newline);
    pending = pending.slice(newline + 1);
    const request = JSON.parse(line);
    const mask = request.mask;
    if (!Number.isSafeInteger(request.id) || !mask ||
        !Number.isSafeInteger(mask.width) || !Number.isSafeInteger(mask.height) ||
        mask.width < 1 || mask.height < 1 || mask.width * mask.height > 854 * 480 ||
        typeof mask.payload !== 'string') throw new Error('Invalid codec request');
    try {
      // Includes the official canonical decode/re-encode and finalization checks.
      const raster = decodeMaskToRaster(mask);
      await reply({ id: request.id, ok: true, raster: Buffer.from(raster).toString('base64') });
    } catch (error) {
      if (!(error instanceof InvalidSegmentationMaskError)) throw error;
      // Keep input bytes, parser detail and any inherited environment off stdout.
      await reply({ id: request.id, ok: false, kind: 'invalid_mask' });
    }
  }
}
if (pending.length) throw new Error('Incomplete codec request');
