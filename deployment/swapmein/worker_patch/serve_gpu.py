"""Private GPU entry point; preserve every detection while bounding IoU scratch."""
import os

import uvicorn

import app as worker
from bounded_gpu_mask_iou import install_bounded_mask_iou


# Reject an unreviewed upstream helper before the worker reports healthy.
install_bounded_mask_iou(worker.sam3_impl, record_cuda_timing=False)

if __name__ == "__main__":
    uvicorn.run(worker.app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
