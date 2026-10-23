"""Run the previously blocked, unchanged history entry with repaired calendars."""
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'quant_research_runs/20261022_five_rounds_backtest_impact_v1/run_worker.py'


def main():
    spec = importlib.util.spec_from_file_location('calendar_original_entry_worker', SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    target = RUN / 'original_entry_runs'
    target.mkdir(exist_ok=False)
    module.HERE = target
    sys.argv = [str(SOURCE), 'execution_history', 'after']
    module.main()


if __name__ == '__main__':
    main()
