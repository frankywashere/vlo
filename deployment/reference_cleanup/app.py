"""Private CPU reference matting; original RGB is composited through predicted alpha."""
from io import BytesIO
import threading
from fastapi import FastAPI, Request, HTTPException, Response
from PIL import Image, ImageOps, UnidentifiedImageError

app = FastAPI()
_session = None
_lock = threading.Lock()
MAX_BYTES = 5 * 1024 * 1024

def clean_reference(data: bytes) -> bytes:
    global _session
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "Choose an image smaller than 5 MB.")
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.width * opened.height > 2048 * 2048 or min(opened.size) < 256:
                raise HTTPException(400, "The prepared reference dimensions are invalid.")
            image = ImageOps.exif_transpose(opened).convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "The reference could not be decoded.") from None
    with _lock:
        if _session is None:
            from rembg import new_session
            _session = new_session('birefnet-portrait', providers=['CPUExecutionProvider'])
        mask = _session.predict(image)[0]
    if mask.size != image.size or mask.getextrema()[1] < 128:
        raise HTTPException(422, "No clear foreground was found. Keep the original image.")
    cleaned = Image.new('RGB', image.size, (128, 128, 128))
    cleaned.paste(image, (0, 0), mask)
    output = BytesIO()
    cleaned.save(output, 'JPEG', quality=95)
    return output.getvalue()

@app.get('/health')
def health():
    return {'model': 'birefnet-portrait', 'gpu': False}

@app.post('/clean')
async def clean(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Choose an image smaller than 5 MB.")
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(clean_reference, bytes(data))
    return Response(result, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
