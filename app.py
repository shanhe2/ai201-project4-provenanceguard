import uuid

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute")
def submit():
    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content", "").strip()
    creator_id = data.get("creator_id", "anonymous")

    if len(content) < 50:
        return jsonify({"error": "content must be at least 50 characters."}), 400
    if len(content) > 10_000:
        return jsonify({"error": "content must be under 10,000 characters."}), 400

    content_id = str(uuid.uuid4())

    # TODO: Signal 1 — stylometric heuristics
    style_score = None

    # TODO: Signal 2 — LLM classifier (Groq)
    llm_score = None

    # TODO: confidence scorer
    ai_probability = None
    confidence = None

    # TODO: transparency label generator
    label_variant = "pending"
    label_text = "Signals not yet wired."

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


if __name__ == "__main__":
    app.run(debug=True)
