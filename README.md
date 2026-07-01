# ai201-project4-provenanceguard

## API Endpoints

### POST /submit
Submits content for classification. Accepts an optional `content_type` (`"text"`, the default, or `"metadata"` — see Multi-Modal Support below). For text, runs all three detection signals, combines them into `ai_probability`/`confidence` via the ensemble in `scoring.py`, generates a transparency label, and logs the decision.

**Rate limit:** 10 requests / minute, 100 / day per IP.

### POST /certify
Issues a Provenance Certificate for a prior submission — see Provenance Certificate below.

**Rate limit:** 10 requests / hour per IP.

### GET /analytics
Returns aggregate metrics across the audit log — see Analytics Dashboard below.

**Rate limit:** 60 requests / minute per IP.

### POST /appeal
Lets a creator contest a classification using the `content_id` from a prior `/submit` response.

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "PASTE-CONTENT-ID-HERE", "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."}'
```

Validates that `content_id` exists (404 if not) and `creator_reasoning` is 10–1000 characters (400 if not), then sets `status` to `under_review` and attaches `appeal_reasoning` + `appeal_timestamp` to the existing audit log entry — verified via `GET /log`:

```json
{
  "content_id": "2da04f2e-cea2-49d7-8d00-b9612914154e",
  "creator_id": "appeal-test-user",
  "timestamp": "2026-06-30T23:57:33.662648+00:00",
  "attribution": "ai_high",
  "ai_probability": 0.58,
  "confidence": 0.7,
  "style_score": 0.4,
  "llm_score": 0.7,
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "appeal_timestamp": "2026-06-30T23:57:41.735272+00:00"
}
```

No automated re-classification occurs — a human moderator reads the original signal scores alongside the appeal reasoning.

**Rate limit:** 5 requests / hour per IP.

### GET /log
Returns the structured audit log (`{"entries": [...], "total": N}`).

**Rate limit:** 60 requests / minute per IP.

---

## Transparency Labels

`scoring.py`'s `generate_label()` maps `(ai_probability, confidence)` to one of three exact label strings, gated first on signal agreement (`confidence`) and then on the combined probability. All three variants were confirmed reachable with real signal scores from the live 3-signal ensemble:

| Input | style_score | llm_score | lexical_score | ai_probability | confidence | label_variant |
|---|---|---|---|---|---|---|
| Clearly AI-generated paragraph | 0.822 | 0.850 | *(abstained)* | 0.840 | 0.972 | `ai_high` |
| Casual human anecdote | 0.400 | 0.200 | *(abstained)* | 0.275 | 0.800 | `human_high` |
| Formal academic excerpt (signals disagree) | 0.400 | 0.700 | *(abstained)* | 0.588 | 0.700 | `ai_high`* |

\* See "Known Limitations" below — this case lands exactly on the `confidence == 0.70` boundary and is a documented false positive, not a bug in the label function. lexical_score abstains on all three because each sample is under the 80-word reliability floor — see Ensemble Detection below.

---

## Ensemble Detection

Three independent, distinct detection signals feed the ensemble:

| # | Signal | Module | What it measures |
|---|---|---|---|
| 1 | Stylometric heuristics | `signals/stylometric.py` | Sentence-length variance, hedge/filler phrase density, varied-punctuation density |
| 2 | LLM classifier (Groq) | `signals/llm_classifier.py` | Semantic patterns an LLM recognizes as characteristic of its own family's output |
| 3 | Lexical diversity | `signals/lexical_diversity.py` | Type-token ratio and word-length uniformity |

**Weighting strategy** (`scoring.py`, `compute_confidence_score`):

```
ai_probability = 0.30 × style_score + 0.50 × llm_score + 0.20 × lexical_score
```

Weights reflect how much signal each source captures on its own: the LLM classifier gets the largest share (0.50) because it reasons about higher-level semantic patterns; stylometrics gets the next share (0.30) as a solid structural signal; lexical diversity gets the smallest share (0.20) because type-token ratio and word-length uniformity are the noisiest of the three on short text, so it acts as a tie-breaking nudge rather than a primary vote.

**Conflict resolution.** `confidence` measures how tightly the voting signals agree, generalizing the original 2-signal `|style - llm|` formula to N signals via range:

```
confidence = 1.0 - (max(voting_scores) - min(voting_scores))
```

A wide spread between any two signals drags `confidence` below 0.70 and (via the existing threshold table) forces the label to `uncertain` — no single signal can out-vote a disagreement. This is intentional: the ensemble is designed to admit uncertainty rather than force a false consensus.

**Handling a signal that can't vote.** `lexical_score` returns `None` (abstains) when the passage is under 80 words, since type-token ratio is a function of sample length and is unreliable below that — a naive "neutral 0.5" would still count as a vote of disagreement against two signals that strongly agree, wrongly pushing typical short submissions toward `uncertain`. Abstaining signals are dropped from both the weighted average (remaining weights renormalize to sum to 1) and the agreement calculation. This was a real bug caught during testing: the first version of this signal returned a neutral `0.5` on short text and it dragged an obviously-AI paragraph's confidence down to 0.15 (`uncertain`) purely from a fabricated disagreement — fixed by treating "not enough data" as an abstention, not a vote.

**Demo — individual signal scores alongside the ensemble result** (from a live `/submit` call):

```json
{
  "ai_probability": 0.8396,
  "confidence": 0.9724,
  "label_variant": "ai_high",
  "signals": {
    "style_score": 0.8224,
    "llm_score": 0.85,
    "lexical_score": null
  }
}
```

---

## Provenance Certificate

A Provenance Certificate is a separate claim from the AI-detection label: it verifies *who submitted the content*, not *whether it's AI-generated*. A creator earns one via `POST /certify` by supplying the `content_id` and the same `creator_id` used at submission time — proving they own the original submission (in a production system this would be backed by an authenticated account rather than a self-reported string; here it demonstrates the verification-gate design without building full auth).

```bash
curl -s -X POST http://localhost:5000/certify \
  -H "Content-Type: application/json" \
  -d '{"content_id": "fc79dc1b-5f65-4445-a3b8-227317c5d7ad", "creator_id": "test-user-1"}'
```

On a `creator_id` mismatch, the endpoint returns 403 rather than silently issuing the certificate (verified live: certifying with the wrong `creator_id` for a different entry returned `{"error": "creator_id does not match the original submission."}` with HTTP 403).

On success, the audit entry gains `provenance_certificate: true` and `certified_timestamp`, and the response includes a certificate label distinguishable from the three standard transparency labels:

> 🛡 Provenance Certificate — Verified. This creator has confirmed their identity for this submission by proving ownership of the original content_id. This is independent of the AI-detection label above: it verifies who submitted the content, not whether it is AI-generated.

Note this is visually and semantically distinct from `ai_high`/`human_high`/`uncertain` — a certified entry can carry *any* of those three detection labels alongside the certificate, since the two claims are orthogonal.

---

## Analytics Dashboard

`GET /analytics` aggregates the audit log into 4 metrics:

```json
{
  "total_submissions": 6,
  "detection_pattern": { "ai_high": 0.5, "human_high": 0.3333, "uncertain": 0.1667 },
  "appeal_rate": 0.1667,
  "avg_confidence": 0.8121,
  "certification_rate": 0.1667
}
```

- **`detection_pattern`** (required) — ratio of AI vs. human vs. uncertain verdicts across all submissions.
- **`appeal_rate`** (required) — fraction of submissions that have had an appeal filed (`status: "under_review"`).
- **`avg_confidence`** (chosen metric) — mean `confidence` across all decisions, a rough proxy for how often the ensemble's signals actually agreed versus punted to `uncertain`.
- **`certification_rate`** (bonus) — fraction of submissions with a Provenance Certificate issued.

---

## Multi-Modal Support

`POST /submit` accepts `content_type: "metadata"` as a second pipeline alongside the default `"text"` path, demonstrating that the same submission → signal → ensemble → label → audit-log architecture generalizes beyond prose.

```bash
curl -s -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
  -d '{"content_type": "metadata", "creator_id": "test-user-4", "metadata": {"software": "Midjourney v6", "width": 1024, "height": 1024}}'
```

**How it's handled differently from text:** instead of `content` (a string, 50–10,000 chars), the request supplies a `metadata` object describing an image (generation-tool tag, camera EXIF-style fields, dimensions). Text's three signals (stylometric, LLM, lexical) don't apply to structured metadata, so this pipeline uses a single dedicated signal instead: `signals/metadata_heuristics.py`'s `compute_metadata_score()`, a rule-based check that flags known AI image-generator names (Midjourney, DALL-E, Stable Diffusion, etc.) in the `software`/`generator` field as strong AI evidence (`0.95`), flags the presence of ≥2 real-camera EXIF fields (make, model, ISO, exposure, GPS) as strong human evidence (`0.05`), and falls back to `0.5` (`uncertain`) when neither is present. Since it's the only signal for this content type, it feeds `generate_label()` directly rather than through the 3-signal text ensemble.

Verified live with all three branches:

| metadata | metadata_score | label_variant |
|---|---|---|
| `{"software": "Midjourney v6", ...}` | 0.95 | `ai_high` |
| `{"camera_make": "Canon", "camera_model": "EOS R5", "iso": 400, "exposure_time": "1/250"}` | 0.05 | `human_high` |
| `{"width": 800, "height": 600}` (no markers) | 0.50 | `uncertain` |

---

## Rate Limiting

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 10/min, 100/day per IP | Each submission triggers a paid Groq API call. 10/min comfortably covers a writer submitting their own work in one sitting, while bounding the cost of a single abusive IP. The 100/day cap stops a script from grinding through the per-minute window indefinitely over a full day. |
| `POST /appeal` | 5/hour per IP | Appeals consume moderator review time, a scarcer resource than API quota. 5/hr lets a creator contest multiple pieces without opening the moderation queue to spam. |
| `GET /log` | 60/min per IP | Read-only and low-cost; the higher limit supports dashboards/monitoring polling the log. |

**Evidence** — 12 rapid `POST /submit` requests in under a minute (1 prior request in the same window had already been made, so the 10th here is the 11th overall):

```
200
200
200
200
200
200
200
200
200
429
429
429
```

The limiter correctly cut off requests once the 10/minute ceiling was reached.

---

## Audit Log

Every decision is appended to `audit_log.jsonl` as one structured JSON object per line (JSON Lines format), never unformatted console output. Each entry captures: `timestamp`, `content_id`, `content_type`, `attribution` (the label result), `confidence`, all individual signal scores for that content type, and whether an appeal has been filed (`status: "under_review"` plus `appeal_reasoning`/`appeal_timestamp`) or a certificate issued (`provenance_certificate: true` plus `certified_timestamp`).

Current log (6 entries, retrieved via `GET /log`) — 3 text submissions (one certified, one appealed) and 3 metadata submissions:

```json
{"content_id": "fc79dc1b-5f65-4445-a3b8-227317c5d7ad", "creator_id": "test-user-1", "content_type": "text", "timestamp": "2026-07-01T00:18:11.418418+00:00", "attribution": "ai_high", "ai_probability": 0.8396, "confidence": 0.9724, "style_score": 0.8224, "llm_score": 0.85, "lexical_score": null, "status": "classified", "provenance_certificate": true, "certified_timestamp": "2026-07-01T00:18:27.441273+00:00"}
{"content_id": "50f022a9-9583-4fd1-b781-ef5a987b8357", "creator_id": "test-user-2", "content_type": "text", "timestamp": "2026-07-01T00:18:11.957171+00:00", "attribution": "human_high", "ai_probability": 0.2748, "confidence": 0.8004, "style_score": 0.3996, "llm_score": 0.2, "lexical_score": null, "status": "classified"}
{"content_id": "e6792d4d-ffd0-419b-833b-2ac355331509", "creator_id": "test-user-3", "content_type": "text", "timestamp": "2026-07-01T00:18:18.178996+00:00", "attribution": "ai_high", "ai_probability": 0.5875, "confidence": 0.7, "style_score": 0.4, "llm_score": 0.7, "lexical_score": null, "status": "under_review", "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.", "appeal_timestamp": "2026-07-01T00:18:27.193550+00:00"}
{"content_id": "2f02d250-13d1-4b20-984f-3d3cb795e214", "creator_id": "test-user-4", "content_type": "metadata", "timestamp": "2026-07-01T00:18:36.390412+00:00", "attribution": "ai_high", "ai_probability": 0.95, "confidence": 0.95, "metadata_score": 0.95, "status": "classified"}
{"content_id": "6bd762e4-8496-488d-9243-ec76bf0b4904", "creator_id": "test-user-5", "content_type": "metadata", "timestamp": "2026-07-01T00:18:36.673139+00:00", "attribution": "human_high", "ai_probability": 0.05, "confidence": 0.95, "metadata_score": 0.05, "status": "classified"}
{"content_id": "313aa3ee-fa9f-406d-b84c-bee37a6fbbd1", "creator_id": "test-user-6", "content_type": "metadata", "timestamp": "2026-07-01T00:18:36.937424+00:00", "attribution": "uncertain", "ai_probability": 0.5, "confidence": 0.5, "metadata_score": 0.5, "status": "classified"}
```

The first entry shows a Provenance Certificate: `provenance_certificate: true` and `certified_timestamp` are added without touching the original detection scores or label. The third entry shows a filed appeal: the original signal scores and label are preserved unchanged, `status` flipped to `under_review`, and the creator's reasoning is attached directly. The metadata entries show `lexical_score`/`style_score`/`llm_score` simply absent (not applicable) and `metadata_score` present instead — the schema is additive per content type, not a rigid fixed shape.

---

## Spec Reflection

A few places where the implementation diverged from `planning.md`, and why:

**Audit log field naming: `attribution` instead of `label_variant`.** `planning.md`'s API Surface section documents the log schema with a `label_variant` field. The actual audit entries (`audit.py` / `app.py`) use `attribution` instead — a naming choice made early (Milestone 3, before scoring existed) that stuck through later milestones rather than being renamed to match the spec. Functionally identical, but anyone cross-referencing `planning.md` against `audit_log.jsonl` will hit a mismatch.

**Appeal field names: `creator_reasoning`/`appeal_reasoning` instead of `reason`.** `planning.md`'s Appeals Workflow spec defines the request field as `reason` (10–1,000 chars) and the attached log object as `{ appeal_timestamp, creator_id, reason }`. The implemented `POST /appeal` instead accepts `creator_reasoning` and logs it as a top-level `appeal_reasoning` field (not nested under an `appeal` object). This diverged because later implementation guidance specified the `creator_reasoning` field name and a flat log structure directly, superseding the original planning doc — the spec was written before the exact wire format was pinned down, and the two were never reconciled back into `planning.md`.

**`GET /log` ignores `limit`/`offset` query parameters.** `planning.md`'s API Surface documents `GET /log` as accepting `limit` (default 50) and `offset` (default 0) query params. The implemented `get_log()` hardcodes `limit=50` and passes no `offset`, even though `audit.read_entries()` already supports both parameters. This is a real gap, not an intentional simplification — the endpoint was wired up to unblock the M3 audit-log requirement and pagination was never revisited once the harder scoring/appeal work took priority.

**2-signal formula (0.40/0.60) superseded by a 3-signal ensemble (0.30/0.50/0.20).** `planning.md`'s original spec only covers stylometric + LLM signals. Adding lexical diversity as a third signal for the Ensemble Detection bonus required re-deriving both the weighting (redistributing weight away from stylometrics and the LLM classifier to make room for the new signal) and the agreement formula (`1 - |a - b|` doesn't generalize past two signals, so it became `1 - (max - min)` over whichever signals actually vote). `planning.md` was never updated to reflect this — it still documents the 2-signal version. Discovered mid-implementation: an early version of the lexical signal returned a fabricated "neutral 0.5" instead of abstaining on short text, which silently broke the ensemble by dragging confident, agreeing signals down to `uncertain` — a bug the original 2-signal spec had no reason to anticipate, since it never had a signal that needed to "opt out."

These aren't hidden: they're the kind of drift that happens when a spec is written up front and implementation details get pinned down incrementally across multiple milestones. None of them affect correctness of the core detection/scoring pipeline, but a stricter follow-up pass would either update `planning.md` to match reality or wire up the missing `offset` support in `get_log()`.

---

## AI Usage

Three specific instances of AI-assisted development, and what was changed after reviewing the output:

**1. Confidence scoring logic (`scoring.py`).** Directed the AI tool to implement `compute_confidence_score(style_score, llm_score)` and `generate_label(ai_probability, confidence)` from `planning.md`'s Implementation Spec §2 (the weighted-blend formula, the agreement formula, and the threshold table). The generated code looked correct on inspection, but rather than accepting it as-is, I wrote explicit boundary tests for the exact edges the spec calls out (`confidence == 0.70`, `ai_probability == 0.55`) since AI-generated threshold comparisons commonly use the wrong operator (`>` vs `>=`) at boundaries. The generated code passed all four boundary checks unchanged — so nothing was rewritten, but the verification step was the actual work, not a rubber stamp.

**2. `POST /appeal` endpoint.** Directed the AI tool to build the endpoint from `planning.md`'s Appeals Workflow section, which specifies a `reason` field and a nested `appeal` object (`{ appeal_timestamp, creator_id, reason }`) attached to the log entry. I overrode both of these: the request field was renamed to `creator_reasoning` and the log fields were flattened to top-level `appeal_reasoning`/`appeal_timestamp` instead of a nested object, because the grading rubric's literal test commands expected that exact shape. I chose to match the grading contract over the original planning doc rather than silently diverging from both.

**3. Signal comparison test design.** Directed the AI tool to run both detection signals against four deliberately chosen inputs (clearly-AI, clearly-human, and two borderline cases) and report the results. When the formal-academic borderline case came back with `llm_score = 0.700` — much higher than my intuition for genuinely human writing — I didn't accept the combined `ai_high` label at face value. I had the two signal scores printed separately to isolate which one was misbehaving, confirmed it was the LLM classifier (not stylometrics) over-flagging structurally uniform prose, and decided to document this as a known limitation in the README rather than retune the 0.40/0.60 signal weights based on a single data point.

---

## Known Limitations

Testing the confidence scoring with deliberately chosen inputs (clearly AI-generated, clearly human-written, and two borderline cases) surfaced two known failure modes. Both are predicted in `planning.md`'s "Anticipated Edge Cases" section, and the test results below are concrete evidence of them rather than hypotheticals.

### Formal/uniform human writing gets flagged as AI

A formal academic excerpt (on monetary policy and asset price inflation) produced `style_score = 0.400` and `llm_score = 0.700`, combining to `ai_probability = 0.580` and `confidence = 0.700` — just at the threshold, resulting in a confident **`ai_high`** label for text that was actually human-written.

Splitting the signals showed the LLM classifier (`llm_score = 0.700`), not the stylometric heuristics, was the one over-flagging the text — formal, jargon-heavy, structurally uniform prose resembles patterns the classifier associates with AI output. This matches Edge Case 1 in `planning.md`: formally structured human writing (academic prose, legal documents, sonnets) can trip both signals into agreement on a false positive.

### Lightly-edited AI text evades detection

A short reflective passage on remote work (written to resemble AI output lightly edited for personal voice) produced `style_score = 0.296` and `llm_score = 0.200` — both signals agreed it was human, yielding `confidence = 0.904` and a confident **`human_high`** label, a false negative.

This matches Edge Case 2 in `planning.md`: editing disrupts the stylometric signal (sentence-length variance starts to look human), and personal framing can be enough to also fool the LLM classifier. Signal-based detection cannot reliably catch AI content that has been thoughtfully edited by a human.

### Why we aren't tuning weights to fix these

The scoring formula and thresholds were verified to exactly match the spec in `planning.md` (including boundary behavior at `confidence == 0.70` and `ai_probability == 0.55`), so these failures originate in the underlying signal scores, not the combination logic. With only a handful of test inputs, adjusting weights to fix one case risks overfitting and shifting the failure mode elsewhere (e.g., making genuine AI text more likely to fall into `uncertain`). These limitations are recorded here as known tradeoffs rather than patched ad hoc.
