# Comparison export timing — 2026-10-24

Isolated Cloud Run CPU job using the immutable production gateway image, us-east4,
2 vCPU and 2 GiB RAM. One task, no retries, five-minute timeout. No production
pipeline settings or generation records were changed. No model/API generation.

A retained 15-second original and its existing completed render were downloaded.
H.264, CRF 18, two encoder threads, 24 fps, AAC 192 kbps, faststart. Baseline
re-encodes the completed render as a stand-in for the provider output; it is not
an execution of the entire production finish function (validation/watermark work
is excluded). Both inputs are already local for each measured encoding operation.

| Operation | Seconds |
| --- | ---: |
| Normal-video encode only | 7.122 |
| Landscape comparison only, 1024x1536 | 15.135 |
| Normal video and comparison, one FFmpeg process | 20.299 |
| Smaller portrait throughput sample, 864x768 combined | 7.969 |
| Download original | 0.110 |
| Download completed render | 0.055 |
| Upload comparison, 5.12 MB | 0.201 |

Cloud Run reported 8.4 seconds to start the execution. All four encodes and
transfers together took 51.115 worker seconds; this is NOT the cost of one
comparison. The execution completed successfully and its temporary job was
removed afterwards. No GPU was requested.

For this sample, integrated encoding added 13.177 seconds over normal encoding;
separate encodes totaled 22.257 seconds versus 20.299 integrated. Prefer adding
the comparison in the existing finishing worker because the inputs are already
local and there is no second worker startup. Keep comparison failure separate
from delivery of the successful main render. Higher resolutions/content can
change timings; the portrait sample is center-cropped throughput evidence only,
not a full-resolution portrait quality test. Production audio restoration,
watermarking, overlay labels and exact timestamp normalization still need
integration/validation before this becomes an app feature.

The landscape output decoded completely and had 360 video frames plus a
15-second audio stream. A representative frame was inspected; model-generated
cut timing can differ from the original, so equal timestamps do not guarantee
matching shot boundaries.

Private cloud artifacts: `experiments/comparison-export-benchmark/` in the app
bucket. Local measured evidence: `report.json`, `execution.json`. The script
is a one-off benchmark, not a production route or deployment entrypoint.
