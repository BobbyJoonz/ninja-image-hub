"""Quality gate: score a generated image in its own ChatGPT conversation, then optionally
regenerate once in that same conversation when text/numbers are wrong.

The gate is deliberately strict about *literal* content (letters, digits, quoted strings)
and lenient about taste. It never loops: one review, at most one refinement.
"""

REQUEST_PLACEHOLDER = "{request}"
DEFAULT_REVIEW_PROMPT = STRICT_REVIEW_PROMPT = """You are a merciless pre-press quality inspector for a generated image.

The user's original request was:
---BEGIN REQUEST---
{request}
---END REQUEST---

Look at the image you just generated in THIS conversation and audit it against that request.

Non-negotiable checks (a single wrong character fails the job):
1. Every word, label, logo text and title must be spelled exactly as requested. Check
   character by character, including Persian/Arabic letter forms (ی/ي, ک/ك), half-spaces,
   diacritics and RTL word order.
2. Every number, percent sign, decimal point, date, price and phone number must be exactly
   right. Digits must not be swapped, added, dropped, mirrored or rendered as a different
   numeral system.
3. Quoted strings in the request are literal: they must appear verbatim and legible.
4. Text must not be cut off, overlapped, duplicated, mirrored or placed over busy detail.
5. The composition must still match the request (subject, colours, aspect, no unrelated
   objects). Do not fail it for taste, style or “make it prettier”.

Reply with ONE JSON object and nothing else (no markdown, no prose):
{{"text_score": 0-10, "numbers_score": 0-10, "spelling_score": 0-10,
 "layout_score": 0-10, "overall": 0-10,
 "issues": ["short, concrete defect", "..."],
 "verdict": "pass" | "fix"}}

Scoring rules: 10 = perfect. 9 = one tiny cosmetic imperfection with no content error.
8 = acceptable with a minor non-content flaw. 7 or less = any wrong letter, wrong digit,
missing word, cut-off text or unreadable label. `overall` must not be higher than the
lowest content score. `verdict` is "fix" whenever any literal text or number is wrong.
"""

CORRECTION_PROMPT = """Regenerate the image from this conversation with the SAME composition,
subject, colours, lighting, framing and style. Change ONLY the text/number defects listed
below — leave every other pixel-level design decision as close to the previous image as
possible. Do not add, remove or rearrange objects. Render the corrected text clearly and
legibly, character-perfect.

Original request:
---BEGIN REQUEST---
{request}
---END REQUEST---

Defects found by the quality inspector:
{issues}

Produce only the corrected image. No explanation, no extra variants."""


def validate_template(template: str) -> str:
    """A custom rubric must stay a *template*: exactly the user request is injected."""
    text = (template or "").strip()
    if len(text) < 80:
        raise ValueError("Quality prompt is too short (min 80 characters).")
    if len(text.encode("utf-8")) > 12000:
        raise ValueError("Quality prompt is too large (max 12 KB).")
    if REQUEST_PLACEHOLDER not in text:
        raise ValueError(
            "Quality prompt must contain the {request} placeholder; it is replaced with "
            "the user's prompt at generation time."
        )
    return text


def build_review_prompt(request: str, template: str | None = None) -> str:
    """Render the master rubric with the user's prompt inside it.

    ``template`` is the operator's override when set, otherwise the shipped default.
    The request is injected as a plain substitution (never .format), so a prompt that
    contains braces cannot break the rubric.
    """
    base = template or DEFAULT_REVIEW_PROMPT
    if REQUEST_PLACEHOLDER not in base:
        base = DEFAULT_REVIEW_PROMPT
    return base.replace(REQUEST_PLACEHOLDER, (request or "").strip()[:4000])


def build_correction_prompt(request: str, issues: list[str]) -> str:
    listed = "\n".join(f"- {str(item)[:300]}" for item in (issues or [])[:12]) or "- text mismatch"
    return CORRECTION_PROMPT.format(request=(request or "").strip()[:4000], issues=listed)


def extract_json(text: str):
    """Return the first balanced JSON object in the text, or None."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, min(len(text), start + 6000)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    import json

                    try:
                        value = json.loads(text[start : index + 1])
                    except ValueError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


def _score(value, low=0, high=10):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(low, min(high, int(round(number))))


def parse_review(text: str):
    """Normalise the assistant's review into a strict, auditable dict."""
    payload = extract_json(text)
    if not payload:
        raise ValueError("Review did not contain a JSON object.")
    scores = {
        key: _score(payload.get(key))
        for key in ("text_score", "numbers_score", "spelling_score", "layout_score", "overall")
    }
    content_scores = [
        scores[key]
        for key in ("text_score", "numbers_score", "spelling_score")
        if scores[key] is not None
    ]
    overall = scores["overall"]
    if overall is None:
        overall = min(content_scores) if content_scores else 0
    if content_scores:
        overall = min(overall, min(content_scores))
    issues = payload.get("issues")
    if isinstance(issues, str):
        issues = [issues]
    if not isinstance(issues, list):
        issues = []
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "fix"):
        verdict = "fix" if overall < 8 or (content_scores and min(content_scores) < 8) else "pass"
    return {
        "text_score": scores["text_score"],
        "numbers_score": scores["numbers_score"],
        "spelling_score": scores["spelling_score"],
        "layout_score": scores["layout_score"],
        "overall": overall,
        "issues": [str(item)[:300] for item in issues[:12]],
        "verdict": verdict,
    }


def needs_refinement(review: dict, threshold: int) -> bool:
    """A single wrong literal must force a refinement regardless of averages."""
    if not review:
        return False
    threshold = int(threshold)
    content = [
        review.get(key)
        for key in ("text_score", "numbers_score", "spelling_score")
        if review.get(key) is not None
    ]
    if content and min(content) < 8:
        return True
    return review.get("overall", 0) < threshold or review.get("verdict") == "fix"


def improved(old: dict, new: dict) -> bool:
    if not new:
        return False
    if not old:
        return True
    return new.get("overall", 0) > old.get("overall", 0) or (
        new.get("overall", 0) == old.get("overall", 0)
        and len(new.get("issues") or []) < len(old.get("issues") or [])
    )
