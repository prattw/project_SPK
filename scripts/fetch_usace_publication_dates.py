#!/usr/bin/env python3
"""Fetch official USACE publication dates from publications.usace.army.mil.

The site blocks most automated HTTP clients. Run the browser snippet below
while logged into a normal browser session on the USACE Publications site,
then pipe the JSON output into this script:

    python scripts/fetch_usace_publication_dates.py --from-stdin < usace.json

Or paste JSON into catalog/usace_publication_dates.json manually.

Browser snippet (DevTools console on publications.usace.army.mil).
Category slugs match app/usace_publication_sites.py:

(async () => {
  const categories = [
    'Army-Regulations-Supplements','CGs-Policy-Notices','Engineer-Circulars',
    'Engineer-Design-Guides','Engineer-Forms','Engineer-Manuals','Engineer-Pamphlets',
    'Engineer-Regulations','Engineer-Technical-Letters','Engineer-Standards-Graphics',
    'Miscellaneous'
  ];
  const parsePage = (html) => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = [];
    doc.querySelectorAll('table tr').forEach(tr => {
      const cells = tr.querySelectorAll('td');
      if (cells.length >= 4) {
        out.push({
          pub_number: cells[0].innerText.trim(),
          title: cells[2].innerText.trim(),
          pub_date: cells[3].innerText.trim(),
          latest_review: (cells[4]?.innerText || '').trim(),
        });
      }
    });
    return out;
  };
  const maxPage = (html) => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    let max = 1;
    doc.querySelectorAll('a').forEach(a => {
      const t = a.textContent.trim();
      if (/^\\d+$/.test(t)) max = Math.max(max, parseInt(t, 10));
    });
    return max;
  };
  const pagePrefix = (html) => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const link = doc.querySelector('a[href*="param_page=2"]');
    if (!link) return null;
    const idx = link.href.indexOf('?');
    return link.href.slice(0, idx + 1) + link.href.slice(idx + 1).replace(/page=2.*/, 'page=');
  };
  const all = [];
  for (const cat of categories) {
    const base = `https://www.publications.usace.army.mil/USACE-Publications/${cat}/`;
    const first = await fetch(base).then(r => r.text());
    const prefix = pagePrefix(first);
    const pages = maxPage(first);
    all.push(...parsePage(first).map(r => ({...r, category: cat})));
    for (let p = 2; p <= pages; p++) {
      const url = prefix ? `${prefix}${p}` : `${base}?udt_43546_param_page=${p}`;
      const html = await fetch(url).then(r => r.text());
      all.push(...parsePage(html).map(r => ({...r, category: cat})));
    }
  }
  copy(JSON.stringify({scraped: new Date().toISOString(), count: all.length, publications: all}, null, 1));
  return all.length;
})();
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "static" / "usace_publication_dates.json"
CATALOG_COPY = ROOT / "catalog" / "usace_publication_dates.json"


def save_payload(payload: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    if out != CATALOG_COPY:
        CATALOG_COPY.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_COPY.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-stdin", action="store_true", help="Read JSON array or {publications: [...]} from stdin.")
    parser.add_argument("--input", type=Path, help="Read JSON from a file.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.from_stdin:
        raw = json.loads(sys.stdin.read())
    elif args.input:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        print(__doc__)
        return 1

    if isinstance(raw, list):
        payload = {
            "scraped": datetime.now(tz=timezone.utc).isoformat(),
            "count": len(raw),
            "publications": raw,
        }
    else:
        payload = raw

    save_payload(payload, args.out)
    print(f"Saved {payload.get('count', len(payload.get('publications', [])))} publications to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
