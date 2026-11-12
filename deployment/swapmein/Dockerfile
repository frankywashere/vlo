FROM denoland/deno:bin-2.9.6 AS youtube-js
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-liberation2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=youtube-js /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY swapmein ./swapmein
RUN useradd --uid 10001 --create-home gateway
USER gateway
RUN META_MASK_CODEC=javascript python -c 'from meta_sam_parser import SegmentationMask,decode_mask_to_raster; from swapmein.meta_mask_codec import StrictMaskCodec; m=SegmentationMask(encoding="one_bit",width=5,height=5,payload="!!!!!(QO(0lu8?"); c=StrictMaskCodec(decode_mask_to_raster,lambda:10); assert c.backend=="javascript" and c.decode(m)==decode_mask_to_raster(m); c.close()'
CMD ["sh", "-c", "exec uvicorn swapmein.app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
