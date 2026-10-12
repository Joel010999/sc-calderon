"""Inventory local legacy exports without database or network access."""

import json

from django.core.management.base import BaseCommand, CommandError

from core.legacy_exports import InventoryError, inventory_legacy_exports


class Command(BaseCommand):
    help = "Inventaría exports CSV/TSV/XLSX locales sin leer valores en reportes."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--input", dest="input_dir", required=True)
        parser.add_argument("--output", required=False)
        parser.add_argument("--format", choices=("json", "markdown"), default="json")
        parser.add_argument("--markdown-output")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--allow-overwrite", action="store_true")
        parser.add_argument("--max-files", type=int, default=500)
        parser.add_argument("--max-file-bytes", type=int, default=50 * 1024 * 1024)
        parser.add_argument("--max-rows", type=int, default=1_000_000)
        parser.add_argument("--max-columns", type=int, default=10_000)
        parser.add_argument("--max-sheets", type=int, default=100)

    def handle(self, *args, **options):
        if not options["dry_run"] and not options.get("output"):
            raise CommandError("output_required")
        limits = {key: options[key] for key in ("max_files", "max_file_bytes", "max_rows", "max_columns", "max_sheets")}
        try:
            payload = inventory_legacy_exports(
                options["input_dir"], options.get("output"), output_format=options["format"],
                markdown_output=options.get("markdown_output"), dry_run=options["dry_run"],
                allow_overwrite=options["allow_overwrite"], limits=limits,
            )
        except (InventoryError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        if payload["status"] != "ok":
            raise CommandError("inventory_rejected")
