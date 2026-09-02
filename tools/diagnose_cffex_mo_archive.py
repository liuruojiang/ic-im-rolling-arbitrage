from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import requests


URLS = (
    "http://www.cffex.com.cn/sj/historysj/202208/zip/202208.zip",
    "https://www.cffex.com.cn/sj/historysj/202208/zip/202208.zip",
)
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


def download() -> tuple[requests.Response, list[dict[str, object]]]:
    errors: list[dict[str, object]] = []
    for url in URLS:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 Codex research diagnostic"},
                timeout=(20, 60),
            )
            response.raise_for_status()
            if not zipfile.is_zipfile(__import__("io").BytesIO(response.content)):
                raise RuntimeError("payload is not ZIP")
            return response, errors
        except Exception as exc:  # diagnostic records every endpoint failure
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
    raise RuntimeError(f"All official CFFEX archive endpoints failed: {errors}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    response, endpoint_errors = download()
    ZIP_PATH.write_bytes(response.content)

    members: list[dict[str, object]] = []
    mo_samples: list[dict[str, object]] = []
    option_like_samples: list[dict[str, object]] = []
    with zipfile.ZipFile(ZIP_PATH) as archive:
        for info in archive.infolist():
            row: dict[str, object] = {"name": info.filename, "bytes": info.file_size}
            if info.is_dir():
                members.append(row)
                continue
            raw = archive.read(info.filename)
            text, encoding = decode(raw)
            row["encoding"] = encoding
            row["first_lines"] = text.splitlines()[:4]
            members.append(row)

            for line_no, line in enumerate(text.splitlines(), start=1):
                compact = line.strip()
                if not compact:
                    continue
                if re.search(r"(?:^|[,\s])MO\d{4}(?:[-\s]?P[-\s]?\d+)?(?:[,\s]|$)", compact, flags=re.I):
                    mo_samples.append({"member": info.filename, "line": line_no, "text": compact[:500]})
                if re.search(r"(?:IO|HO|MO)\d{4}[-\s]?[CP][-\s]?\d+", compact, flags=re.I):
                    option_like_samples.append({"member": info.filename, "line": line_no, "text": compact[:500]})
                if len(mo_samples) >= 30 and len(option_like_samples) >= 30:
                    break

    payload = {
        "requested_urls": list(URLS),
        "resolved_url": response.url,
        "endpoint_errors": endpoint_errors,
        "http_status": response.status_code,
        "download_bytes": len(response.content),
        "member_count": len(members),
        "members": members,
        "mo_samples": mo_samples[:30],
        "option_like_samples": option_like_samples[:30],
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "resolved_url": payload["resolved_url"],
        "download_bytes": payload["download_bytes"],
        "member_count": payload["member_count"],
        "mo_sample_count": len(payload["mo_samples"]),
        "option_like_sample_count": len(payload["option_like_samples"]),
        "output": str(JSON_PATH),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
