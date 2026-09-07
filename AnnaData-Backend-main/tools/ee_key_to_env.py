"""
Turn a downloaded Earth Engine service account key into an EE_SERVICE_KEY value.

The key arrives as a pretty-printed JSON file, but environment variables are a
single line - and the private key inside contains literal newlines that break a
naive copy-paste. This collapses it safely and checks the fields we rely on.

    python tools/ee_key_to_env.py path/to/key.json
    python tools/ee_key_to_env.py path/to/key.json --write   # append to .env
"""
import json
import sys
from pathlib import Path

REQUIRED = ("type", "client_email", "private_key", "project_id")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv

    if not args:
        print(__doc__)
        return 1

    path = Path(args[0])
    if not path.is_file():
        print(f"No such file: {path}")
        return 1

    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"That file is not valid JSON: {e}")
        return 1

    missing = [f for f in REQUIRED if not key.get(f)]
    if missing:
        print(f"Key is missing required field(s): {', '.join(missing)}")
        print("Download the JSON key for a service account, not an OAuth client.")
        return 1

    if key.get("type") != "service_account":
        print(f"Expected a service account key, got type={key.get('type')!r}")
        return 1

    one_line = json.dumps(key, separators=(",", ":"))

    print(f"service account : {key['client_email']}")
    print(f"cloud project   : {key['project_id']}")
    print(f"length          : {len(one_line)} characters")
    print()

    if write:
        env = Path(".env")
        existing = env.read_text(encoding="utf-8") if env.exists() else ""
        if "EE_SERVICE_KEY=" in existing:
            print("EE_SERVICE_KEY is already in .env - remove it first, or copy below.")
        else:
            with env.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"EE_SERVICE_KEY={one_line}\n")
            print("Appended EE_SERVICE_KEY to .env")
            return 0

    print("Set this as EE_SERVICE_KEY (one line, paste the whole thing):")
    print()
    print(one_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
