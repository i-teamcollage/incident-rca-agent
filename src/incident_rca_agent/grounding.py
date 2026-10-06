"""The grounding check: is a claim traced to a real source?

A claim is grounded when all three hold:

1. it cites at least one source id;
2. every cited id was actually shown during this investigation;
3. every identifier and number in the claim appears in at least one of the cited sources.

Identifiers are words with a digit or an underscore (pipeline names, error codes, ids, times) and host
names. Plain words are not checked: the check verifies facts, not wording. Numbers are compared without
thousands separators, signs or percent signs, and a time may leave out its seconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .categories import CATEGORY_IDS
from .tools import TOOLS, Source

_TOKEN = re.compile(r"[a-z0-9_][a-z0-9_.:/%+\-]*")
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})t(\d{2}:\d{2}(?::\d{2})?)z?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_HOST = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+")
_SECONDS = re.compile(r"\d{2}:\d{2}:\d{2}")
_UNIT = re.compile(r"(\d+(?:\.\d+)?)([a-z]{1,3})")
_SEPARATORS = re.compile(r"[-/:=,]")
# Words of the answer itself, not facts about the incident.
NOT_FACTS = frozenset({*CATEGORY_IDS, *(tool["function"]["name"] for tool in TOOLS)})


@dataclass
class ClaimCheck:
    text: str
    source_ids: list[str]
    grounded: bool
    problems: list[str] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    cleaned = _THOUSANDS.sub("", _ISO.sub(r"\1 \2", text.lower()))
    tokens = []
    for match in _TOKEN.finditer(cleaned):
        token = match.group(0).rstrip(".:/-+")
        if token.endswith("%"):
            token = token[:-1].rstrip(".:/-+")
        if token:
            tokens.append(token)
    return tokens


def _digit_or_underscore(token: str) -> bool:
    return "_" in token or any(character.isdigit() for character in token)


def _plain(part: str) -> str:
    """Numbers without leading zeros, so 02 and 2 compare equal."""
    return (part.lstrip("0") or "0") if part.isdigit() else part


def checkable(text: str) -> list[str]:
    """The identifiers and numbers of a text, in order, each once."""
    found: list[str] = []
    for token in _tokens(text):
        if token in NOT_FACTS or token in found:
            continue
        if _digit_or_underscore(token) or (len(token) >= 6 and _HOST.fullmatch(token)):
            found.append(token)
    return found


class _Vocabulary:
    """Everything a set of sources says, ready for lookups."""

    def __init__(self, texts: list[str]):
        self.whole: set[str] = set()
        self.parts: set[str] = set()
        for text in texts:
            for token in _tokens(text):
                self.whole.add(token)
                self.whole.add(_plain(token))
                if _SECONDS.fullmatch(token):
                    self.whole.add(token[:5])
                for part in _SEPARATORS.split(token):
                    if part:
                        self.parts.add(_plain(part))

    def has(self, token: str) -> bool:
        if token in self.whole or _plain(token) in self.whole:
            return True
        if not token.isdigit() and token in self.parts:  # a host inside a URL, a pipeline inside a path
            return True
        unit = _UNIT.fullmatch(token)
        if unit and _plain(unit.group(1)) in self.whole:
            return True
        if _SEPARATORS.search(token):
            pieces = [piece for piece in _SEPARATORS.split(token) if piece and _digit_or_underscore(piece)]
            return bool(pieces) and all(_plain(piece) in self.whole | self.parts for piece in pieces)
        return False


def normalize_id(source_id: str) -> str:
    return str(source_id).strip().strip("[]()`'\"").strip().upper()


def check_claim(text: str, source_ids: list[str], sources: dict[str, Source]) -> ClaimCheck:
    cited = list(dict.fromkeys(normalize_id(source_id) for source_id in source_ids if str(source_id).strip()))
    if not cited:
        return ClaimCheck(text, cited, False, ["cites no source"])
    missing = [source_id for source_id in cited if source_id not in sources]
    if missing:
        problems = [f"cites {source_id}, which this investigation never retrieved" for source_id in missing]
        return ClaimCheck(text, cited, False, problems)
    vocabulary = _Vocabulary([sources[source_id].text for source_id in cited])
    absent = [token for token in checkable(text) if not vocabulary.has(token)]
    problems = [f"'{token}' is not in the cited sources" for token in absent[:3]]
    return ClaimCheck(text, cited, not problems, problems)
