"""Safe, local-only structural inventory for legacy tabular exports."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import posixpath
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

SUPPORTED_SUFFIXES = {".csv", ".tsv", ".xlsx"}
DEFAULT_LIMITS = {
    "max_files": 500,
    "max_file_bytes": 50 * 1024 * 1024,
    "max_rows": 1_000_000,
    "max_columns": 10_000,
    "max_sheets": 100,
    "max_zip_members": 1_000,
    "max_zip_uncompressed_bytes": 250 * 1024 * 1024,
}
SENSITIVE_NAME_RE = re.compile(
    r"(?:email|correo|mail|dni|document|telefono|tel|phone|token|secret|password|clave|"
    r"comprobante|receipt|pago|payment|importe|monto|cbu|cvu|cuit|bank|banco|credential)",
    re.IGNORECASE,
)
KEY_NAME_RE = re.compile(r"(?:^|[_\s-])(id|codigo|código|code|clave|key|ref|referencia)(?:$|[_\s-])", re.I)
TYPE_PATTERNS = (
    ("boolean", re.compile(r"^(?:true|false|sí|si|no)$", re.I)),
    ("integer", re.compile(r"^[+-]?\d+$")),
    ("decimal", re.compile(r"^[+-]?(?:\d+[.,]\d+|\d+[eE][+-]?\d+)$")),
    ("date_or_datetime", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[T ]\S+)?$")),
)
MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class InventoryError(ValueError):
    """A safe, stable error code suitable for a CLI report."""


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.()-]+", "_", name, flags=re.ASCII).strip("._")
    return name[:120] or "archivo_sin_nombre"


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_root(path: Path) -> Path:
    if path.is_symlink():
        raise InventoryError("input_symlink_rejected")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise InventoryError("input_not_accessible") from exc
    if not root.is_dir():
        raise InventoryError("input_not_directory")
    return root


def _files(root: Path, limits: dict[str, int]) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InventoryError("symlink_rejected")
        if not path.is_file():
            continue
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise InventoryError("path_traversal_rejected") from exc
        if path.suffix.lower() in SUPPORTED_SUFFIXES or path.suffix.lower() == ".xlsm":
            found.append(path)
    if len(found) > limits["max_files"]:
        raise InventoryError("max_files_exceeded")
    return found


def _encoding_and_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding, marker in (("utf-8-sig", b"\xef\xbb\xbf"), ("utf-16", b"\xff\xfe"), ("utf-16", b"\xfe\xff")):
        if raw.startswith(marker):
            try:
                return encoding, raw.decode(encoding)
            except UnicodeDecodeError as exc:
                raise InventoryError("invalid_encoding") from exc
    try:
        return "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError("invalid_encoding") from exc


def _infer_type(value: str) -> str:
    if not value.strip():
        return "empty"
    for type_name, pattern in TYPE_PATTERNS:
        if pattern.fullmatch(value.strip()):
            return type_name
    return "text"


def _column_name(value: str, index: int) -> str:
    candidate = value.strip()
    if candidate and re.fullmatch(r"[A-Za-z_][A-Za-z0-9 _-]{0,80}", candidate):
        return _safe_name(candidate)
    return f"columna_{index + 1}"


def _tabular_summary(rows: Iterable[list[str]], *, encoding: str, delimiter: str, sheet: str | None = None,
                     max_rows: int, max_columns: int) -> dict:
    iterator = iter(rows)
    try:
        header = next(iterator)
    except StopIteration:
        header = []
    if len(header) > max_columns:
        raise InventoryError("max_columns_exceeded")
    names = [_column_name(value, index) for index, value in enumerate(header)]
    data_rows = 0
    empty: list[int] = [0] * len(names)
    types: list[set[str]] = [set() for _ in names]
    distinct: list[set[str]] = [set() for _ in names]
    formula_count = 0
    row_signatures: Counter[str] = Counter()
    for row_number, row in enumerate(iterator, start=1):
        if row_number > max_rows:
            raise InventoryError("max_rows_exceeded")
        if len(row) > max_columns:
            raise InventoryError("max_columns_exceeded")
        data_rows += 1
        if len(row) > len(names):
            names.extend(f"columna_{index + 1}" for index in range(len(names), len(row)))
            empty.extend([0] * (len(row) - len(empty)))
            types.extend(set() for _ in range(len(row) - len(types)))
            distinct.extend(set() for _ in range(len(row) - len(distinct)))
        padded = row + [""] * (len(names) - len(row))
        row_signatures[hashlib.sha256("\x1f".join(padded).encode("utf-8")).hexdigest()] += 1
        for index, value in enumerate(padded):
            normalized = value.strip()
            if not normalized:
                empty[index] += 1
            else:
                types[index].add(_infer_type(value))
                distinct[index].add(normalized)
                formula_count += int(normalized.startswith("="))
    columns = []
    for index, name in enumerate(names):
        is_key = bool(KEY_NAME_RE.search(name)) and data_rows > 0 and not empty[index] and len(distinct[index]) == data_rows
        columns.append({"name": name, "empty_count": empty[index], "inferred_types": sorted(types[index] or {"empty"}),
                        "distinct_count": len(distinct[index]), "possible_key": bool(is_key),
                        "possibly_sensitive": bool(SENSITIVE_NAME_RE.search(name))})
    return {"sheet": sheet, "encoding": encoding, "delimiter": delimiter, "rows": data_rows,
            "columns": columns, "formula_cells": formula_count,
            "duplicate_rows": sum(count - 1 for count in row_signatures.values() if count > 1)}


def _csv_inventory(path: Path, limits: dict[str, int], delimiter: str) -> dict:
    encoding, text = _encoding_and_text(path)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        summary = _tabular_summary(reader, encoding=encoding, delimiter=delimiter,
                                   max_rows=limits["max_rows"], max_columns=limits["max_columns"])
    except csv.Error as exc:
        raise InventoryError("malformed_csv") from exc
    return {"sheets": [summary]}


def _zip_safe(archive: zipfile.ZipFile, limits: dict[str, int]) -> None:
    infos = archive.infolist()
    if len(infos) > limits["max_zip_members"]:
        raise InventoryError("max_zip_members_exceeded")
    total = 0
    for info in infos:
        normalized = posixpath.normpath(info.filename)
        if info.filename.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise InventoryError("xlsx_path_traversal_rejected")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise InventoryError("xlsx_symlink_rejected")
        if info.file_size > 1024 * 1024 and (info.compress_size == 0 or info.file_size > info.compress_size * 100):
            raise InventoryError("xlsx_compression_ratio_exceeded")
        total += info.file_size
    if total > limits["max_zip_uncompressed_bytes"]:
        raise InventoryError("max_zip_size_exceeded")
    names = {info.filename for info in infos}
    if any(name.lower().endswith(("vbaproject.bin", "vbaprojectsignature.bin")) for name in names):
        raise InventoryError("macros_rejected")
    try:
        content_types = archive.read("[Content_Types].xml")
    except KeyError as exc:
        raise InventoryError("invalid_xlsx") from exc
    if b"macroEnabled" in content_types or b"vbaProject" in content_types:
        raise InventoryError("macros_rejected")
    for name in names:
        if name.lower().endswith(".xml"):
            content = archive.read(name)
            if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
                raise InventoryError("xlsx_unsafe_xml")


def _xlsx_inventory(path: Path, limits: dict[str, int]) -> dict:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InventoryError("invalid_xlsx") from exc
    with archive:
        _zip_safe(archive, limits)
        try:
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (KeyError, ElementTree.ParseError) as exc:
            raise InventoryError("invalid_xlsx_xml") from exc
        targets = {node.get("Id"): node.get("Target", "") for node in relationships.findall(PKG_REL_NS + "Relationship")}
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            try:
                shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(item.itertext()) for item in shared_root.findall(MAIN_NS + "si")]
            except ElementTree.ParseError as exc:
                raise InventoryError("invalid_xlsx_xml") from exc
        sheets = workbook.find(MAIN_NS + "sheets")
        if sheets is None:
            raise InventoryError("xlsx_no_sheets")
        summaries = []
        for sheet in list(sheets):
            if len(summaries) >= limits["max_sheets"]:
                raise InventoryError("max_sheets_exceeded")
            target = targets.get(sheet.get(REL_NS + "id", ""), "")
            target = posixpath.normpath(posixpath.join("xl", target))
            if not target.startswith("xl/") or target not in archive.namelist():
                raise InventoryError("invalid_xlsx_sheet")
            try:
                sheet_root = ElementTree.fromstring(archive.read(target))
            except (KeyError, ElementTree.ParseError) as exc:
                raise InventoryError("invalid_xlsx_xml") from exc
            def row_values_generator():
                for row_number, row_node in enumerate(sheet_root.iter(MAIN_NS + "row"), start=1):
                    if row_number > limits["max_rows"]:
                        raise InventoryError("max_rows_exceeded")
                    row_values: dict[int, str] = {}
                    for cell in row_node.findall(MAIN_NS + "c"):
                        reference = cell.get("r", "A1")
                        letters = re.match(r"([A-Z]+)", reference.upper())
                        index = 0
                        for letter in letters.group(1) if letters else "A":
                            index = index * 26 + ord(letter) - ord("A") + 1
                        index -= 1
                        if index < 0 or index >= limits["max_columns"]:
                            raise InventoryError("max_columns_exceeded")
                        formula = cell.find(MAIN_NS + "f")
                        value_node = cell.find(MAIN_NS + "v")
                        value = ""
                        if formula is None and value_node is not None:
                            value = value_node.text or ""
                            if cell.get("t") == "s":
                                try:
                                    shared_index = int(value)
                                except ValueError as exc:
                                    raise InventoryError("invalid_xlsx_shared_string") from exc
                                if shared_index < 0 or shared_index >= len(shared):
                                    raise InventoryError("invalid_xlsx_shared_string")
                                value = shared[shared_index]
                        elif formula is None and cell.get("t") == "inlineStr":
                            inline = cell.find(MAIN_NS + "is")
                            value = "".join(inline.itertext()) if inline is not None else ""
                        elif formula is not None:
                            value = "=FORMULA_HIDDEN"
                        row_values[index] = value
                    width = max(row_values, default=-1) + 1
                    if width > limits["max_columns"]:
                        raise InventoryError("max_columns_exceeded")
                    yield [row_values.get(index, "") for index in range(width)]
            summaries.append(_tabular_summary(row_values_generator(), encoding="xlsx", delimiter="", sheet=sheet.get("name", "Hoja"),
                                               max_rows=limits["max_rows"], max_columns=limits["max_columns"]))
        return {"sheets": summaries}


def _output_text(payload: dict, output_format: str) -> str:
    if output_format == "json":
        import json
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    lines = ["# Inventario de exports heredados", "", "Reporte estructural; no contiene valores de celdas.", "",
             "| Archivo | Tipo | Bytes | Hojas | Filas | Fórmulas | Duplicados | Estado |",
             "|---|---|---:|---:|---:|---:|---:|---|"]
    for item in payload["files"]:
        lines.append("| {filename} | {file_type} | {bytes} | {sheets} | {rows} | {formula_cells} | {duplicate_rows} | {status} |".format(
            filename=item["filename"].replace("|", "-"), file_type=item["file_type"], bytes=item["bytes"],
            sheets=len(item["sheets"]), rows=item["rows"], formula_cells=item["formula_cells"],
            duplicate_rows=item["duplicate_rows"], status=item["status"],
        ))
    return "\n".join(lines) + "\n"


def _atomic_write(destination: Path, content: str, allow_overwrite: bool) -> None:
    if destination.exists() and not allow_overwrite:
        raise InventoryError("output_exists")
    if destination.is_symlink():
        raise InventoryError("output_symlink_rejected")
    if not destination.parent.is_dir():
        raise InventoryError("output_parent_missing")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent,
                                         prefix=f".{destination.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.exists() and not allow_overwrite:
            raise InventoryError("output_exists")
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def inventory_legacy_exports(input_dir: str | os.PathLike[str], output: str | os.PathLike[str] | None = None,
                             *, output_format: str = "json", markdown_output: str | os.PathLike[str] | None = None,
                             dry_run: bool = False, allow_overwrite: bool = False,
                             limits: dict[str, int] | None = None) -> dict:
    effective = {**DEFAULT_LIMITS, **(limits or {})}
    if any(value <= 0 for value in effective.values()):
        raise InventoryError("invalid_limits")
    root = _safe_root(Path(input_dir))
    files = _files(root, effective)
    used_names: Counter[str] = Counter()
    assigned_names: set[str] = set()
    entries = []
    for path in files:
        suffix = path.suffix.lower()
        filename = _safe_name(path.name)
        used_names[filename] += 1
        base_name = filename
        while filename in assigned_names:
            used_names[base_name] += 1
            filename = f"{Path(base_name).stem}-{used_names[base_name]}{Path(base_name).suffix}"
        assigned_names.add(filename)
        item = {"filename": filename, "file_type": suffix[1:].upper(), "bytes": path.stat().st_size,
                "checksum_sha256": _checksum(path), "status": "ok", "sheets": [], "rows": 0,
                "formula_cells": 0, "duplicate_rows": 0}
        try:
            if item["bytes"] > effective["max_file_bytes"]:
                raise InventoryError("max_file_bytes_exceeded")
            if suffix == ".xlsm":
                raise InventoryError("macros_rejected")
            details = (_xlsx_inventory(path, effective) if suffix == ".xlsx" else
                       _csv_inventory(path, effective, "\t" if suffix == ".tsv" else ","))
            item["sheets"] = details["sheets"]
            item["rows"] = sum(sheet["rows"] for sheet in details["sheets"])
            item["formula_cells"] = sum(sheet["formula_cells"] for sheet in details["sheets"])
            item["duplicate_rows"] = sum(sheet["duplicate_rows"] for sheet in details["sheets"])
        except InventoryError as exc:
            item.update(status="rejected", error=str(exc))
        entries.append(item)
    column_locations = {}
    for item in entries:
        for sheet in item["sheets"]:
            for column in sheet["columns"]:
                normalized = re.sub(r"[^a-z0-9]+", "", column["name"].lower())
                if normalized:
                    column_locations.setdefault(normalized, []).append({"filename": item["filename"], "sheet": sheet["sheet"], "column": column["name"]})
    relations = [locations for locations in column_locations.values() if len(locations) > 1]
    payload = {"version": 1, "input_directory": _safe_name(root.name),
               "status": "ok" if all(item["status"] == "ok" for item in entries) else "rejected",
               "file_count": len(entries), "files": entries, "relations_hypotheses": relations,
               "manifest": [{"filename": item["filename"], "sha256": item["checksum_sha256"], "bytes": item["bytes"]} for item in entries],
               "dry_run": dry_run}
    if not dry_run:
        if output is None:
            raise InventoryError("output_required")
        destination = Path(output).resolve()
        try:
            destination.relative_to(root)
        except ValueError:
            pass
        else:
            raise InventoryError("output_inside_input_rejected")
        _atomic_write(destination, _output_text(payload, output_format), allow_overwrite)
        if markdown_output:
            markdown_destination = Path(markdown_output).resolve()
            if markdown_destination == destination:
                raise InventoryError("output_paths_must_differ")
            try:
                markdown_destination.relative_to(root)
            except ValueError:
                pass
            else:
                raise InventoryError("output_inside_input_rejected")
            _atomic_write(markdown_destination, _output_text(payload, "markdown"), allow_overwrite)
    return payload
