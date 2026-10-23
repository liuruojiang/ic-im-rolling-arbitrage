"""Classify the normal delivery and an explicit ledger-only repair."""
from __future__ import annotations

import os


def classify_attempt(event: str, ledger_only: str, mode: str, correction: str, external: str) -> dict[str, str]:
    flags = []
    for value in (ledger_only, correction, external):
        if value not in {"", "true", "false"}:
            raise ValueError("attempt flag must be an explicit boolean")
        flags.append(value == "true")
    recovery, corrected, scheduled = flags
    if recovery and (event != "workflow_dispatch" or mode != "close_confirmed" or corrected or scheduled):
        raise ValueError("ledger-only recovery requires a manual close-confirmed run without correction or external scheduling")
    return {"final_attempt": "true", "ledger_only": str(recovery).lower(), "smtp_enabled": str(not recovery).lower()}


def main() -> None:
    values = classify_attempt(os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("LEDGER_ONLY", ""),
        os.environ.get("REQUESTED_MODE", "close_confirmed"), os.environ.get("CORRECTION", ""), os.environ.get("EXTERNAL_SCHEDULE", ""))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
