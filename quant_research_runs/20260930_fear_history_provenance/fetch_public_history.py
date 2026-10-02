"""Freeze Baifenwei's currently public retrospective Fear history.

These files are retrieval-time snapshots, not historical publication vintages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
URLS = {
    "fear_greed_full.csv": "https://baifenwei.com/data/fear-greed/fear-greed-full.csv",
    "subscores.json": "https://baifenwei.com/data/fear-greed/subscores.json",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=HERE,
                        help="New directory for an immutable retrieval snapshot")
    output_dir = parser.parse_args().output_dir.resolve()
    outputs = [output_dir / name for name in (*URLS, "fetch_report.json")]
    if any(path.exists() for path in outputs):
        raise FileExistsError(f"Snapshot output exists; choose a new --output-dir: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    retrieved: dict[str, dict] = {}
    raw: dict[str, bytes] = {}
    for name, url in URLS.items():
        response = requests.get(url, timeout=20, headers={"User-Agent": "QuantResearchProvenance/1.0"})
        response.raise_for_status()
        raw[name] = response.content
        retrieved[name] = {
            "url": url,
            "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": response.status_code,
            "sha256": digest(response.content),
            "byte_count": len(response.content),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "date_header": response.headers.get("Date"),
        }

    rows = list(csv.DictReader(io.StringIO(raw["fear_greed_full.csv"].decode("utf-8-sig"))))
    if not rows or set(rows[0]) != {"date", "fear_greed_index"}:
        raise ValueError("Unexpected Fear CSV schema")
    scores = {r["date"]: r["fear_greed_index"] for r in rows}
    if len(scores) != len(rows) or list(scores) != sorted(scores):
        raise ValueError("Fear CSV has duplicate or unsorted dates")
    prior_rows = list(csv.DictReader(PRIOR.open(encoding="utf-8-sig", newline="")))
    prior_scores = {r["date"]: r["fear_greed_index"] for r in prior_rows}
    changed = [date for date, value in prior_scores.items() if scores.get(date) != value]

    subscores = json.loads(raw["subscores.json"])
    dates = subscores["dates"]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise ValueError("Subscore dates duplicate or unsorted")
    for series in subscores["series"]:
        if len(series["data"]) != len(dates):
            raise ValueError(f"Subscore length mismatch: {series['key']}")

    for name, data in raw.items():
        (output_dir / name).write_bytes(data)
    report = {
        "status": "RETROSPECTIVE_SNAPSHOT_ONLY",
        "retrieved": retrieved,
        "full_csv_rows": len(rows),
        "full_csv_first_date": rows[0]["date"],
        "full_csv_last_date": rows[-1]["date"],
        "prior_snapshot_path": str(PRIOR),
        "prior_snapshot_sha256": digest(PRIOR.read_bytes()),
        "prior_rows": len(prior_rows),
        "prior_overlap_changed_dates": changed,
        "new_dates_since_prior": sorted(set(scores) - set(prior_scores)),
        "missing_2026_07_27": "2026-07-27" not in scores,
        "subscore_rows": len(dates),
        "subscore_first_date": dates[0],
        "subscore_last_date": dates[-1],
        "subscore_names": [series["key"] for series in subscores["series"]],
        "subscore_generated_at": subscores.get("generated_at"),
        "point_in_time_vintages_obtained": False,
    }
    (output_dir / "fetch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "full_csv_rows", "full_csv_last_date",
                                                  "prior_overlap_changed_dates", "new_dates_since_prior",
                                                  "missing_2026_07_27", "subscore_rows", "subscore_generated_at")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
