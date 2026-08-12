from pathlib import Path

from .core import import_preview, preview_file

sample = Path(__file__).resolve().parents[1] / "Dataset" / "Customer Traffic 06-06-2026 - 13-07-2026.xlsx"
preview = preview_file(sample.read_bytes(), sample.name)
result = import_preview(preview["token"], sample.name)
print({**result, "sample": str(sample), "rows": preview["total_leads"]})

