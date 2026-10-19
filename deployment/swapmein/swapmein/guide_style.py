"""Select the guide saved for a job, including compatibility with older guides."""
SHARED_CYAN_STYLES = {"cyan_overlay_20", "cyan_overlay_multi_20"}


def guide_style(job, enhanced, multi):
    if not enhanced:
        return "original", "none"
    if job.get("depth_guidance", True):
        return "depth", "depth_multi" if multi else "depth"
    if job.get("guide_style_version", 1) >= 2:
        return "translucent", "cyan_overlay_multi_20" if multi else "cyan_overlay_20"
    return "solid", "solid_multi" if multi else "gray"


def compose_styles(fill_mode, targets):
    return [{"label": target["label"], "color": "#00dcff" if fill_mode == "translucent" else target["color"]}
            for target in targets]
