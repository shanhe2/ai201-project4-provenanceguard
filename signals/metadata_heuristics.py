AI_GENERATOR_MARKERS = [
    "midjourney", "dall-e", "dall·e", "stable diffusion", "firefly",
    "runway", "leonardo.ai", "imagen", "sora", "gemini image", "flux",
]

CAMERA_FIELDS = ["camera_make", "camera_model", "exposure_time", "iso", "gps"]


def compute_metadata_score(metadata: dict) -> dict:
    """
    Signal for the metadata content type (Multi-Modal Support).
    Returns {"metadata_score": float} where 1.0 = strong AI signal.

    Rule-based, not statistical: checks the declared generator/software
    field against known AI image-tool names, and checks for camera
    capture fields that only appear on real photographs.
    """
    software = str(metadata.get("software") or metadata.get("generator") or "").lower()

    if any(marker in software for marker in AI_GENERATOR_MARKERS):
        return {"metadata_score": 0.95}

    camera_fields_present = sum(1 for f in CAMERA_FIELDS if metadata.get(f))
    if camera_fields_present >= 2:
        return {"metadata_score": 0.05}

    return {"metadata_score": 0.5}
