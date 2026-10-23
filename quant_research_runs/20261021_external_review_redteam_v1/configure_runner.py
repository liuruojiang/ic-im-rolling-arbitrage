from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
source=(ROOT/"quant_research_runs/20261021_external_review_v1/run_checks.py").read_text(encoding="utf-8")
source=source.replace("HERE = Path(__file__).resolve().parent",
    'HERE = Path(__file__).resolve().parent / os.environ.get("ICIM_AUDIT_STAGE", "cumulative")\nHERE.mkdir(parents=True, exist_ok=True)')
(HERE/"run_checks.py").write_text(source,encoding="utf-8")

