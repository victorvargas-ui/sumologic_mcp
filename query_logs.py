"""Query Sumo Logic for ProjectSight production application errors from the last hour."""

import argparse
import asyncio
import json
import os
import sys
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_config(config_file: str) -> dict:
    """Load configuration from JSON file, with env var overrides."""
    config = {}
    if config_file and Path(config_file).exists():
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

    return {
        "endpoint": os.environ.get("SUMOLOGIC_ENDPOINT", config.get("endpoint", "")),
        "access_id": os.environ.get("SUMOLOGIC_ACCESS_ID", config.get("access_id", "")),
        "access_key": os.environ.get("SUMOLOGIC_ACCESS_KEY", config.get("access_key", "")),
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
    args = parser.parse_args()
    if not args.query and not args.query_file:
        parser.error("Either --query or --query-file is required")
    if args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as f:
            args.query = f.read().strip()
    return args

async def main():
    args = parse_args()
    config = load_config(args.config_file)

    endpoint = config["endpoint"]
    access_id = config["access_id"]
    access_key = config["access_key"]

    if not all([endpoint, access_id, access_key]):
        print("Error: Missing configuration. Provide endpoint, access_id, and access_key")
        print("       via config file (--config-file) or environment variables.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    from_time = (now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%S")
    to_time = now.strftime("%Y-%m-%dT%H:%M:%S")
    
    print(f"Searching Sumo Logic logs...")
    print(f"Query: {args.query}")
    print(f"Time range: {from_time} to {to_time} (UTC)")
    print("=" * 80)
    
    async with httpx.AsyncClient(
        auth=(access_id, access_key),
        timeout=60.0
    ) as client:
        # Create search job
        payload = {
            "query": args.query,
            "from": from_time,
            "to": to_time,
            "timeZone": "UTC"
        }
        
        resp = await client.post(
            f"{endpoint}/api/v1/search/jobs",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )
        
        if resp.status_code != 202:
            print(f"Error creating search job: {resp.status_code}")
            print(resp.text)
            return
        
        job_id = resp.json().get("id")
        print(f"Search job created: {job_id}")
        
        # Poll for completion
        for i in range(60):
            await asyncio.sleep(3)
            status_resp = await client.get(
                f"{endpoint}/api/v1/search/jobs/{job_id}",
                headers={"Accept": "application/json"}
            )
            status_data = status_resp.json()
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
            results_resp = await client.get(
                f"{endpoint}/api/v1/search/jobs/{job_id}/records",
                params={"offset": 0, "limit": limit},
                headers={"Accept": "application/json"}
            )
            
            if results_resp.status_code != 200:
                print(f"Error getting records: {results_resp.status_code}")
                print(results_resp.text)
                return
            
            results = results_resp.json()
            records = results.get("records", [])
            fields = results.get("fields", [])
            
            print(f"\n{'=' * 80}")
            print(f"Found {rec_count} records. Showing first {len(records)}:")
            print(f"{'=' * 80}\n")
            
            # Print field headers
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
            
            results_resp = await client.get(
                f"{endpoint}/api/v1/search/jobs/{job_id}/messages",
                params={"offset": 0, "limit": limit},
                headers={"Accept": "application/json"}
            )
        
            if results_resp.status_code != 200:
                print(f"Error getting results: {results_resp.status_code}")
                print(results_resp.text)
                return
            
            results = results_resp.json()
            messages = results.get("messages", [])
            
            print(f"\n{'=' * 80}")
            print(f"Found {msg_count} total messages. Showing first {len(messages)}:")
            print(f"{'=' * 80}\n")
            
            for i, msg in enumerate(messages[:10], 1):
                msg_map = msg.get("map", {})
                timestamp = msg_map.get("_messagetime", msg_map.get("_receipttime", ""))
                source_cat = msg_map.get("_sourcecategory", "")
                raw = msg_map.get("_raw", "")
                
                # Format timestamp
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
        await client.delete(f"{endpoint}/api/v1/search/jobs/{job_id}")
        print(f"\nSearch job {job_id} cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())
