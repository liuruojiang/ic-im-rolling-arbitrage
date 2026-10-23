from __future__ import annotations

import argparse
import io
import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
ARTIFACT_NAME = "ic-im-v1-3-r7-ledger"


class StripCrossOriginAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow artifact redirects without leaking GitHub auth cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_origin = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_origin = urllib.parse.urlsplit(newurl).netloc.lower()
        if old_origin != new_origin:
            for name in ("Authorization", "X-GitHub-Api-Version", "Accept"):
                redirected.remove_header(name)
        return redirected


def api_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ic-im-ledger-restore",
        },
    )


def latest_artifact(
    payload: dict[str, object],
    name: str = ARTIFACT_NAME,
    before_created_at: str = "",
) -> dict[str, object] | None:
    items = payload.get("artifacts", [])
    if not isinstance(items, list):
        return None
    candidates = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is not True
        and item.get("archive_download_url")
        and (not before_created_at or str(item.get("created_at", "")) < before_created_at)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (str(item.get("created_at", "")), int(item.get("id", 0))))


def fetch_latest(
    repository: str,
    token: str,
    api_url: str,
    artifact_name: str = ARTIFACT_NAME,
    before_created_at: str = "",
) -> dict[str, object] | None:
    name = urllib.parse.quote(artifact_name, safe="")
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    candidates = []
    for page in range(1, 101):
        url = f"{base}/artifacts?name={name}&per_page=100&page={page}"
        with urllib.request.urlopen(api_request(url, token), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise RuntimeError("GitHub artifacts response must contain an artifacts list")
        candidates.extend(payload["artifacts"])
        if len(payload["artifacts"]) < 100:
            break
    else:
        raise RuntimeError("GitHub artifact pagination exceeded safety limit")
    while (artifact := latest_artifact(
        {"artifacts": candidates}, artifact_name, before_created_at
    )) is not None:
        candidates.remove(artifact)
        run_id = (artifact.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}", token), timeout=30) as response:
            run = json.loads(response.read().decode("utf-8"))
        if (isinstance(run, dict) and run.get("id") == run_id
                and run.get("status") == "completed" and run.get("conclusion") == "success"
                and run.get("head_branch") == "main"
                and str(run.get("path", "")).split("@", 1)[0] == ".github/workflows/ic-im-v1-3-daily-digest.yml"):
            if artifact["archive_download_url"] != f"{base}/artifacts/{artifact['id']}/zip":
                raise RuntimeError("ledger archive download URL does not match trusted repository")
            return artifact
    return None


def download(artifact: dict[str, object], token: str) -> bytes:
    url = str(artifact["archive_download_url"])
    opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(api_request(url, token), timeout=60) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("ledger artifact exceeds safety limit")
    return data


def safe_members(data: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
    if len(members) > MAX_FILES:
        raise RuntimeError("ledger artifact contains too many files")
    if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("ledger artifact expands beyond safety limit")
    seen = set()
    for item in members:
        path = PurePosixPath(item.filename)
        if str(path).casefold() in seen:
            raise RuntimeError("duplicate ledger artifact member")
        seen.add(str(path).casefold())
        if item.flag_bits & 0x1:
            raise RuntimeError("encrypted ledger artifact is not allowed")
        if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename:
            raise RuntimeError("unsafe ledger artifact path")
        if item.is_dir():
            continue
        allowed = path in {
            PurePosixPath("latest.json"),
            PurePosixPath("migration_record.json"),
        } or (
            len(path.parts) == 2
            and path.parts[0] == "journal"
            and path.suffix == ".json"
        )
        if not allowed:
            raise RuntimeError(f"unexpected ledger artifact member: {item.filename}")
    return members


def extract(data: bytes, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("ledger destination must be empty")
    members = safe_members(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = {item.filename for item in members if not item.is_dir()}
        for required in ("latest.json", "migration_record.json"):
            if required not in names:
                raise RuntimeError(f"v1.3 ledger artifact is missing {required}")
        # Read and CRC-check every entry before changing the destination.
        contents = {item.filename: archive.read(item) for item in members if not item.is_dir()}
        destination.mkdir(parents=True, exist_ok=True)
        for item in members:
            if item.is_dir():
                continue
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[item.filename])
    if not (destination / "latest.json").is_file():
        raise RuntimeError("ledger artifact is missing latest.json")
    if not (destination / "migration_record.json").is_file():
        raise RuntimeError("v1.3 ledger artifact is missing migration_record.json")


def write_output(restored: bool, artifact_id: str = "") -> None:
    rendered = f"restored={str(restored).lower()}\nartifact_id={artifact_id}\n"
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--artifact-name", default=ARTIFACT_NAME)
    parser.add_argument("--before-created-at", default="")
    parser.add_argument("--required", action="store_true")
    args = parser.parse_args()
    if not args.repository or not args.token:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
    artifact = fetch_latest(
        args.repository,
        args.token,
        args.api_url,
        args.artifact_name,
        args.before_created_at,
    )
    if artifact is None:
        write_output(False)
        return 1 if args.required else 0
    extract(download(artifact, args.token), Path(args.state_dir))
    write_output(True, str(artifact.get("id", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
