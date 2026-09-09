"""Replace one reviewed MSP marketing year from a local official CSV."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import msp  # noqa: E402


DEFAULT_SOURCE = "Government of India Minimum Support Prices"


def official_msp_url(value: str) -> str:
    if not msp.is_official_source_url(value):
        raise argparse.ArgumentTypeError("--source-url must be an HTTPS official .gov.in URL")
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load a reviewed official MSP CSV.")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--year", required=True)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--source-url", required=True, type=official_msp_url)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = msp.replace_csv(str(args.csv), args.year, args.source, args.source_url)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"MSP load failed: {exc}")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
