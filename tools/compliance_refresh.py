"""
tools/compliance_refresh.py
Fetches public labor law references and indexes them into ChromaDB locally.
Run this on a schedule (weekly) rather than at agent runtime to keep
the agent air-gapped during normal operation.

Usage:
    python -m tools.compliance_refresh --state MD
    python -m tools.compliance_refresh --all
"""

import argparse
import datetime
import httpx
from tools.vector_store import VectorStore

# Public Department of Labor and state labor board URLs
# These are the only internet calls the system makes, and only during refresh
""" COMPLIANCE_SOURCES = {
    "federal": [
        {
            "url": "https://www.dol.gov/agencies/whd/flsa",
            "description": "FLSA - Fair Labor Standards Act overview"
        },
        {
            "url": "https://www.dol.gov/agencies/whd/fmla",
            "description": "FMLA - Family and Medical Leave Act"
        },
        {
            #"url": "https://www.dol.gov/agencies/whd/workers/misclassification",
            "url": "https://www.dol.gov/agencies/whd/flsa/misclassification",
            "description": "Worker misclassification - employee vs contractor"
        }
    ],
    "MD": [
        {
            "url": "https://www.dllr.state.md.us/labor/wages/",
            "description": "Maryland wage and hour laws"
        }
    ],
    "VA": [
        {
            "url": "https://www.doli.virginia.gov/labor-law/",
            "description": "Virginia labor law"
        }
    ],
    "DC": [
        {
            "url": "https://does.dc.gov/service/employment-law",
            "description": "DC employment law"
        }
    ]
} """

COMPLIANCE_SOURCES = {
    "federal": [
        {
            "url": "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-V/subchapter-A/part-552",
            "description": "FLSA regulations - eCFR Title 29"
        },
        {
            "url": "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-V/subchapter-B/part-825",
            "description": "FMLA regulations - eCFR Title 29"
        },
        {
            "url": "https://www.irs.gov/businesses/small-businesses-self-employed/independent-contractor-self-employed-or-employee",
            "description": "IRS employee vs contractor classification"
        }
    ],
    "MD": [
        {
            "url": "https://www.dllr.state.md.us/labor/wages/",
            "description": "Maryland wage and hour laws"
        },
        {
            "url": "https://www.dllr.state.md.us/labor/",
            "description": "Maryland Department of Labor overview"
        }
    ],
    "VA": [
        {
            "url": "https://www.doli.virginia.gov/labor-law/",
            "description": "Virginia labor law"
        },
        {
            "url": "https://www.doli.virginia.gov/",
            "description": "Virginia Department of Labor and Industry"
        }
    ],
    "DC": [
        {
            "url": "https://does.dc.gov/page/about-does",
            "description": "DC Department of Employment Services"
        },
        {
            "url": "https://ohr.dc.gov/",
            "description": "DC Office of Human Rights"
        }
    ],
}


def fetch_page_text(url: str) -> str:
    """Fetch a web page and return plain text content."""
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            })
            response.raise_for_status()
            text = response.text
            import re
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:50000]  # Cap at 50k chars to avoid huge pages
    except Exception as e:
        print(f"  Warning: Could not fetch {url}: {e}")
        return ""


def refresh_state(state: str, store: VectorStore) -> dict:
    """Refresh compliance sources for a specific state plus federal sources."""
    results = {}
    today = datetime.date.today().isoformat()

    # Always include federal sources
    sources_to_fetch = COMPLIANCE_SOURCES.get("federal", [])

    # Add state-specific sources if available
    state_sources = COMPLIANCE_SOURCES.get(state.upper(), [])
    if not state_sources:
        print(f"  No specific sources configured for {state} — fetching federal only")
    sources_to_fetch = sources_to_fetch + state_sources

    for source in sources_to_fetch:
        print(f"  Fetching: {source['description']}")
        text = fetch_page_text(source["url"])
        if text:
            count = store.index_compliance_text(
                text=text,
                source_url=source["url"],
                state=state.upper(),
                retrieved_date=today
            )
            results[source["url"]] = count
            print(f"  Indexed {count} chunks from {source['url']}")
        else:
            results[source["url"]] = 0

    return results


def refresh_all(store: VectorStore) -> dict:
    """Refresh compliance sources for all configured states."""
    all_results = {}
    states = [k for k in COMPLIANCE_SOURCES.keys() if k != "federal"]
    for state in states:
        print(f"\nRefreshing {state}...")
        results = refresh_state(state, store)
        all_results[state] = results
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh compliance sources into local vector store")
    parser.add_argument("--state", type=str, help="State code to refresh (e.g. MD, VA, DC)")
    parser.add_argument("--all", action="store_true", help="Refresh all configured states")
    args = parser.parse_args()

    store = VectorStore()

    if args.all:
        print("Refreshing all states...")
        results = refresh_all(store)
        print(f"\nDone. Results: {results}")
    elif args.state:
        print(f"Refreshing {args.state.upper()}...")
        results = refresh_state(args.state.upper(), store)
        print(f"\nDone. Results: {results}")
    else:
        parser.print_help()