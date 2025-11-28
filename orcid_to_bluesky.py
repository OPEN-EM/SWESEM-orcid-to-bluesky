import os
import time
import yaml
import requests
from datetime import datetime, timedelta, timezone
from atproto import Client, client_utils  # note client_utils import

ORCID_API_BASE = "https://pub.orcid.org/v3.0"


def load_config():
    print("Loading config.yaml")
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    print("Loaded config:", cfg)
    return cfg


def fetch_orcid_name(orcid_id: str) -> str:
    """
    Fetch the display name for an ORCID id from the public ORCID API.
    Falls back to the ORCID id string if anything goes wrong.
    """
    url = f"{ORCID_API_BASE}/{orcid_id}/person"
    headers = {"Accept": "application/vnd.orcid+json"}
    print(f"Requesting ORCID person record for {orcid_id}: {url}")

    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Failed to fetch name for {orcid_id}: {e}")
        return orcid_id

    name = data.get("name", {}) or {}
    given = (name.get("given-names", {}) or {}).get("value") or ""
    family = (name.get("family-name", {}) or {}).get("value") or ""

    full_name = " ".join(part for part in [given, family] if part).strip()
    if not full_name:
        full_name = orcid_id

    print(f"  Resolved {orcid_id} to name: {full_name}")
    return full_name


def fetch_works(orcid_id):
    url = f"{ORCID_API_BASE}/{orcid_id}/works"
    headers = {"Accept": "application/vnd.orcid+json"}
    print(f"Requesting ORCID works for {orcid_id}: {url}")
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    groups = data.get("group", []) or []
    print(f"  Retrieved {len(groups)} groups for {orcid_id}")
    return groups


def filter_recent(groups, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"  Filtering works with last-modified-date >= {cutoff.isoformat()}")
    results = []

    for g in groups:
        for ws in g.get("work-summary", []) or []:
            ts = ws.get("last-modified-date", {}).get("value")
            if not ts:
                continue

            dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            if dt < cutoff:
                continue

            # Title
            title_obj = ws.get("title", {}) or {}
            tval = title_obj.get("title", {}) or {}
            if isinstance(tval, dict):
                title = tval.get("value", "(no title)")
            elif isinstance(tval, str):
                title = tval
            else:
                title = "(no title)"

            # Try DOI
            url = None
            for ext in ws.get("external-ids", {}).get("external-id", []) or []:
                if (ext.get("external-id-type") or "").lower() == "doi":
                    val = ext.get("external-id-value")
                    if val:
                        url = "https://doi.org/" + val
                        break

            results.append({"title": title, "url": url, "date": dt})

    print(f"  Found {len(results)} works after filtering")
    # newest first
    return sorted(results, key=lambda x: x["date"], reverse=True)


def main():
    cfg = load_config()

    handle = os.getenv("BLUESKY_HANDLE")
    app_pw = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle or not app_pw:
        raise RuntimeError("BLUESKY_HANDLE or BLUESKY_APP_PASSWORD not set")

    hashtags = cfg.get("hashtags", [])
    print("Using hashtags:", hashtags)

    print("Logging in to Bluesky as", handle)
    client = Client()
    client.login(handle, app_pw)

    max_posts = cfg.get("max_posts_total", 5)
    posted = 0

    # Cache ORCID → name within a single run
    name_cache = {}

    for oid in cfg["orcid_ids"]:
        if posted >= max_posts:
            break

        print(f"\n=== Checking {oid} ===")

        # Resolve human name from ORCID
        if oid not in name_cache:
            name_cache[oid] = fetch_orcid_name(oid)
        author_name = name_cache[oid]

        orcid_profile_url = f"https://orcid.org/{oid}"

        groups = fetch_works(oid)
        items = filter_recent(groups, cfg["days_back"])

        if not items:
            print(f"  No recent works for {oid}")
            continue

        for item in items:
            if posted >= max_posts:
                break

            builder = client_utils.TextBuilder()

            # First line: name as a clickable link to ORCID profile
            builder.text("New paper from ")
            builder.link(author_name, orcid_profile_url)

            # Title on next line (plain text)
            builder.text("\n" + item["title"])

            # DOI link on separate line, clickable
            if item["url"]:
                builder.text("\n")
                # visible text = URL, link target = URL
                builder.link(item["url"], item["url"])

            # Hashtags on final line, each as a real tag facet
            if hashtags:
                builder.text("\n")
                for i, tag in enumerate(hashtags):
                    clean_tag = tag.lstrip("#")
                    visible = "#" + clean_tag
                    # include a trailing space except after the last tag
                    if i < len(hashtags) - 1:
                        visible += " "
                    builder.tag(visible, clean_tag)

            text_preview = builder.build_text()
            print("  Posting (preview):", text_preview.replace("\n", " | "))

            # You can pass TextBuilder directly; client will add facets
            client.send_post(builder)

            posted += 1
            time.sleep(1)

    print(f"\nFinished. Posted {posted} items.")


if __name__ == "__main__":
    main()
