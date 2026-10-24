"""Versioned prompt experiments, independent of guide rendering and job routing.

The current provider builder remains the compatibility path.  New wording uses
the actual saved guide, audio, and reviewed assignments rather than inferring
those settings from an experiment name.
"""
from .errors import PublicError
from .guide_style import SHARED_CYAN_STYLES
from .multi_tracking import TARGET_COLORS

CURRENT = "current"
FAVORITE_V2 = "favorite_v2"
STRUCTURED_V1 = "structured_v1"
IDENTITY_COLORS_V1 = "identity_colors_v1"
IDENTITY_TINT_TRACKING_V1 = "identity_tint_tracking_v1"
CLOUDFLARE_IDENTITY_V1 = "cloudflare_identity_v1"
PROMPT_VARIANTS = (CURRENT, FAVORITE_V2, STRUCTURED_V1, IDENTITY_COLORS_V1, IDENTITY_TINT_TRACKING_V1, CLOUDFLARE_IDENTITY_V1)
DEFAULT_PROMPT_VARIANT = CLOUDFLARE_IDENTITY_V1
MAX_PROMPT_LENGTH = 7000
SAME_CAMERA_SENTENCE = "All replacement performers should look as though they were filmed in the original scene with the same camera."
IDENTITY_OVERLAY_STYLES = frozenset({"identity_overlay_20", "identity_overlay_multi_20"})
IDENTITY_DEPTH_STYLES = frozenset({"identity_depth", "identity_depth_multi"})
SHARED_DEPTH_STYLES = frozenset({"shared_depth", "shared_depth_multi"})
EASY_TINT_TRACKING_GUIDANCE = (
    "Use the tinted region to help follow the identified source person's movement and performance while it remains attached to that person. "
    "If the tint disappears or switches to someone else, use the visible source person and the timestamp/position assignments. "
    "Keep the same replacement identity across cuts.")


def _easy_tracking_guidance(enhanced_masking, mask_style, replace_all_people):
    if not enhanced_masking:
        return ("Follow each source person's movement and performance using the visible source people; "
                "keep each person's performance separate through cuts and use the same replacement reference for everyone."
                if replace_all_people else
                "Follow the identified source person's movement and performance using the visible source person and the "
                "timestamp/position assignments. Keep the same replacement identity across cuts.")
    if replace_all_people:
        return ("Use the marked regions to help follow each source person's movement and performance while they remain "
                "attached to that person. If a marker disappears or switches people, follow the visible source person. "
                "Keep each person's performance separate through cuts and use the same replacement reference for everyone.")
    if mask_style in SHARED_CYAN_STYLES or mask_style == "cyan_overlay":
        return EASY_TINT_TRACKING_GUIDANCE
    return ("Use the marked region to help follow the identified source person's movement and performance while it remains "
            "attached to that person. If the marker disappears or switches to someone else, use the visible source person "
            "and the timestamp/position assignments. Keep the same replacement identity across cuts.")


def normalize_prompt_variant(value):
    if value is None:
        # Legacy jobs may have no stored version. New uploads snapshot the
        # global default explicitly, so changing it must not rewrite old jobs.
        return CURRENT
    if not isinstance(value, str) or value not in PROMPT_VARIANTS:
        raise PublicError("Choose a valid generation prompt version.", code="prompt_variant_invalid")
    return value


def _point(point):
    return (f"{point['time']:.2f} seconds, {point['x'] * 100:.0f}% across and "
            f"{point['y'] * 100:.0f}% down from the top-left corner")


def _fallbacks(targets):
    return any(target.get("coordinate_fallback") or any(scene.get("coordinate_fallback")
               for scene in target.get("scenes", [])) for target in targets or [])


def adapt_favorite_prompt(prompt, *, enhanced_masking, anchor, mask_style, targets, replace_all_people):
    """Correct audited omissions without changing the matching favorite path.

    Five-reference Easy Mode shared cyan already matches the saved favorite
    verbatim.  Single-reference mask prompts omitted their supplied anchor, and
    two legacy single-person branches omitted the absence safeguard.
    """
    if not replace_all_people and targets is None:
        point = anchor or {"x": .5, "y": .5, "time": 0}
        if enhanced_masking:
            prompt += (f" Identify the selected source person at {_point(point)}. "
                       "Use this position and timestamp to establish their reference-image assignment across cuts.")
        if not enhanced_masking or mask_style not in {"depth", "cyan_overlay", *SHARED_CYAN_STYLES}:
            prompt = prompt.replace("in every frame", "where that source person is present")
            prompt += (" When the selected person is absent, preserve the scene and do not replace another person in their place.")
    if enhanced_masking and mask_style in SHARED_CYAN_STYLES and _fallbacks(targets):
        prompt = prompt.replace("All selected people share the same cyan overlay at 20% color opacity.",
            "Selected people with detected masks share the same cyan overlay at 20% color opacity. "
            "Selections without masks remain in original visible footage; identify them by their listed positions and timestamps, never by another person's overlay.")
    return prompt


def _guide_description(enhanced_masking, mask_style, face_mesh, has_fallback, replace_all_people):
    if not enhanced_masking:
        visual = "The source people remain visible in original footage; no person-mask overlay is present."
    elif mask_style in SHARED_CYAN_STYLES:
        visual = (("Selected performers with detected masks share" if has_fallback else "All selected performers share")
            + " the same cyan overlay at 20% opacity. The color marks selection, not identity; "
            + ("each source person uses <Picture 1>." if replace_all_people
               else "use the defined position/time anchors for identity."))
        if face_mesh:
            visual += " Face-mesh lines describe facial motion only."
        visual += (" Remove all cyan and mesh marks from the finished footage." if face_mesh
                   else " Remove all cyan marks from the finished footage.")
    elif mask_style == "cyan_overlay":
        visual = ("The translucent cyan overlay marks the selected person, not their appearance. "
                  "Use position/time assignments for identity.")
    elif mask_style in {"depth", "depth_multi", *SHARED_DEPTH_STYLES}:
        visual = ("Colored depth imagery inside detected person masks describes shape and motion, not appearance. "
                  "Use position/time assignments for identity.")
        if mask_style in SHARED_DEPTH_STYLES:
            visual += " The shared tint marks selection, not individual identity."
    else:
        visual = ("Flat opaque person silhouettes mark detected selections, not depth or target appearance. "
                  "Use position/time assignments for identity.")
    if has_fallback:
        visual += (" Assignments marked no mask remain in original visible footage. Identify them from their listed "
                   "position and timestamp; never substitute a different masked person.")
    if face_mesh and not (enhanced_masking and mask_style in SHARED_CYAN_STYLES):
        visual += " Face-mesh lines describe detected facial motion only; remove every mesh line from the final video."
    if enhanced_masking and mask_style not in SHARED_CYAN_STYLES:
        if mask_style in {"depth", "depth_multi", *SHARED_DEPTH_STYLES}:
            visual += " Remove all depth-map colors from the finished footage."
        elif mask_style in {"cyan_overlay", *SHARED_CYAN_STYLES}:
            visual += " Remove all selection tint from the finished footage."
        else:
            visual += " Remove all flat mask colors from the finished footage."
    return visual


def _audio_sections(audio_processing, submit_without_audio):
    if submit_without_audio:
        return (None,
            "The reference video is silent. Preserve visual mouth movements, expressions and performance timing; the original soundtrack will be restored after generation.",
            "The reference video is silent; do not invent dialogue, ambience or effects. The original soundtrack will be restored after generation.",
            "Add no music; the original soundtrack will be restored after generation.")
    if audio_processing:
        return ("<Audio 1> is the synchronized isolated-vocal track embedded in <Video 1>, pitched up three semitones.",
            "<Audio 1>: performance_guidance - use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue. The original soundtrack will be restored after generation.",
            "Use <Audio 1>'s isolated pitched vocals for synchronized performance and source speaker timing; add no dialogue or effects. The original soundtrack will be restored after generation.",
            "Add no music; the original soundtrack will be restored after generation.")
    return ("<Audio 1> is the synchronized soundtrack embedded in <Video 1>.",
        "<Audio 1>: fully_copy - retain the original embedded soundtrack without added dialogue or music.",
        "Reuse the original dialogue, ambience, laughter and effects from <Audio 1>, retaining their source timing and speaker associations.",
        "Retain only music already present in <Audio 1>; add none.")


def _target_color_name(target, index):
    """Describe the color actually painted, without accepting prompt prose.

    Tracking normally saves both the hex color and its canonical name. A saved
    hex value takes precedence over an inconsistent old name. Older plans have
    neither field and use the same ordered palette as the compositor.
    """
    colors = {color.lower(): name for color, name in TARGET_COLORS}
    color = target.get("color")
    if isinstance(color, str) and color.lower() in colors:
        return colors[color.lower()]
    if color is not None:
        raise PublicError("The selected person's mask color is invalid. Review the scene matches before generating.",
            400, code="mask_color_invalid")
    name = target.get("color_name")
    if name is not None:
        if name in colors.values():
            return name
        raise PublicError("The selected person's mask color is invalid. Review the scene matches before generating.",
            400, code="mask_color_invalid")
    return TARGET_COLORS[index - 1][1]


def _identity_guide_description(enhanced_masking, mask_style, face_mesh, has_fallback, replace_all_people,
                                *, tint_tracking=False):
    """Identity families follow saved guide pixels, never automatic track IDs."""
    if not enhanced_masking:
        visual = _guide_description(False, mask_style, face_mesh, has_fallback, replace_all_people)
        return visual + (" " + _easy_tracking_guidance(False, mask_style, replace_all_people) if tint_tracking else "")
    if mask_style in IDENTITY_OVERLAY_STYLES:
        if replace_all_people:
            visual = ("Detected source people have 20%-opacity selection tints; every source person uses <Picture 1>. "
                "The tint is a selection annotation, not the target appearance.")
        else:
            visual = ("Each performer has the 20%-opacity color assigned above. Keep color-to-reference mappings across cuts; "
                "use position/time anchors where tint is missing.")
    elif mask_style in SHARED_CYAN_STYLES or mask_style == "cyan_overlay":
        opacity = " at 20% opacity" if mask_style in SHARED_CYAN_STYLES else ""
        visual = (f"Selected performers with detected masks share the same cyan tint{opacity}. "
            "The shared color marks selection, not identity. "
            + ("Every source person uses <Picture 1>." if replace_all_people else
               "For each reference assignment, identify the tinted source person at the listed timestamp and position. "
               "If no tint is available there, use the visible source person at that same anchor, never another tinted person. "
               "Coordinates identify a person at that timestamp; they are not a fixed screen position to follow throughout the video."))
    elif mask_style in IDENTITY_DEPTH_STYLES:
        visual = ("Colored depth imagery inside detected person masks describes shape and motion, not target appearance. "
            + ("Every source person uses <Picture 1>." if replace_all_people else
               "Use the depth-color-to-reference assignments above and the reviewed position/time anchors; "
               "keep the same assignment across cuts and use its visible source person when depth imagery is missing."))
    elif mask_style in {"depth", "depth_multi"}:
        visual = ("Colored depth imagery inside detected person masks describes shape and motion, not target appearance. "
            + ("Every source person uses <Picture 1>." if replace_all_people else
               "Use position/time assignments to identify each source performer; depth colors alone do not establish identity. "
               "When no mask is available, use the visible source person at that same timed anchor."))
    elif mask_style in SHARED_DEPTH_STYLES:
        visual = ("Detected selected people share the same depth-guide treatment. Depth imagery describes shape and motion; "
            "the shared tint marks selection, not identity. "
            + ("Every source person uses <Picture 1>." if replace_all_people else
               "Identify each source person at the listed timestamp and position, using the depth-marked region when available. "
               "If no mask is available there, use the visible source person at that same anchor, never a different masked person. "
               "Coordinates identify a person at that timestamp, not a fixed screen position throughout the video."))
    else:
        visual = ("Flat opaque silhouettes mark detected selected people, not their appearance or depth. "
            + ("Every source person uses <Picture 1>." if replace_all_people else
               "Use the listed flat-mask colors where provided and position/time assignments to identify each source performer."))
    if tint_tracking:
        visual += " " + _easy_tracking_guidance(True, mask_style, replace_all_people)
    if has_fallback:
        visual += (" Assignments marked no mask remain in original visible footage. Identify them from their listed "
            "position and timestamp; never substitute a different masked person.")
    if face_mesh:
        visual += " Face-mesh lines describe facial motion only."
    visual += (" Remove all color and mesh marks from the finished footage." if face_mesh
        else " Remove all guide colors from the finished footage.")
    return visual


def _scene_lines(index, target, *, compact):
    lines = []
    if compact and target.get("scenes"):
        lines.append(f"<Subject {index}> reviewed schedule:")
    for scene in target.get("scenes", []):
        interval = f"{scene['start']:.2f}-{scene['end']:.2f}s"
        if scene["absent"]:
            lines.append(f"{interval}: absent." if compact else f"<Subject {index}> {interval}: absent; replace nobody for this assignment.")
        else:
            point = scene["anchor"]
            fallback = bool(scene.get("coordinate_fallback") or target.get("coordinate_fallback"))
            if compact:
                lines.append(f"{interval}: at {point['time']:.2f}s "
                    f"x={point['x'] * 100:.0f}% y={point['y'] * 100:.0f}%"
                    + ("; no mask." if fallback else "."))
            else:
                lines.append(f"<Subject {index}> from {scene['start']:.2f} to {scene['end']:.2f} seconds: "
                    f"identify at {_point(point)}; apply this subject's reference throughout this interval."
                    + (" No selection mask is present; use the original visible person, never another masked person." if fallback else ""))
    return lines


def _structured_prompt(duration, targets, enhanced_masking, mask_style, audio_processing,
                       submit_without_audio, replace_all_people, face_mesh, *, compact, identity_colors=False,
                       tint_tracking=False):
    audio_definition, audio_retention, soundscape, music = _audio_sections(audio_processing, submit_without_audio)
    has_fallback = _fallbacks(targets)
    guide = (_identity_guide_description(enhanced_masking, mask_style, face_mesh, has_fallback,
                replace_all_people, tint_tracking=tint_tracking) if identity_colors else
             _guide_description(enhanced_masking, mask_style, face_mesh, has_fallback, replace_all_people))
    definitions = ["<Video 1> is the original source footage being edited, including its existing shots and camera rendering."]
    if enhanced_masking and mask_style in SHARED_CYAN_STYLES:
        definitions[0] += (" The shared cyan tint and face-mesh lines are selection and motion annotations, not the target visual style."
            if face_mesh else " The shared cyan tint is a selection annotation, not the target visual style.")
    elif identity_colors and enhanced_masking and mask_style in IDENTITY_OVERLAY_STYLES:
        definitions[0] += (" The distinct 20%-opacity color tints and face-mesh lines are selection and motion annotations, not the target visual style."
            if face_mesh else " The distinct 20%-opacity color tints are selection annotations, not the target visual style.")
    if audio_definition:
        definitions.append(audio_definition)
    retention = [("<Video 1>: partially_preserved - retain the source environment, "
        + ("" if replace_all_people else "unselected people, ")
        + "framing, camera work, timing and photographic treatment. Only the selected identities and clothing change, and guide annotations are removed.")]
    retention.append(audio_retention)
    assignments = []
    if replace_all_people:
        definitions.extend([
            "<Subject 1> means every source person throughout <Video 1>, including people who enter later or first appear after cuts; it is not a fixed list or limited number of performers.",
            "<Subject 2> is the replacement character whose identity, hair and clothing come from <Picture 1>; the picture is not a target frame or pose."])
        mapping = "Replace every source person in every scene with a separate instance of <Subject 2>, using <Picture 1>."
        retention.extend([
            "<Subject 1>: partially_preserved - preserve each individual person's position, interactions, posture, head angle, gaze, facial movement and speaking turns; change their identity and clothing to <Subject 2>.",
            "<Subject 2>: attribute_transfer - transfer identity, hair and clothing to each source person; do not transfer the photograph's pose, illumination, background or sharpness."])
        scope = ("Inspect the entire video. Include people entering later and after cuts. Keep every person's separate performance; "
                 "do not merge people or add extra people. In frames without people, preserve the scene without adding anyone.")
    else:
        count = len(targets)
        pairs = []
        for index, target in enumerate(targets, 1):
            replacement = count + index
            reference = target["reference_index"] + 1
            point = target["anchor"]
            if compact:
                definitions.append(f"<Subject {index}>: source performer at {_point(point)}. "
                    f"<Subject {replacement}>: identity, hair and clothing from <Picture {reference}> only.")
            else:
                definitions.extend([
                    f"<Subject {index}> is the source performer selected in <Video 1> at {_point(point)}. "
                    "This anchor identifies that performer when they appear again after cuts.",
                    f"<Subject {replacement}> is the replacement character whose identity, hair and clothing come from <Picture {reference}>. "
                    "The photo defines the character, not a target frame or pose."])
            pairs.append(f"<Subject {index}> becomes <Subject {replacement}>")
            if compact:
                retention.append(f"<Subject {index}>: preserve source performance; transfer identity, hair and clothing from <Subject {replacement}> (<Picture {reference}>) only.")
            else:
                retention.extend([
                    f"<Subject {index}>: partially_preserved - preserve this performer's screen position, physical interactions, body posture, head angle, gaze, facial movement and speaking turns; replace their identity and clothing with <Subject {replacement}>.",
                    f"<Subject {replacement}>: attribute_transfer - transfer this character's recognizable identity, hair and clothing onto <Subject {index}> throughout that source performer's appearances. Do not transfer the photograph's pose, illumination, background or photographic sharpness."])
            if target.get("coordinate_fallback"):
                assignments.append(f"<Subject {index}> has no selection mask; identify from its position/time in original visible footage, never another masked person.")
            if (identity_colors and enhanced_masking
                    and mask_style in {*IDENTITY_OVERLAY_STYLES, *IDENTITY_DEPTH_STYLES, "solid_multi"}
                    and not target.get("coordinate_fallback")):
                marker = ("overlay" if mask_style in IDENTITY_OVERLAY_STYLES else
                    "flat mask" if mask_style == "solid_multi" else "depth marker")
                color = _target_color_name(target, index)
                assignments.append(f"<Subject {index}>: {color} {marker}; <Picture {reference}>.")
            elif not identity_colors and enhanced_masking and mask_style in {"depth_multi", "solid_multi"} and not target.get("coordinate_fallback"):
                marker = "depth" if mask_style == "depth_multi" else "flat mask"
                assignments.append(f"<Subject {index}>'s detected {marker} marker is {_target_color_name(target, index)}.")
            assignments.extend(_scene_lines(index, target, compact=compact))
        mapping = "Change only the selected performers: " + ", ".join(pairs) + "."
        if any(target.get("scenes") for target in targets):
            assignments.insert(0, "The following reviewed scene positions and absences are authoritative over initial anchors and automatic tracking; absent means replace nobody for that assignment. Times are seconds; x/y are percentages across/down from the top left.")
        scope = ("Keep all pairings separate through cuts and overlaps. Each replacement appears only where its corresponding "
                 "source performer is present; do not insert it elsewhere or replace an unselected bystander.")
    if assignments:
        definitions.extend(assignments)
    if compact:
        definitions[0] = "<Video 1> is the source footage, with its existing shots and camera rendering."
    detail = [
        "The source video governs how the whole scene is photographed, including the replacement people. Render their appearance within that same source-shot lighting and image texture. Identity comes from the pictures; photographic rendering comes from <Video 1>.",
        f"Follow all existing shots, cuts, actions and interactions in <Video 1> over its exact {duration}-second timeline. " + scope,
        "Preserve each source performer's continuous movement and visible mouth performance, not the still pose or expression in the photograph. Keep a listener listening when somebody else speaks. Replace the entire selected character, including head, hands, body and feet, consistently with its reference identity and clothing.",
        guide,
        SAME_CAMERA_SENTENCE,
    ]
    if compact:
        detail[0] = "Pictures supply identity, hair and clothing only. <Video 1> supplies shot lighting, camera texture, performance and photographic rendering."
        if not replace_all_people:
            detail[1] = (f"Follow every existing shot, cut and interaction over the exact {duration}-second timeline. "
                "Keep pairings separate through overlaps and cuts; replace only present source performers, never bystanders.")
        detail[2] = "Follow visible facial expressions, mouth movements and speaking turns; keep listeners' mouths at rest. Replace entire head, hands, body and feet; preserve source performance, never photograph pose."
    audio_tag = "silent visual guidance" if submit_without_audio else "audio reuse"
    sections = [
        ("subject_definitions", "\n".join(definitions)),
        ("summary", f"[video editing + reference generation + {audio_tag}] The target video is an edited version of <Video 1>. " + mapping + " Keep every original shot and the source performance."),
        ("retention_analysis", "\n".join(retention)),
        ("detailed_description", "\n".join(detail)),
        ("overall_soundscape", soundscape),
        ("non_diegetic_music", music),
    ]
    return "\n\n".join(f"{name}:\n{body}" for name, body in sections)


def structured_prompt(duration, *, enhanced_masking=False, anchor=None, mask_style="cyan_overlay",
                      audio_processing=True, submit_without_audio=False, replace_all_people=False,
                      targets=None, face_mesh=False):
    normalized_targets = ([{**target, "anchor": target.get("anchor", target)} for target in targets]
        if targets is not None else [{"reference_index": 0,
            "anchor": anchor or {"x": .5, "y": .5, "time": 0}}])
    kwargs = dict(enhanced_masking=enhanced_masking, mask_style=mask_style,
        audio_processing=audio_processing, submit_without_audio=submit_without_audio,
        replace_all_people=replace_all_people, face_mesh=face_mesh)
    prompt = _structured_prompt(duration, normalized_targets, **kwargs, compact=False)
    if len(prompt) > MAX_PROMPT_LENGTH:
        prompt = _structured_prompt(duration, normalized_targets, **kwargs, compact=True)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise PublicError("This clip has too many scene assignments for the provider prompt limit. Use a shorter clip or fewer replacements. No generation was submitted.",
            400, code="provider_prompt_too_long")
    return prompt


def identity_colors_prompt(duration, *, enhanced_masking=False, anchor=None, mask_style="cyan_overlay",
                           audio_processing=True, submit_without_audio=False, replace_all_people=False,
                           targets=None, face_mesh=False, tint_tracking=False):
    """Color identities for reviewed guides, timed shared selection for Easy Mode.

    The guide style is the saved description of what the compositor produced.
    It deliberately carries whether a tint encodes identity: automatic SAM track
    IDs alone never authorize a claim that a color identifies someone across cuts.
    """
    normalized_targets = ([{**target, "anchor": target.get("anchor", target)} for target in targets]
        if targets is not None else [{"reference_index": 0,
            "anchor": anchor or {"x": .5, "y": .5, "time": 0}}])
    kwargs = dict(enhanced_masking=enhanced_masking, mask_style=mask_style,
        audio_processing=audio_processing, submit_without_audio=submit_without_audio,
        replace_all_people=replace_all_people, face_mesh=face_mesh, identity_colors=True,
        tint_tracking=tint_tracking)
    prompt = _structured_prompt(duration, normalized_targets, **kwargs, compact=False)
    if len(prompt) > MAX_PROMPT_LENGTH:
        prompt = _structured_prompt(duration, normalized_targets, **kwargs, compact=True)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise PublicError("This clip has too many scene assignments for the provider prompt limit. Use a shorter clip or fewer replacements. No generation was submitted.",
            400, code="provider_prompt_too_long")
    return prompt
