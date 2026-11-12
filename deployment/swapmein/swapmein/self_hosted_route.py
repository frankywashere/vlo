"""Validated generation routing, pinned before uploads or background work."""

from .errors import PublicError


MULTI_PERSON_GENERATION_ROUTES = frozenset({"current", "google_h3", "paperspace_h3"})
SELF_HOSTED_GENERATION_ROUTES = MULTI_PERSON_GENERATION_ROUTES - {"current"}


def validate_generation_route(value):
    if not isinstance(value, str) or value not in MULTI_PERSON_GENERATION_ROUTES:
        raise PublicError("Choose a supported multi-person generation route.")
    return value


def effective_generation_route(setup, configured_route):
    """A shared reference still represents two people when two targets are selected."""
    route = validate_generation_route(configured_route)
    data = setup if isinstance(setup, dict) else setup.model_dump()
    from .reference_groups import has_multiple_angles
    if (data.get("generation_model") == "kling-4-0"
            or data.get("generation_resolution") == "2K" or has_multiple_angles(data)):
        # Explicit model and its quoted price take precedence over an Admin
        # override intended only for MiniMax CharacterSwap.
        return "current"
    targets = data.get("targets") or []
    if data.get("replace_all_people") or len(targets) < 2:
        return "current"
    return route


def generation_route_snapshot(setup, configured_route):
    from .preparation_only import preparation_only, isolated_test_site
    if preparation_only() or isolated_test_site():
        return {"multi_person_generation_route": "current"}
    route = effective_generation_route(setup, configured_route)
    snapshot = {"multi_person_generation_route": route}
    if route in SELF_HOSTED_GENERATION_ROUTES:
        snapshot.update(face_mesh=False, depth_guidance=False,
                        retry_missing_masks_with_sam=False, mask_style="none",
                        audio_processing=False, restore_original_audio=True)
    return snapshot
