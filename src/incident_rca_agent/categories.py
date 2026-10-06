"""The ten failure types the agent can name, written once for the prompt, the schema and the Slack message.

Each type also has a signature: the error messages the runbooks list for it. The confidence line uses the
signature to check that a cited warning or error really shows the failure type the model named.
"""

from __future__ import annotations

import re

CATEGORIES = {
    "upstream_schema_change": (
        "upstream schema change",
        "a source renamed, dropped or retyped a field, so loads fail their schema or contract checks",
    ),
    "api_rate_limit": (
        "API rate limit (HTTP 429)",
        "an API answers HTTP 429 Too Many Requests because a quota is used up",
    ),
    "disk_full": (
        "disk full",
        "a volume reached 100% and writes fail with No space left on device",
    ),
    "expired_credentials": (
        "expired credentials",
        "a token, password, key or certificate expired, so authentication fails (401, 403, login refused)",
    ),
    "late_upstream_data": (
        "late upstream data",
        "upstream data did not arrive on time; runs succeed but bring no or little new data",
    ),
    "out_of_memory": (
        "out of memory (exit 137)",
        "a run used more memory than its limit and was killed: OOMKilled, exit code 137",
    ),
    "bad_config_deploy": (
        "bad config deploy",
        "a config change deployed shortly before the failure made the pipeline fail at start-up",
    ),
    "duplicate_keys": (
        "duplicate keys",
        "duplicate rows or keys entered a table and broke unique tests or merges further down",
    ),
    "daylight_saving_bug": (
        "daylight-saving bug",
        "local-time handling broke on a clock-change day: an hour that does not exist or happens twice",
    ),
    "dns_failure": (
        "DNS failure",
        "a host name could not be resolved, so connections failed before they started",
    ),
}
UNKNOWN = "unknown"
CATEGORY_IDS = (*CATEGORIES, UNKNOWN)

_SIGNS = {
    "upstream_schema_change": r"data contract|contract violation|required field|unexpected field",
    "api_rate_limit": r"too many requests|http 429|rate limit|quota",
    "disk_full": r"no space left|errno 28|disk usage",
    "expired_credentials": r"unauthori[sz]ed|http 401|expired|expires|authentication failed|permission denied",
    "late_upstream_data": r"no file|not ready|behind the primary|consumer lag|watermark",
    "out_of_memory": r"oomkilled|exit code 137|out of memory|gib (?:memory )?limit",
    "bad_config_deploy": r"config error|invalid config",
    "duplicate_keys": r"duplicate|restarted from offset|re-reading|loaded twice|matched .* twice",
    "daylight_saving_bug": r"nonexistenttimeerror|ambiguoustimeerror|does not exist in europe/|is ambiguous in europe/",
    "dns_failure": r"name resolution|nxdomain|could not resolve|dns lookup",
}
SIGNATURES = {category: re.compile(pattern, re.IGNORECASE) for category, pattern in _SIGNS.items()}


def category_name(category: str) -> str:
    return CATEGORIES[category][0] if category in CATEGORIES else "unknown cause"


def shows(category: str, text: str) -> bool:
    """Does this text carry the signature of the failure type? Always False for unknown."""
    signature = SIGNATURES.get(category)
    return bool(signature and signature.search(text))
