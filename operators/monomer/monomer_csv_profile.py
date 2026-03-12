import os
from typing import Dict, List, Optional

from operators.general.csv_exporter import CsvExportOperator


MONOMER_CSV_COLUMNS = [
    "doi",
    "abbreviation",
    "full_name",
    "smiles",
    "smiles_can",
    "smiles_pubchem",
    "smiles_pubchem_can",
    "smiles_opsin",
    "smiles_opsin_can",
    "smiles_cactus",
    "smiles_cactus_can",
    "smiles_api_can",
    "smiles_final",
    "smiles_valid",
]


def monomer_row_to_csv_data(m: Dict, extracted_doi: str) -> Dict:
    final_doi = extracted_doi if extracted_doi else m.get("doi", "")
    return {
        "doi": final_doi,
        "abbreviation": ";".join(m.get("abbreviation", [])),
        "full_name": ";".join(m.get("full_name", [])),
        "smiles": m.get("smiles"),
        "smiles_can": m.get("smiles_can", ""),
        "smiles_pubchem": m.get("smiles_pubchem", ""),
        "smiles_pubchem_can": m.get("smiles_pubchem_can", ""),
        "smiles_opsin": m.get("smiles_opsin", ""),
        "smiles_opsin_can": m.get("smiles_opsin_can", ""),
        "smiles_cactus": m.get("smiles_cactus", ""),
        "smiles_cactus_can": m.get("smiles_cactus_can", ""),
        "smiles_api_can": m.get("smiles_api_can", ""),
        "smiles_final": m.get("smiles_final", ""),
        "smiles_valid": m.get("smiles_valid", ""),
    }


def monomer_csv_path_resolver(row, output_root: Optional[str]) -> str:
    file_path = getattr(row, "file_path", None)
    extracted_doi = getattr(row, "extracted_doi", "")
    if not file_path or not os.path.exists(file_path):
        return ""
    if output_root:
        subdir_name = extracted_doi if extracted_doi else os.path.basename(os.path.dirname(file_path))
        dir_path = os.path.join(output_root, subdir_name)
    else:
        dir_path = os.path.dirname(file_path)
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, "monomers.csv")


def monomer_row_expander(row) -> List[Dict]:
    monomers_info = getattr(row, "monomers_info", None) or []
    extracted_doi = getattr(row, "extracted_doi", "")
    return [monomer_row_to_csv_data(m, extracted_doi) for m in monomers_info]


def build_monomer_csv_exporter(csv_workers: int, progress_every: int) -> CsvExportOperator:
    return CsvExportOperator(
        columns=MONOMER_CSV_COLUMNS,
        csv_path_resolver=monomer_csv_path_resolver,
        row_expander=monomer_row_expander,
        csv_workers=csv_workers,
        progress_every=progress_every,
        skip_if_has_data=True,
        write_empty_file=True,
    )

