"""Query Sumo Logic for ProjectSight production application errors from the last hour."""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_config(config_file: str, args=None) -> dict:
    """Load configuration from JSON file, with env var and CLI arg overrides."""
    config = {}
    if config_file and Path(config_file).exists():
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

    cli_endpoint = getattr(args, "endpoint", None) if args else None
    cli_access_id = getattr(args, "access_id", None) if args else None
    cli_access_key = getattr(args, "access_key", None) if args else None

    return {
        "endpoint": cli_endpoint or os.environ.get("SUMOLOGIC_ENDPOINT", config.get("endpoint", "")),
        "access_id": cli_access_id or os.environ.get("SUMOLOGIC_ACCESS_ID", config.get("access_id", "")),
        "access_key": cli_access_key or os.environ.get("SUMOLOGIC_ACCESS_KEY", config.get("access_key", "")),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Query Sumo Logic logs")
    parser.add_argument(
        "--config-file", default="config.json",
        help="Path to config JSON file (default: config.json)"
    )
    parser.add_argument(
        "--query", "-q", default=None,
        help="Sumo Logic search query"
    )
    parser.add_argument(
        "--query-file", "-f", default=None,
        help="Path to file containing the Sumo Logic search query"
    )
    parser.add_argument(
        "--hours", type=float, default=1.0,
        help="How many hours back to search (default: 1)"
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max results to return (default: 200)"
    )
    parser.add_argument(
        "--access-id", dest="access_id", default=None,
        help="Sumo Logic access ID (overrides env var and config file)"
    )
    parser.add_argument(
        "--access-key", dest="access_key", default=None,
        help="Sumo Logic access key (overrides env var and config file)"
    )
    parser.add_argument(
        "--endpoint", default=None,
        help="Sumo Logic API endpoint (overrides env var and config file)"
    )
    args = parser.parse_args()
    if not args.query and not args.query_file:
        parser.error("Either --query or --query-file is required")
    if args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as f:
            args.query = f.read().strip()
    return args

def api_call(method, url, auth_header, data=None, params=None):
    """Make a REST API call using only stdlib urllib."""
    if params:
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query_string}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", auth_header)
    req.add_header("Accept", "application/json")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def main():
    args = parse_args()
    config = load_config(args.config_file, args)

    endpoint = config["endpoint"]
    access_id = config["access_id"]
    access_key = config["access_key"]

    if not all([endpoint, access_id, access_key]):
        print("Error: Missing configuration. Provide endpoint, access_id, and access_key")
        print("       via config file (--config-file) or environment variables.")
        sys.exit(1)

    credentials = base64.b64encode(f"{access_id}:{access_key}".encode()).decode()
    auth_header = f"Basic {credentials}"

    now = datetime.now(timezone.utc)
    from_time = (now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%S")
    to_time = now.strftime("%Y-%m-%dT%H:%M:%S")

    print(f"Searching Sumo Logic logs...")
    print(f"Query: {args.query}")
    print(f"Time range: {from_time} to {to_time} (UTC)")
    print("=" * 80)

    # Create search job
    payload = {
        "query": args.query,
        "from": from_time,
        "to": to_time,
        "timeZone": "UTC"
    }

    status_code, resp_data = api_call(
        "POST", f"{endpoint}/api/v1/search/jobs", auth_header, data=payload
    )

    if status_code != 202:
        print(f"Error creating search job: {status_code}")
        print(resp_data)
        return

    job_id = resp_data.get("id")
    print(f"Search job created: {job_id}")

    # Poll for completion
    status_data = {}
    for i in range(60):
        time.sleep(3)
        poll_code, status_data = api_call(
            "GET", f"{endpoint}/api/v1/search/jobs/{job_id}", auth_header
        )
        state = status_data.get("state", "")
        msg_count = status_data.get("messageCount", 0)

        print(f"  Status: {state} | Messages: {msg_count}")

        if state == "DONE GATHERING RESULTS":
            break
        elif state in ("CANCELLED", "FORCE PAUSED"):
            print(f"Job ended with state: {state}")
            return
    else:
        print("Timeout waiting for search results")
        return

    # Get messages or records
    msg_count = status_data.get("messageCount", 0)
    rec_count = status_data.get("recordCount", 0)

    print(f"\nMessages: {msg_count}, Records: {rec_count}")

    # If we have records (aggregation query), get records
    if rec_count > 0:
        limit = min(rec_count, args.limit)
        results_code, results = api_call(
            "GET", f"{endpoint}/api/v1/search/jobs/{job_id}/records",
            auth_header, params={"offset": 0, "limit": limit}
        )

        if results_code != 200:
            print(f"Error getting records: {results_code}")
            print(results)
            return

        records = results.get("records", [])
        fields = results.get("fields", [])

        print(f"\n{'=' * 80}")
        print(f"Found {rec_count} records. Showing first {len(records)}:")
        print(f"{'=' * 80}\n")

        field_names = [f.get("name", "") for f in fields]
        print(" | ".join(field_names))
        print("-" * 80)

        for rec in records:
            rec_map = rec.get("map", {})
            values = [str(rec_map.get(fn, "")) for fn in field_names]
            print(" | ".join(values))
    else:
        # Get raw messages
        limit = min(msg_count, args.limit)
        if limit == 0:
            print("\nNo results found.")
            return

        results_code, results = api_call(
            "GET", f"{endpoint}/api/v1/search/jobs/{job_id}/messages",
            auth_header, params={"offset": 0, "limit": limit}
        )

        if results_code != 200:
            print(f"Error getting results: {results_code}")
            print(results)
            return

        messages = results.get("messages", [])

        print(f"\n{'=' * 80}")
        print(f"Found {msg_count} total messages. Showing first {len(messages)}:")
        print(f"{'=' * 80}\n")

        for i, msg in enumerate(messages[:10], 1):
            msg_map = msg.get("map", {})
            timestamp = msg_map.get("_messagetime", msg_map.get("_receipttime", ""))
            source_cat = msg_map.get("_sourcecategory", "")
            raw = msg_map.get("_raw", "")

            if timestamp:
                try:
                    ts = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
                    timestamp = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
                except (ValueError, TypeError):
                    pass

            print(f"[{i}] {timestamp} | {source_cat}")
            print(f"    {raw[:5000]}")
            print()

    # Delete job
    api_call("DELETE", f"{endpoint}/api/v1/search/jobs/{job_id}", auth_header)
    print(f"\nSearch job {job_id} cleaned up.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
