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
from scoring import compute_confidence_score, generate_label

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content", "").strip()
    creator_id = data.get("creator_id", "anonymous")
   
    if len(content) < 50:
        return jsonify({"error": "content must be at least 50 characters."}), 400
    if len(content) > 10_000:
        return jsonify({"error": "content must be under 10,000 characters."}), 400

    content_id = str(uuid.uuid4())
    #Test the first signal in submission endpoint with curl command
    #curl -s -X POST http://localhost:5000/submit -H "Content-Type: application/json" -d "{\"content\": \"The sun dipped below the horizon, painting the sky in hues of amber and rose. I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet.\", \"creator_id\": \"test-user-1\"}"

    # Signal 1 — LLM classifier (Groq)
    llm_score = classify_with_llm(content)["llm_score"]

    # Signal 2 — stylometric heuristics
    style_score = compute_style_score(content)["style_score"]

    # Confidence scorer
    ai_probability, confidence = compute_confidence_score(style_score, llm_score)

    # Transparency label generator
    label_variant, label_text = generate_label(ai_probability, confidence)

    audit.append_entry({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": label_variant,
        "ai_probability": ai_probability,
        "confidence": confidence,
        "style_score": style_score,
        "llm_score": llm_score,
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
            },
            "status": "decided",
        }
    )


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


@app.route("/log", methods=["GET"])
@limiter.limit("60 per minute")
def get_log():
    entries, total = audit.read_entries(limit=50)
    return jsonify({"entries": entries, "total": total})


if __name__ == "__main__":
    app.run(debug=True)
