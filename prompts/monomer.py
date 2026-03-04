from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC
import json


def _load_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()

@PROMPT_REGISTRY.register()
class MonomerNameExtractPrompt(PromptABC):
    """
    Prompt for extracting starting monomer seed (Stage 1).
    """
    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        base_prompt = _load_text("/share/lcc/dataflow-dp/prompts/details/monomer.md")
        stage_hint = """

STAGE 1 REQUIREMENTS:
- Extract only: abbreviation, full_name, and SMILES if explicitly present.
- If SMILES is not explicitly present in the text, set it to an empty string "".
- Set doi to null.
- Set iupac_name to null.
- Set cas_no to an empty list [].
- Output MUST be a JSON array matching the schema. Do not wrap it in an object.
""".strip()
        return f"{base_prompt}\n\n{stage_hint}\n"

    def build_json_schema(self) -> dict:
        return json.load(open("/share/lcc/dataflow-dp/schemas/monomer.json"))
