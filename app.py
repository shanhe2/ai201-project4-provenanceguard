import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
from signals.llm_classifier import classify_with_llm
from signals.stylometric import compute_style_score
from signals.lexical_diversity import compute_lexical_score
from signals.metadata_heuristics import compute_metadata_score
from scoring import compute_confidence_score, generate_label

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)

CERTIFICATE_LABEL_TEXT = (
    "\U0001F6E1 Provenance Certificate — Verified. This creator has confirmed "
    "their identity for this submission by proving ownership of the original "
    "content_id. This is independent of the AI-detection label above: it "
    "verifies who submitted the content, not whether it is AI-generated."
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(force=True, silent=True) or {}
    content_type = data.get("content_type", "text")
    creator_id = data.get("creator_id", "anonymous")
    content_id = str(uuid.uuid4())

    if content_type == "text":
        content = data.get("content", "").strip()

        if len(content) < 50:
            return jsonify({"error": "content must be at least 50 characters."}), 400
        if len(content) > 10_000:
            return jsonify({"error": "content must be under 10,000 characters."}), 400

        # Signal 1 — LLM classifier (Groq)
        llm_score = classify_with_llm(content)["llm_score"]

        # Signal 2 — stylometric heuristics
        style_score = compute_style_score(content)["style_score"]

        # Signal 3 — lexical diversity heuristics
        lexical_score = compute_lexical_score(content)["lexical_score"]

        # Ensemble confidence scorer
        ai_probability, confidence = compute_confidence_score(style_score, llm_score, lexical_score)

        # Transparency label generator
        label_variant, label_text = generate_label(ai_probability, confidence)

        audit.append_entry({
            "content_id": content_id,
            "creator_id": creator_id,
            "content_type": content_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attribution": label_variant,
            "ai_probability": ai_probability,
            "confidence": confidence,
            "style_score": style_score,
            "llm_score": llm_score,
            "lexical_score": lexical_score,
            "status": "classified",
        })

        return jsonify(
            {
                "content_id": content_id,
                "label_variant": label_variant,
                "label_text": label_text,
                "ai_probability": ai_probability,
                "confidence": confidence,
                "signals": {
                    "style_score": style_score,
                    "llm_score": llm_score,
                    "lexical_score": lexical_score,
                },
                "status": "decided",
            }
        )

    elif content_type == "metadata":
        metadata = data.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            return jsonify({"error": "metadata (object) is required for content_type=metadata."}), 400

        # Sole signal for this content type — see signals/metadata_heuristics.py
        metadata_score = compute_metadata_score(metadata)["metadata_score"]
        ai_probability = metadata_score
        # Deterministic rule-based signal: full agreement with itself except
        # the neutral "couldn't tell" case, which should read as uncertain.
        confidence = 0.95 if metadata_score != 0.5 else 0.50

        label_variant, label_text = generate_label(ai_probability, confidence)

        audit.append_entry({
            "content_id": content_id,
            "creator_id": creator_id,
            "content_type": content_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attribution": label_variant,
            "ai_probability": ai_probability,
            "confidence": confidence,
            "metadata_score": metadata_score,
            "status": "classified",
        })

        return jsonify(
            {
                "content_id": content_id,
                "label_variant": label_variant,
                "label_text": label_text,
                "ai_probability": ai_probability,
                "confidence": confidence,
                "signals": {
                    "metadata_score": metadata_score,
                },
                "status": "decided",
            }
        )

    else:
        return jsonify({"error": f"unsupported content_type: {content_type}"}), 400


@app.route("/appeal", methods=["POST"])
@limiter.limit("5 per hour")
def appeal():
    data = request.get_json(force=True, silent=True) or {}
    content_id = data.get("content_id", "").strip()
    creator_reasoning = data.get("creator_reasoning", "").strip()
    creator_id = data.get("creator_id")

    if not content_id:
        return jsonify({"error": "content_id is required."}), 400
    if not (10 <= len(creator_reasoning) <= 1000):
        return jsonify({"error": "creator_reasoning must be 10-1000 characters."}), 400
    if not audit.entry_exists(content_id):
        return jsonify({"error": "content_id not found."}), 404

    updates = {
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
        "appeal_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if creator_id:
        updates["appeal_creator_id"] = creator_id

    audit.update_entry(content_id, updates)

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": "Your appeal has been logged. A moderator will review it.",
        }
    )


@app.route("/certify", methods=["POST"])
@limiter.limit("10 per hour")
def certify():
    data = request.get_json(force=True, silent=True) or {}
    content_id = data.get("content_id", "").strip()
    creator_id = data.get("creator_id", "").strip()

    if not content_id or not creator_id:
        return jsonify({"error": "content_id and creator_id are required."}), 400

    entry = audit.get_entry(content_id)
    if entry is None:
        return jsonify({"error": "content_id not found."}), 404

    # Verification step: the creator must supply the same creator_id used
    # at submission time -- proof they own the original submission. In a
    # production system this would be backed by an authenticated account
    # rather than a self-reported string.
    if entry.get("creator_id") != creator_id:
        return jsonify({"error": "creator_id does not match the original submission."}), 403

    audit.update_entry(content_id, {
        "provenance_certificate": True,
        "certified_timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return jsonify(
        {
            "content_id": content_id,
            "provenance_certificate": True,
            "certificate_label": CERTIFICATE_LABEL_TEXT,
            "message": "Provenance certificate issued.",
        }
    )


@app.route("/log", methods=["GET"])
@limiter.limit("60 per minute")
def get_log():
    entries, total = audit.read_entries(limit=50)
    return jsonify({"entries": entries, "total": total})


@app.route("/analytics", methods=["GET"])
@limiter.limit("60 per minute")
def analytics():
    entries, total = audit.read_entries(limit=10_000)

    if total == 0:
        return jsonify({
            "total_submissions": 0,
            "detection_pattern": {},
            "appeal_rate": 0.0,
            "avg_confidence": None,
            "certification_rate": 0.0,
        })

    variant_counts = {}
    appealed = 0
    certified = 0
    confidences = []

    for e in entries:
        variant = e.get("attribution", "unknown")
        variant_counts[variant] = variant_counts.get(variant, 0) + 1
        if e.get("status") == "under_review" or "appeal_reasoning" in e:
            appealed += 1
        if e.get("provenance_certificate"):
            certified += 1
        if e.get("confidence") is not None:
            confidences.append(e["confidence"])

    detection_pattern = {
        variant: round(count / total, 4) for variant, count in variant_counts.items()
    }
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None

    return jsonify(
        {
            "total_submissions": total,
            "detection_pattern": detection_pattern,
            "appeal_rate": round(appealed / total, 4),
            "avg_confidence": avg_confidence,
            "certification_rate": round(certified / total, 4),
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
