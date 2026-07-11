"""Shared Snowpark/Cortex helpers used by every stored procedure module in
this package. The whole procs/ directory is uploaded for every entity (see
../snowflake.yml), so any proc module can `import cortex_common` directly —
no separate packaging step needed.
"""

import json
import re

DEFAULT_MODEL_NAME = "claude-4-sonnet"


def complete(session, model_name, prompt):
    """Call Cortex COMPLETE and return the raw text response."""
    row = session.sql(
        "select snowflake.cortex.complete(?, ?) as resp",
        params=[model_name, prompt],
    ).collect()
    return row[0]["RESP"]


def extract_json(text):
    """Pull the first {...} block out of a model reply and parse it.

    Models sometimes wrap JSON in commentary even when told not to
    ("Sure! Here's the JSON: {...}") — this strips that off.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Model did not return JSON: {text[:300]}")
    return json.loads(match.group(0))
