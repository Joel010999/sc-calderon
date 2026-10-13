import json
import tempfile
import zipfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .legacy_exports import inventory_legacy_exports


def _write_xlsx(path: Path) -> None:
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Datos" sheetId="1" r:id="rId1"/><sheet name="Otra" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>codigo</t></is></c><c r="B1" t="inlineStr"><is><t>importe</t></is></c></row><row><c r="A2"><v>1</v></c><c r="B2"><f>1+1</f><v>2</v></c></row><row><c r="A3"><v>1</v></c><c r="B3"><f>otro</f><v>2</v></c></row></sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>estado</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>ok</t></is></c></row></sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


class LegacyExportInventoryTests(SimpleTestCase):
    def test_csv_report_has_structure_only_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "input"
            root.mkdir()
            (root / "ventas.csv").write_text("codigo,email,importe\nA-1,no-reportar@example.invalid,10\nA-1,,11\n", encoding="utf-8")
            output = Path(directory) / "report.json"
            payload = inventory_legacy_exports(root, output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(report["files"][0]["sheets"][0]["rows"], 2)
            self.assertNotIn("no-reportar", output.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "output_exists"):
                inventory_legacy_exports(root, output)

    def test_dry_run_does_not_write_and_malformed_encoding_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.csv").write_bytes(b'"unclosed\n')
            (root / "latin.csv").write_bytes("á\n".encode("latin-1"))
            output = root / "not-created.json"
            payload = inventory_legacy_exports(root, output, dry_run=True)
            self.assertEqual({item["error"] for item in payload["files"]}, {"malformed_csv", "invalid_encoding"})
            self.assertFalse(output.exists())

    def test_xlsx_sheets_formulas_and_duplicates_are_structural(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_xlsx(root / "export.xlsx")
            payload = inventory_legacy_exports(root, dry_run=True)
            item = payload["files"][0]
            self.assertEqual(len(item["sheets"]), 2)
            self.assertEqual(item["formula_cells"], 2)
            self.assertEqual(item["duplicate_rows"], 1)
            self.assertNotIn("1+1", json.dumps(payload))

    def test_rejects_xlsm_symlink_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "macro.xlsm").write_bytes(b"not inspected")
            payload = inventory_legacy_exports(root, dry_run=True)
            self.assertEqual(payload["files"][0]["error"], "macros_rejected")
            try:
                (root / "link.csv").symlink_to(root / "macro.xlsm")
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaisesRegex(ValueError, "symlink_rejected"):
                    inventory_legacy_exports(root, dry_run=True)
                (root / "link.csv").unlink()
            (root / "large.csv").write_text("a\n1\n", encoding="utf-8")
            limited = inventory_legacy_exports(root, dry_run=True, limits={"max_file_bytes": 1})
            self.assertEqual(limited["files"][-1]["error"], "max_file_bytes_exceeded")

    def test_command_contract_and_output_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "exports"
            root.mkdir()
            (root / "data.tsv").write_text("id\tvalor\n1\tprivado\n", encoding="utf-8")
            output = Path(directory) / "result.json"
            call_command("inventory_legacy_exports", "--input", str(root), "--output", str(output), "--format", "json")
            self.assertTrue(output.exists())
            with self.assertRaises(CommandError):
                call_command("inventory_legacy_exports", "--input", str(root), "--output", str(output))

    def test_markdown_is_compact_and_output_cannot_be_inside_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "exports"
            root.mkdir()
            (root / "data.csv").write_text("id\n1\n", encoding="utf-8")
            markdown = Path(directory) / "summary.md"
            inventory_legacy_exports(root, markdown, output_format="markdown")
            content = markdown.read_text(encoding="utf-8")
            self.assertIn("| 1 |", content)
            with self.assertRaisesRegex(ValueError, "output_inside_input_rejected"):
                inventory_legacy_exports(root, root / "inside.json")
