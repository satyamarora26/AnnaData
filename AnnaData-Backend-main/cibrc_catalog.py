"""Declared identities for the six retained CIB&RC pesticide registers."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse


REQUIRED_CATEGORIES = frozenset({
    "insecticide",
    "fungicide",
    "biofungicide",
    "herbicide",
    "pgr",
    "bioinsecticide",
})
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "data" / "cibrc_manifest.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CibrcArtifact:
    category: str
    filename: str
    source_url: str
    source_page: str
    authority: str
    as_on: str
    sha256: str


def _official_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and (parsed.hostname or "").casefold() == "ppqs.gov.in"


def load_catalog(path: Path = DEFAULT_MANIFEST) -> dict[str, CibrcArtifact]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    catalog = {}
    for row in rows:
        artifact = CibrcArtifact(**row)
        if artifact.category in catalog:
            raise ValueError(f"duplicate CIB&RC category: {artifact.category}")
        if artifact.filename != f"{artifact.category}.pdf" or Path(artifact.filename).name != artifact.filename:
            raise ValueError(f"invalid CIB&RC filename for {artifact.category}")
        if not _official_url(artifact.source_url) or not _official_url(artifact.source_page):
            raise ValueError(f"untrusted CIB&RC source for {artifact.category}")
        if not artifact.authority.strip() or not artifact.as_on.strip():
            raise ValueError(f"incomplete CIB&RC identity for {artifact.category}")
        if not _SHA256.fullmatch(artifact.sha256):
            raise ValueError(f"invalid CIB&RC hash for {artifact.category}")
        catalog[artifact.category] = artifact
    actual = set(catalog)
    if actual != REQUIRED_CATEGORIES:
        missing = sorted(REQUIRED_CATEGORIES - actual)
        extra = sorted(actual - REQUIRED_CATEGORIES)
        raise ValueError(f"invalid CIB&RC catalog; missing={missing} extra={extra}")
    return catalog


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_artifacts(
    folder: Path, catalog: dict[str, CibrcArtifact] | None = None
) -> list[tuple[CibrcArtifact, Path]]:
    catalog = catalog or load_catalog()
    expected_names = {artifact.filename for artifact in catalog.values()}
    actual_names = {path.name for path in folder.glob("*.pdf")}
    unexpected = sorted(actual_names - expected_names)
    if unexpected:
        raise ValueError(f"unexpected CIB&RC artifacts: {unexpected}")
    missing = sorted(expected_names - actual_names)
    if missing:
        raise ValueError(f"missing CIB&RC artifacts: {missing}")

    verified = []
    for category in sorted(catalog):
        artifact = catalog[category]
        path = folder / artifact.filename
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(f"invalid PDF signature for {artifact.filename}")
        if sha256_file(path) != artifact.sha256:
            raise ValueError(f"CIB&RC hash mismatch for {artifact.filename}")
        verified.append((artifact, path))
    return verified
