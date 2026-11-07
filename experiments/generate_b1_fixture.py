#!/usr/bin/env python3
"""Generate synthetic input TEXTS for load testing, never expert passports.

The CSV/TXT/JSON generation and validation use Python's standard library.
The auto XLSX engine uses artifact-tool inside Codex and openpyxl in normal
Python/WSL environments. Prebuilt XLSX is included for normal application use.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SEED = 20261107
HEADERS = ["synthetic_id", "Наименование", "Тип данных"]
NOTICE = "СИНТЕТИЧЕСКИЕ ДАННЫЕ ДЛЯ НАГРУЗОЧНОГО ТЕСТА"


def source_texts():
    materials = ["20", "09Г2С", "12Х18Н10Т", "08Х18Н10", "10Х17Н13М2Т"]
    diameters = [32, 38, 45, 57, 76, 89, 108, 114, 133, 159, 168, 219, 273, 325, 530]
    walls = ["2", "2,5", "3", "3,5", "4", "4,5", "5", "6", "8", "10"]
    texts = [f"Труба стальная {d}х{t} мм, материал {m}, длина {length} м"
             for d, t, m, length in itertools.product(diameters, walls, materials, [3, 6, 9, 12])]
    dns = [15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 400]
    pns = [10, 16, 25, 40, 63]
    valve_materials = ["20Л", "25Л", "20ГЛ", "12Х18Н10Т", "09Г2С"]
    texts += [f"Задвижка клиновая фланцевая DN {dn} мм, PN {pn} бар, корпус {m}, {drive}, {climate}"
              for dn, pn, m, drive, climate in itertools.product(
                  dns, pns, valve_materials,
                  ["ручной привод", "механический редуктор", "электропривод", "пневмопривод"], ["У1", "ХЛ1"])]
    texts += [f"Кран шаровой DN {dn} мм, PN {pn} бар, корпус {m}, {connection}, {drive}, {climate}"
              for dn, pn, m, connection, drive, climate in itertools.product(
                  dns, pns, valve_materials, ["фланцевый", "под приварку"],
                  ["ручной привод", "электропривод"], ["У1", "ХЛ1"])]
    texts += [f"Фланец стальной DN {dn} мм, PN {pn} бар, материал {m}, {kind}, {face}"
              for dn, pn, m, kind, face in itertools.product(
                  dns, pns, materials, ["плоский", "воротниковый", "свободный", "резьбовой"],
                  ["исполнение 1", "исполнение 2"])]
    assert len(texts) == 12000 and len(set(texts)) == 12000
    return texts


def sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_openpyxl(output: Path, rows):
    """Portable WSL export using the backend's existing openpyxl dependency."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise SystemExit("openpyxl is required for XLSX generation. Use the backend Python environment (openpyxl is in its requirements), or --data-only.") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SYNTHETIC_12000"
    sheet.sheet_view.showGridLines = False
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:C12001"
    for column, width in {"A": 16, "B": 92, "C": 40}.items():
        sheet.column_dimensions[column].width = width
    for row in sheet:
        sheet.row_dimensions[row[0].row].height = 28 if row[0].row == 1 else 40
        for cell in row:
            cell.number_format = "@"
            cell.font = Font(name="Arial", size=10, color="1F2937")
            cell.alignment = Alignment(vertical="center", wrap_text=cell.column in (2, 3))
    for cell in sheet[1]:
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3A5F")
    table = Table(displayName="SyntheticCatalog", ref="A1:C12001")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    sheet.add_table(table)
    workbook.save(output / "B1_catalog_12000_SYNTHETIC.xlsx")


def extract_xlsx_rows(path: Path):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in root.findall(".//s:sheetData/s:row", ns):
            values = []
            for cell in row.findall("s:c", ns):
                value = cell.find("s:v", ns)
                text = value.text if value is not None else "".join(cell.itertext())
                values.append(strings[int(text)] if cell.get("t") == "s" else text)
            if values:
                rows.append(values)
        assert not root.findall(".//s:f", ns), "fixture must contain raw texts, not formulas"
        return rows


def validate(output: Path, expect_xlsx=True):
    catalog_csv = output / "B1_catalog_12000_SYNTHETIC.csv"
    with catalog_csv.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == HEADERS
    assert len(rows) - 1 == 12000
    assert len({row[0] for row in rows[1:]}) == 12000
    assert len({row[1] for row in rows[1:]}) == 12000
    assert all(row[2] == NOTICE for row in rows[1:])
    reqs = (output / "B1_requirements_10_SYNTHETIC.txt").read_text().splitlines()
    assert len(reqs) == 10 and len(set(reqs)) == 10 and all(reqs)
    assert set(reqs) <= {row[1] for row in rows[1:]}
    requirement_files = sorted((output / "requirements_10").glob("Q*.txt"))
    assert [path.name for path in requirement_files] == [f"Q{i:02}.txt" for i in range(1, 11)]
    assert [path.read_text(encoding="utf-8").splitlines() for path in requirement_files] == [[text] for text in reqs]
    if expect_xlsx:
        actual = extract_xlsx_rows(output / "B1_catalog_12000_SYNTHETIC.xlsx")
        assert actual == rows, "XLSX values differ from generated CSV"
    manifest = json.loads((output / "B1_fixture_manifest.json").read_text())
    for filename, expected in manifest["sha256"].items():
        assert sha256(output / filename) == expected, filename
    assert manifest["generator_sha256"] == sha256(Path(__file__)), "generator hash is stale"
    return {"catalog_rows": 12000, "unique_source_texts": 12000, "requirements": 10,
            "separate_requirement_files": 10,
            "xlsx_verified": expect_xlsx, "hashes_verified": True, "synthetic": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "fixtures")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--data-only", action="store_true", help="write CSV/TXT/JSON without requiring the XLSX runtime")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--refresh-manifest", action="store_true", help="add individual requirement files and refresh hashes, preserving existing catalog CSV/XLSX")
    parser.add_argument("--xlsx-engine", choices=("auto", "artifact", "openpyxl"), default="auto")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.validate_only:
        print(json.dumps(validate(output, not args.data_only), ensure_ascii=False, indent=2))
        return
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "B1_catalog_12000_SYNTHETIC.csv"
    requirements_path = output / "B1_requirements_10_SYNTHETIC.txt"
    if args.refresh_manifest:
        old_manifest = json.loads((output / "B1_fixture_manifest.json").read_text())
        args.seed = old_manifest["seed"]
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            saved = list(csv.reader(f))
        assert saved[0] == HEADERS
        rows = saved[1:]
        texts = [row[1] for row in rows]
        requirements = requirements_path.read_text(encoding="utf-8").splitlines()
    else:
        texts = source_texts()
        # Raw example inputs, not expert labels or validated specifications.
        requirements = [texts[i] for i in (0, 2999, 3000, 5999, 6000, 8999, 9000, 11999, 1500, 7500)]
        random.Random(args.seed).shuffle(texts)
        rows = [[f"SYN-{i:05d}", text, NOTICE] for i, text in enumerate(texts, 1)]
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
            writer.writerows(rows)
        requirements_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    (output / "requirements_10").mkdir(exist_ok=True)
    for number, text in enumerate(requirements, 1):
        (output / "requirements_10" / f"Q{number:02}.txt").write_text(text + "\n", encoding="utf-8")
    payload = {"synthetic": True, "seed": args.seed, "headers": HEADERS, "rows": rows}
    node = os.getenv("CODEX_PRIMARY_RUNTIME_NODE")
    modules = os.getenv("CODEX_PRIMARY_RUNTIME_NODE_MODULES")
    engine = args.xlsx_engine
    if args.refresh_manifest:
        engine = old_manifest.get("xlsx_engine", "artifact")
    elif engine == "auto":
        engine = "artifact" if node and modules and Path(node).is_file() and Path(modules).is_dir() else "openpyxl"
    if not args.data_only and not args.refresh_manifest and engine == "openpyxl":
        export_openpyxl(output, rows)
    if not args.data_only and not args.refresh_manifest and engine == "artifact":
        if not node or not modules:
            raise SystemExit("artifact engine requires the Codex runtime; use --xlsx-engine openpyxl on WSL.")
        # Runtime dependencies resolve in a temporary directory, never by changing
        # the project's dependencies or the shared Codex runtime.
        with tempfile.TemporaryDirectory(prefix="stockexpert_b1_fixture_") as work:
            work = Path(work)
            (work / "node_modules").symlink_to(modules, target_is_directory=True)
            (work / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            builder = work / "b1_fixture_workbook.mjs"
            shutil.copyfile(Path(__file__).with_name("b1_fixture_workbook.mjs"), builder)
            subprocess.run([node, str(builder), str(work / "data.json"), str(output)], cwd=work, check=True)
    filenames = [csv_path.name, requirements_path.name] + [f"requirements_10/Q{i:02}.txt" for i in range(1, 11)]
    if not args.data_only:
        filenames.append("B1_catalog_12000_SYNTHETIC.xlsx")
    manifest = {"synthetic": True, "notice": NOTICE, "seed": args.seed,
                "catalog_rows": len(rows), "requirements": len(requirements),
                "separate_requirement_files": 10,
                "requirements_ui_workflow": "upload all ten Q01.txt..Q10.txt into one source session BEFORE normalization; select that prepared source session in B1",
                "xlsx_engine": None if args.data_only else engine,
                "unique_descriptions": len(set(texts)), "source": "deterministic combinatorial text generator; no production EBDZ copied",
                "source_classes": {"Труба": 3000, "Задвижка": 3000, "Кран шаровой": 3000, "Фланец": 3000},
                "intended_use": "load and UI workflow only; not an expert dataset for LLM/search quality",
                "workbook_sheet": "SYNTHETIC_12000", "header_row": 1,
                "mapping": {"description": "Наименование", "description_column_index_zero_based": 1,
                            "technical_mapping": {"header_row": 1, "name_columns": ["1"], "category_column": None}},
                "generator_sha256": sha256(Path(__file__)),
                "workbook_builder_sha256": sha256(Path(__file__).with_name("b1_fixture_workbook.mjs")),
                "semantic_data_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "sha256": {name: sha256(output / name) for name in filenames},
                "reproducibility": "CSV/TXT and semantic data hash are deterministic. XLSX byte hash records this export; ZIP metadata may vary across runtime exports."}
    (output / "B1_fixture_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validate(output, not args.data_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
