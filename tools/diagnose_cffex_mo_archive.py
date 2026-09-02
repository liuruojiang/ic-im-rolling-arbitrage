from __future__ import annotations

import json
import re
import sys
import zipfile
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


URL = "https://www.cffex.com.cn/sj/historysj/202208/zip/202208.zip"
OUT = Path("artifacts/cffex_mo_archive_diagnostic")
ZIP_PATH = OUT / "202208.zip"
JSON_PATH = OUT / "diagnostic.json"


def decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("gb18030", "gbk", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1"), "latin1"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    response = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 Codex research diagnostic"}, timeout=120)
    response.raise_for_status()
    ZIP_PATH.write_bytes(response.content)
    if not zipfile.is_zipfile(ZIP_PATH):
        raise RuntimeError(f"Downloaded payload is not ZIP: {ZIP_PATH}")

    members: list[dict[str, object]] = []
    mo_samples: list[dict[str, object]] = []
    option_like_samples: list[dict[str, object]] = []
    with zipfile.ZipFile(ZIP_PATH) as archive:
        for info in archive.infolist():
            row: dict[str, object] = {
                "name": info.filename,
                "bytes": info.file_size,
            }
            if info.is_dir():
                members.append(row)
                continue
            raw = archive.read(info.filename)
            text, encoding = decode(raw)
            row["encoding"] = encoding
            row["first_lines"] = text.splitlines()[:4]
            members.append(row)

            lines = text.splitlines()
            for line_no, line in enumerate(lines, start=1):
                compact = line.strip()
                if not compact:
                    continue
                if re.search(r"(?:^|[,\s])MO\d{4}(?:[-\s]?P[-\s]?\d+)?(?:[,\s]|$)", compact, flags=re.I):
                    mo_samples.append({"member": info.filename, "line": line_no, "text": compact[:500]})
                    if len(mo_samples) >= 30:
                        break
                if re.search(r"(?:IO|HO|MO)\d{4}[-\s]?[CP][-\s]?\d+", compact, flags=re.I):
                    option_like_samples.append({"member": info.filename, "line": line_no, "text": compact[:500]})
                    if len(option_like_samples) >= 30:
                        break

    payload = {
        "url": URL,
        "http_status": response.status_code,
        "download_bytes": len(response.content),
        "member_count": len(members),
        "members": members,
        "mo_samples": mo_samples,
        "option_like_samples": option_like_samples,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "download_bytes": payload["download_bytes"],
        "member_count": payload["member_count"],
        "mo_sample_count": len(mo_samples),
        "option_like_sample_count": len(option_like_samples),
        "output": str(JSON_PATH),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
