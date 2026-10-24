"""Select the guide saved for a job, including compatibility with older guides."""
SHARED_CYAN_STYLES = {"cyan_overlay_20", "cyan_overlay_multi_20"}
IDENTITY_OVERLAY_STYLES = {"identity_overlay_20", "identity_overlay_multi_20"}
SHARED_DEPTH_STYLES = {"shared_depth", "shared_depth_multi"}
IDENTITY_DEPTH_STYLES = {"identity_depth", "identity_depth_multi"}


def guide_style(job, enhanced, multi, *, reviewed=False):
    if not enhanced:
        return "original", "none"
    if job.get("prompt_variant") in {"identity_colors_v1", "identity_tint_tracking_v1", "cloudflare_identity_v1"}:
        setup = job.get("setup", {})
        manual = (not job.get("easy_mode", setup.get("easy_mode", False))
                  or job.get("prompt_variant") == "cloudflare_identity_v1")
        identity = (manual and (reviewed or job.get("tracking_reviewed") or job.get("identity_matching_completed"))
                    and not job.get("replace_all_people", setup.get("replace_all_people", False)))
        if job.get("depth_guidance", True):
            # New jobs keep person colors over depth too. Saved jobs retain
            # the shared-tint composition their guide/prompt was prepared for.
            depth_identity = identity and multi and (job.get("depth_overlay_opacity", 1) == 1
                or job.get("depth_identity_colors_version", 0) >= 1)
            style = "identity_depth" if depth_identity else "shared_depth"
            return "depth", style + ("_multi" if multi else "")
        if identity:
            return "translucent", "identity_overlay_multi_20" if multi else "identity_overlay_20"
        return "translucent", "cyan_overlay_multi_20" if multi else "cyan_overlay_20"
    if job.get("depth_guidance", True):
        return "depth", "depth_multi" if multi else "depth"
    if job.get("guide_style_version", 1) >= 2:
        return "translucent", "cyan_overlay_multi_20" if multi else "cyan_overlay_20"
    return "solid", "solid_multi" if multi else "gray"


def compose_styles(fill_mode, targets, mask_style=None):
    shared = (fill_mode == "translucent" and mask_style not in IDENTITY_OVERLAY_STYLES
              or mask_style in SHARED_DEPTH_STYLES)
    return [{"label": target["label"], "color": "#00dcff" if shared else target["color"]}
            for target in targets]
