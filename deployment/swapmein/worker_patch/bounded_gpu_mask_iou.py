"""Isolated, bounded-scratch replacement for Transformers 5.19.0 SAM3 mask IoU.

All pairs and pixels are counted with integer AND/OR reductions before the same
float32 conversion and union.clamp(min=1) as the upstream implementation. Only
the order of independent integer additions changes. Input views and the required
N-by-M float32 result are excluded from the temporary-memory budget.

The conservative scratch estimate also reserves eight bytes per comparison for
a possible reduction conversion buffer. No model weights, objects, confidence
thresholds, frame resolution, or tracking configuration are changed here.
"""
from dataclasses import asdict, dataclass
import hashlib
import inspect
import time
from types import ModuleType

import torch


MIB = 1024 * 1024
UPSTREAM_MASK_IOU_SHA256 = "31db5245efad145fe561bd4180afff4eac35bedcaf6c46bfa746e22ae10deb3e"
UPSTREAM_SOURCE_URL = "https://raw.githubusercontent.com/huggingface/transformers/v5.19.0/src/transformers/models/sam3_video/modeling_sam3_video.py"


@dataclass
class IoUProfile:
    calls: int = 0
    failed_calls: int = 0
    max_pred_masks: int = 0
    max_gt_masks: int = 0
    max_pixels: int = 0
    max_output_bytes: int = 0
    max_temporary_bytes_estimate: int = 0
    pair_blocks: int = 0
    pixel_blocks: int = 0
    total_elapsed_seconds: float = 0.0
    max_elapsed_seconds: float = 0.0
    total_cuda_elapsed_seconds: float = 0.0
    max_cuda_elapsed_seconds: float = 0.0
    max_cuda_allocated_bytes_observed: int = 0
    cuda_timing_pending_calls: int = 0
    cuda_timing_failed_calls: int = 0


class BoundedMaskIoU:
    def __init__(self, *, max_temporary_bytes: int = 16 * MIB,
                 pair_block_size: int = 16, pixel_block_size: int = 65536,
                 max_objects: int | None = None, synchronize_timing: bool = False,
                 record_cuda_timing: bool = True):
        values = (max_temporary_bytes, pair_block_size, pixel_block_size)
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("IoU bounds must be positive integers")
        if max_objects is not None and (type(max_objects) is not int or max_objects < 1):
            raise ValueError("An optional object bound must be a positive integer")
        if not 1024 <= max_temporary_bytes <= 16 * MIB:
            raise ValueError("The isolated benchmark permits 1 KiB to 16 MiB of IoU scratch")
        self.max_temporary_bytes = max_temporary_bytes
        self.pair_block_size = pair_block_size
        self.pixel_block_size = pixel_block_size
        self.max_objects = max_objects
        self.synchronize_timing = synchronize_timing
        self.record_cuda_timing = record_cuda_timing
        self.allocator_reserve_bytes = min(65536, max_temporary_bytes // 4)
        self.profile = IoUProfile()
        self._cuda_events = []

    def _collect_cuda_timings(self, *, synchronize: bool = False):
        pending = []
        for start, end in self._cuda_events:
            try:
                if synchronize:
                    end.synchronize()
                elif not end.query():
                    pending.append((start, end))
                    continue
                elapsed = start.elapsed_time(end) / 1000
                self.profile.total_cuda_elapsed_seconds += elapsed
                self.profile.max_cuda_elapsed_seconds = max(self.profile.max_cuda_elapsed_seconds, elapsed)
            except RuntimeError:
                # Preserve benchmark diagnostics even when the main model OOMs.
                self.profile.cuda_timing_failed_calls += 1
        self._cuda_events = pending
        self.profile.cuda_timing_pending_calls = len(pending)

    def snapshot(self, *, synchronize: bool = False) -> dict:
        self._collect_cuda_timings(synchronize=synchronize)
        return {"patch": "bounded_mask_iou_v1", **asdict(self.profile),
                "max_temporary_bytes": self.max_temporary_bytes,
                "pair_block_size": self.pair_block_size,
                "pixel_block_size": self.pixel_block_size,
                "max_objects": self.max_objects,
                "allocator_reserve_bytes": self.allocator_reserve_bytes,
                "synchronize_timing": self.synchronize_timing,
                "record_cuda_timing": self.record_cuda_timing,
                "host_elapsed_scope": "Python dispatch; includes GPU waits only if synchronization is requested",
                "scratch_budget_excludes": "existing input masks and required float32 N-by-M result",
                "cuda_observation": "global live allocator high-water mark; this helper does not reset it"}

    def __call__(self, pred_masks: torch.Tensor, gt_masks: torch.Tensor) -> torch.Tensor:
        started = time.perf_counter()
        self.profile.calls += 1
        try:
            cuda_start = cuda_end = None
            if pred_masks.device.type == "cuda" and self.record_cuda_timing:
                cuda_start = torch.cuda.Event(enable_timing=True)
                cuda_end = torch.cuda.Event(enable_timing=True)
                cuda_start.record(torch.cuda.current_stream(pred_masks.device))
            result = self._compute(pred_masks, gt_masks)
            if pred_masks.device.type == "cuda":
                if cuda_end is not None:
                    cuda_end.record(torch.cuda.current_stream(pred_masks.device))
                    self._cuda_events.append((cuda_start, cuda_end))
                    if self.synchronize_timing or len(self._cuda_events) >= 64:
                        self._collect_cuda_timings(synchronize=self.synchronize_timing)
                self.profile.max_cuda_allocated_bytes_observed = max(
                    self.profile.max_cuda_allocated_bytes_observed,
                    torch.cuda.max_memory_allocated(pred_masks.device))
            return result
        except Exception:
            self.profile.failed_calls += 1
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.profile.total_elapsed_seconds += elapsed
            self.profile.max_elapsed_seconds = max(self.profile.max_elapsed_seconds, elapsed)

    def _compute(self, pred_masks: torch.Tensor, gt_masks: torch.Tensor) -> torch.Tensor:
        if pred_masks.ndim != 3 or gt_masks.ndim != 3 or pred_masks.shape[1:] != gt_masks.shape[1:]:
            raise ValueError("SAM IoU inputs must have matching N-by-H-by-W and M-by-H-by-W shapes")
        if pred_masks.device != gt_masks.device:
            raise ValueError("SAM IoU inputs must use the same device")
        integer_dtypes = {torch.bool, torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64}
        if pred_masks.dtype not in integer_dtypes or gt_masks.dtype not in integer_dtypes:
            raise TypeError("SAM mask IoU requires boolean or integer masks")
        n, h, w = pred_masks.shape
        m = gt_masks.shape[0]
        if self.max_objects is not None and max(n, m) > self.max_objects:
            raise ValueError("SAM IoU object count exceeds the explicitly requested object limit")
        pixels = h * w
        # Match upstream view semantics; never clone the full mask collection.
        pred_flat = pred_masks.view(n, 1, pixels)
        gt_flat = gt_masks.view(1, m, pixels)
        result = torch.empty((n, m), dtype=torch.float32, device=pred_masks.device)
        profile = self.profile
        profile.max_pred_masks = max(profile.max_pred_masks, n)
        profile.max_gt_masks = max(profile.max_gt_masks, m)
        profile.max_pixels = max(profile.max_pixels, pixels)
        profile.max_output_bytes = max(profile.max_output_bytes, n * m * 4)
        # Two int64 counters and one int64 reduction result: 24 bytes per pair.
        # A bitwise result plus a conservative int64 conversion reserve covers
        # the pixel-dependent scratch even for alternate integer input dtypes.
        operation_bytes = torch.empty((), dtype=torch.promote_types(pred_masks.dtype, gt_masks.dtype)).element_size()
        comparison_bytes = operation_bytes + 8
        working_budget = self.max_temporary_bytes - self.allocator_reserve_bytes
        max_pairs = working_budget // (24 + comparison_bytes)
        row_block = min(self.pair_block_size, max(1, n), max_pairs)
        col_block = min(self.pair_block_size, max(1, m), max(1, max_pairs // row_block))
        for first in range(0, n, row_block):
            last = min(first + row_block, n)
            for other in range(0, m, col_block):
                end = min(other + col_block, m)
                pairs = (last - first) * (end - other)
                chunk = min(self.pixel_block_size,
                    (working_budget - 24 * pairs) // (comparison_bytes * pairs))
                if chunk < 1:
                    raise RuntimeError("IoU scratch bounds could not fit one pixel per pair")
                intersection = torch.zeros((last - first, end - other), dtype=torch.int64, device=result.device)
                union = torch.zeros_like(intersection)
                profile.pair_blocks += 1
                profile.max_temporary_bytes_estimate = max(profile.max_temporary_bytes_estimate,
                    24 * pairs + self.allocator_reserve_bytes)
                for pixel in range(0, pixels, chunk):
                    stop = min(pixel + chunk, pixels)
                    pred = pred_flat[first:last, :, pixel:stop]
                    ground_truth = gt_flat[:, other:end, pixel:stop]
                    intersection.add_((pred & ground_truth).sum(dim=2))
                    union.add_((pred | ground_truth).sum(dim=2))
                    profile.pixel_blocks += 1
                    profile.max_temporary_bytes_estimate = max(profile.max_temporary_bytes_estimate,
                        pairs * ((stop - pixel) * comparison_bytes + 24) + self.allocator_reserve_bytes)
                numerator = intersection.float()
                denominator = union.float()
                denominator.clamp_(min=1)
                numerator.div_(denominator)
                result[first:last, other:end].copy_(numerator)
                del numerator, denominator, intersection, union
        return result


def install_bounded_mask_iou(sam3_module: ModuleType, **options) -> BoundedMaskIoU:
    """Replace only the exact pinned module-global helper; fail on source drift."""
    original = sam3_module.mask_iou
    if isinstance(original, BoundedMaskIoU):
        if options:
            raise RuntimeError("Bounded SAM IoU is already installed; do not silently change its bounds")
        return original
    source = inspect.getsource(original).strip()
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != UPSTREAM_MASK_IOU_SHA256:
        raise RuntimeError(f"Unexpected upstream mask_iou source: {digest}")
    replacement = BoundedMaskIoU(**options)
    sam3_module.mask_iou = replacement
    return replacement
