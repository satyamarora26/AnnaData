from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


Tier = Literal["official", "extension", "reference"]
FetchMode = Literal["http", "browser"]

TRUSTED_HOSTS = {
    "pmkisan.gov.in",
    "pmfby.gov.in",
    "www.pib.gov.in",
    "soilhealth.dac.gov.in",
    "enam.gov.in",
    "icar.gov.in",
    "www.icar.gov.in",
    "nhb.gov.in",
    "www.nhb.gov.in",
    "pau.edu",
    "www.pau.edu",
}


@dataclass(frozen=True)
class SourceSpec:
    id: str
    title: str
    authority: str
    tier: Tier
    source_url: str
    published_on: date | None
    topics: tuple[str, ...]
    required_terms: tuple[str, ...]
    scope: dict[str, tuple[str, ...]]
    local_path: Path
    fetch_mode: FetchMode


def assert_trusted_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TRUSTED_HOSTS:
        raise ValueError(f"untrusted source host: {parsed.hostname or url}")


def _tuple_scope(raw: dict) -> dict[str, tuple[str, ...]]:
    return {key: tuple(str(value) for value in values) for key, values in raw.items()}


def load_catalog(path: Path) -> dict[str, SourceSpec]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    catalog: dict[str, SourceSpec] = {}
    for row in rows:
        assert_trusted_url(row["source_url"])
        if row["tier"] not in {"official", "extension", "reference"}:
            raise ValueError(f"invalid source tier: {row['tier']}")
        published = date.fromisoformat(row["published_on"]) if row.get("published_on") else None
        spec = SourceSpec(
            id=row["id"],
            title=row["title"],
            authority=row["authority"],
            tier=row["tier"],
            source_url=row["source_url"],
            published_on=published,
            topics=tuple(row["topics"]),
            required_terms=tuple(row["required_terms"]),
            scope=_tuple_scope(row.get("scope", {})),
            local_path=Path(row["local_path"]),
            fetch_mode=row["fetch_mode"],
        )
        if spec.id in catalog:
            raise ValueError(f"duplicate source id: {spec.id}")
        catalog[spec.id] = spec
    return catalog


def scope_score(scope: dict, state: str | None, crop: str | None) -> int | None:
    score = 0
    for key, requested in (("states", state), ("crops", crop)):
        allowed = {str(value).casefold() for value in scope.get(key, ())}
        if not allowed:
            continue
        if not requested or requested.casefold() not in allowed:
            return None
        score += 2
    return score
