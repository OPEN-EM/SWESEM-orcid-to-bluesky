import os
import time
from datetime import datetime, timedelta, timezone

import requests
from atproto import Client

ORCID_API_BASE = "https://pub.orcid.org/v3.0"


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def fetch_works(orcid_id: str):
    url = f"{ORCID_API_BASE}/{orcid_id}/works"
    headers = {"Accept": "application/vnd.orcid+json"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("group", []) or []


def extract_items_last_ndays(orcid_id: str, groups, days: int = 7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = []

    for g in groups:
        for ws in g.get("work-summary", []) or []:
            lmd = ws.get("last-modified-date", {})
            ts = lmd.get("value")
            if not ts:
                continue
            # ORCID timestamp is milliseconds since epoch
            dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            if dt < cutoff:
                continue

            # title
            title = ""
            title_obj = ws.get("title") or {}
            if isinstance(title_obj, dict):
                tval = title_obj.get("title") or {}
                if isinstance(tval, dict):
                    title = tval.get("value") or ""
                elif isinstance(tval, str):
                    title = tval

            # DOI (if present)
            url = None
            ext_ids = ws.get("external-ids", {}) or {}
            for ext in ext_ids.get("external-id", []) or []:
                if (ext.get("external-id-type") or "").lower() == "doi":
                    val = ext.get("external-id-value")
                    if val:
                        url = f"https://doi.org/{val}"
                        break

            items.append(
                {
                    "orcid": orcid_id,
                    "title": title.strip() or "(no title)",
                    "url": url,
                    "last_modified": dt.isoformat(),
                }
            )

    # Sort newest first
    items.sort(key=lambda x: x["last_modified"], reverse=True)
    return items


def build_post_text(item):
    base = f"New publication from {item['orcid']}:\n{item['title']}"
    if item["url"]:
        return base + f"\n{item['url']}"
    return base


def main():
    handle = get_env("BLUESKY_HANDLE")
    app_pw = get_env("BLUESKY_APP_PASSWORD")
    orcids = [x.strip() for x in get_env("ORCID_IDS").split(",") if x.strip()]

    # connect to Bluesky once
    client = Client()
    client.login(handle, app_pw)

    max_posts_total = int(os.getenv("MAX_POSTS_TOTAL", "10"))
    days_back = int(os.getenv("DAYS_BACK", "7"))

    posted = 0

    for oid in orcids:
        if posted >= max_posts_total:
            break

        print(f"Checking ORCID {oid}")
        groups = fetch_works(oid)
        items = extract_items_last_ndays(oid, groups, days=days_back)

        for item in items:
            if posted >= max_posts_total:
                break
            text = build_post_text(item)
            print("Posting:", text.replace("\n", " | "))
            client.send_post(text)
            posted += 1
            # tiny delay to be gentle
            time.sleep(1)

    print(f"Done, posted {posted} items.")


if __name__ == "__main__":
    main()
