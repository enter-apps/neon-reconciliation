import tempfile
import unittest
from pathlib import Path

import openpyxl

from app.consolidation import load_mapping, transform
from app.consolidation.transform import read_source
from app.reconcile import preview, validate_decisions
from app.server import invalid_cases


class WorkflowTest(unittest.TestCase):
    def test_valid_and_invalid_cnj_and_removal_decision(self):
        mapping = load_mapping()
        headers = []
        for field in mapping.all_fields:
            if headers.count(field.source) < field.occurrence:
                headers.append(field.source)
        rows = []
        for folder, cnj in [(50182, "8017873-86.2026.8.05.0150"), (99999, "CNJ ruim")]:
            row = [None] * len(headers)
            row[headers.index("Pasta")] = folder
            row[headers.index("Numero atual do processo")] = cnj
            rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extraction.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            workbook.save(path)
            frame = transform(path, mapping)
            self.assertEqual(len(frame), 1)
            self.assertEqual(list(frame.attrs["report_keys"]), [50182, 99999])
            self.assertEqual(invalid_cases(read_source(path, mapping), mapping), [{"legal_folder": 99999, "case_number": "CNJ ruim"}])
            cases = frame.to_dict("records")
            existing = [
                {"legal_folder": 50182, "case_number": "8017873-86.2026.8.05.0150", "case_status": "ANTIGO", "deleted_at": None},
                {"legal_folder": 70001, "case_number": "1234567-71.2026.8.05.0150", "deleted_at": None},
                {"legal_folder": 99999, "case_number": "1234567-71.2026.8.05.0150", "deleted_at": None},
            ]
            result = preview(cases, list(frame.attrs["report_keys"]), existing)
            self.assertEqual(len(result["updated"]), 1)
            self.assertEqual([item["legal_folder"] for item in result["missing"]], [70001])
            decisions = validate_decisions(result["missing"], {"70001": {"action": "remove", "deleted_at": "2026-10-30"}})
            self.assertEqual(decisions[70001]["deleted_at"], "2026-10-30")
            self.assertEqual(validate_decisions(result["missing"], {}), {})


if __name__ == "__main__":
    unittest.main()
