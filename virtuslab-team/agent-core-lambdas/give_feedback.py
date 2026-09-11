import json
import os
from datetime import datetime, timezone

import boto3

BUCKET = os.environ.get("FEEDBACK_BUCKET", "energy-knowledge-feedback")

# Fields the tool schema declares. Listed so they keep a stable order in the
# file; anything else the caller sends is still written out, after these.
KNOWN_FIELDS = ("user_question", "answer", "user_reaction", "source")

ENTRY_DELIMITER = "\n\n---\n\n"

s3 = boto3.client("s3")


def lambda_handler(event, context):
    try:
        params = event if isinstance(event, dict) else {}

        now = datetime.now(timezone.utc)
        key = f"feedback-{now:%Y-%m-%d}.md"
        entry = _render_entry(params, now)

        # S3 objects cannot be appended to, so the day's file is read back and
        # rewritten whole. Two invocations overlapping on the same day can lose
        # an entry that way; acceptable at feedback volumes, not at request
        # volumes.
        existing = _read(key)
        body = entry if existing is None else existing.rstrip("\n") + ENTRY_DELIMITER + entry

        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )

        return _reply(200, {"status": "accepted", "bucket": BUCKET, "key": key})

    except Exception as exc:
        # The reason, not a traceback: whatever is on the other end of this
        # Lambda has to be able to tell a person what went wrong.
        return _reply(500, {"error": f"{type(exc).__name__}: {exc}"})


def _read(key: str):
    """The day's file as text, or None when nothing has been recorded yet."""
    try:
        return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return None


def _render_entry(params: dict, now: datetime) -> str:
    lines = [f"## {now:%Y-%m-%dT%H:%M:%SZ}", ""]

    for field in KNOWN_FIELDS:
        lines += [f"**{field}**", "", _format(params.get(field)), ""]

    for name, value in params.items():
        if name not in KNOWN_FIELDS:
            lines += [f"**{name}**", "", _format(value), ""]

    return "\n".join(lines).rstrip("\n") + "\n"


def _format(value) -> str:
    if value is None or value == "":
        return "_not provided_"
    if isinstance(value, str):
        return value.strip()
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _reply(status: int, payload: dict) -> dict:
    # ensure_ascii=False because the corpus is German: escaping "abhängig" into
    # abhängig makes every log line unreadable for no gain.
    return {"statusCode": status,
            "body": json.dumps(payload, ensure_ascii=False)}
