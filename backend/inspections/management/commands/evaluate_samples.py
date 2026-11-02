"""Score the frozen model on the labeled samples and write the report served by GET /api/inspections/metrics/.

Writes only the report file (settings.INSPECTION_METRICS_FILE); it never touches the database.
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backend.inspections.evaluation import evaluate_samples, save_metrics
from inspection_ai import verify_model_integrity
from inspection_ai.evaluation import format_report


class Command(BaseCommand):
    help = "Evaluate the frozen model on the labeled samples (confusion matrix, defect recall) and save the report."

    def add_arguments(self, parser):
        parser.add_argument("--samples-dir", type=Path, default=None,
                            help="folder holding labels.csv and images/ (default: settings.INSPECTION_SAMPLES_DIR)")

    def handle(self, *args, **options):
        samples_dir: Path = options["samples_dir"] or Path(settings.INSPECTION_SAMPLES_DIR)
        if not verify_model_integrity():
            raise CommandError("Model files do not match inspection_ai/model/SHA256SUMS.txt; nothing was evaluated.")
        try:
            result = evaluate_samples(samples_dir, samples_dir / "labels.csv")
        except (OSError, ValueError, csv.Error) as exc:
            raise CommandError(f"Cannot read the labeled samples: {exc}") from exc

        target = save_metrics(result)
        # format_report (inspection_ai) names the path key relative_path.
        self.stdout.write(format_report(result | {"failed": [f | {"relative_path": f["file"]} for f in result["failed"]]}))
        self.stdout.write(f"\nModel version: {result['model_version']}\nReport written to {target}")
        if result["failed"]:
            raise CommandError(f"{len(result['failed'])} of {result['total']} samples have no prediction (listed above).")
