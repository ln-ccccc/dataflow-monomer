import os
from typing import Dict, List

from operators.general.csv_exporter import CsvExportOperator


POLYMER_CSV_COLUMNS = [
    "doi",
    "polymer_name",
    "polymer_type",
    "components",
    "ratio_type",
    "ratio_values_text",
    "feed_ratio_text",
    "diamine_ratio",
    "dianhydride_ratio",
    "diisocyanate_ratio",
    "diol_ratio",
    "diacid_ratio",
    "mn_value",
    "mw_value",
    "pdi_value",
    "mw_unit",
    "test_method",
]


def polymer_row_to_csv_data(m: Dict, doi: str) -> Dict:
    return {
        "doi": doi,
        "polymer_name": m.get("polymer_name"),
        "polymer_type": m.get("polymer_type"),
        "components": ";".join(m.get("components", [])),
        "ratio_type": m.get("ratio_type"),
        "ratio_values_text": m.get("ratio_values_text"),
        "feed_ratio_text": m.get("feed_ratio_text"),
        "diamine_ratio": m.get("diamine_ratio"),
        "dianhydride_ratio": m.get("dianhydride_ratio"),
        "diisocyanate_ratio": m.get("diisocyanate_ratio"),
        "diol_ratio": m.get("diol_ratio"),
        "diacid_ratio": m.get("diacid_ratio"),
        "mn_value": m.get("mn_value"),
        "mw_value": m.get("mw_value"),
        "pdi_value": m.get("pdi_value"),
        "mw_unit": m.get("mw_unit"),
        "test_method": m.get("test_method"),
    }


def polymer_csv_path_resolver(row, _) -> str:
    file_path = getattr(row, "file_path", None)
    if not file_path or not os.path.exists(file_path):
        return ""
    return os.path.join(os.path.dirname(file_path), "polymers.csv")


def polymer_row_expander(row) -> List[Dict]:
    polymers = getattr(row, "polymers", None) or []
    doi = getattr(row, "extracted_doi", "") or getattr(row, "doi_hint", "")
    return [polymer_row_to_csv_data(m, doi) for m in polymers]


def build_polymer_csv_exporter(csv_workers: int, progress_every: int) -> CsvExportOperator:
    return CsvExportOperator(
        columns=POLYMER_CSV_COLUMNS,
        csv_path_resolver=polymer_csv_path_resolver,
        row_expander=polymer_row_expander,
        csv_workers=csv_workers,
        progress_every=progress_every,
        skip_if_has_data=True,
        write_empty_file=True,
    )

