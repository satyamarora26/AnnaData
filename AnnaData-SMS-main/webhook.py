"""
Register (or inspect) the sms:received webhook with the gateway.

Run once after the bridge is publicly reachable:
    python webhook.py            # list, then register if absent
    python webhook.py --list     # list only
    python webhook.py --delete <id>

In cloud mode the target is api.sms-gate.app, so PUBLIC_URL only needs to change
when the bridge itself moves - no more re-registering on every ngrok restart.
"""
import sys

import requests
from requests.auth import HTTPBasicAuth

import config

EVENT = "sms:received"


def auth():
    return HTTPBasicAuth(config.USERNAME, config.PASSWORD)


def list_webhooks():
    r = requests.get(config.webhooks_url(), auth=auth(), timeout=30)
    r.raise_for_status()
    return r.json()


def register(target_url: str):
    r = requests.post(
        config.webhooks_url(),
        json={"url": target_url, "event": EVENT},
        auth=auth(),
        timeout=30,
    )
    print("Status:", r.status_code)
    print("Response:", r.text)
    r.raise_for_status()


def delete(webhook_id: str):
    r = requests.delete(f"{config.webhooks_url()}/{webhook_id}", auth=auth(), timeout=30)
    print("Status:", r.status_code, r.text)


def main():
    problems = [p for p in config.validate() if "AI_ENDPOINT" not in p]
    if problems:
        for p in problems:
            print(f"ERROR: {p}")
        sys.exit(1)

    if not config.PUBLIC_URL:
        print("ERROR: PUBLIC_URL not set (the public https URL of this bridge)")
        sys.exit(1)

    args = sys.argv[1:]

    if args and args[0] == "--delete":
        delete(args[1])
        return

    target = f"{config.public_url()}{config.WEBHOOK_PATH}"
    print(f"Gateway : {config.webhooks_url()}  (mode={config.SMS_MODE})")
    print(f"Target  : {target}")

    try:
        existing = list_webhooks()
    except Exception as e:
        print(f"Could not list webhooks: {e}")
        sys.exit(1)

    print(f"\nExisting webhooks ({len(existing)}):")
    for w in existing:
        print(f"  [{w.get('id')}] {w.get('event')} -> {w.get('url')}")

    if args and args[0] == "--list":
        return

    # Registering the same URL twice produces duplicate deliveries.
    if any(w.get("url") == target and w.get("event") == EVENT for w in existing):
        print("\nAlready registered, nothing to do.")
        return

    print("\nRegistering...")
    register(target)


if __name__ == "__main__":
    main()
