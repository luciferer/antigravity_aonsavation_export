#!/usr/bin/env python3
"""Send one log record with retry and immediate server ack parsing."""

import argparse
import json
import time
import urllib.error
import urllib.request


def send_once(url, role, content, timeout):
    payload = {"role": role, "content": content}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def main():
    parser = argparse.ArgumentParser(description="Reliable send_log helper")
    parser.add_argument("--url", default="http://localhost:18888/log")
    parser.add_argument("--role", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=2.5)
    parser.add_argument("--retry-delay", type=float, default=0.25)
    args = parser.parse_args()

    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            status, body = send_once(args.url, args.role, args.content, args.timeout)
            if status != 200:
                raise RuntimeError(f"status={status} body={body}")
            parsed = json.loads(body)
            if parsed.get("status") != "success":
                raise RuntimeError(f"server error: {body}")
            print(
                json.dumps(
                    {
                        "ok": True,
                        "attempt": attempt,
                        "message_id": parsed.get("message_id"),
                        "timestamp": parsed.get("timestamp"),
                    },
                    ensure_ascii=False,
                )
            )
            return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt < args.retries:
                time.sleep(args.retry_delay * attempt)

    print(json.dumps({"ok": False, "error": last_error}, ensure_ascii=False))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
