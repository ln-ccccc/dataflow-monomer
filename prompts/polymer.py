from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC
import json


def _load_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


@PROMPT_REGISTRY.register()
class PolymerExtractPrompt(PromptABC):
    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        base = _load_text("/uni-curator/user/lcc/lcc/dataflow-dp/prompts/details/polymer.md")
        wl = kwargs.get("monomer_whitelist") or []
        
        # 如果白名单存在且非空，添加受限指令
        if isinstance(wl, list) and wl:
            header = "### Monomer Whitelist & Constraints\n"
            rule = (
                "You must strictly adhere to the following Monomer Whitelist for identifying polymer components:\n"
                "1. **Exact Match**: Output components MUST be exact strings from the whitelist below.\n"
                "2. **'other' Fallback**: If a synthesized monomer is NOT in the whitelist, you MUST represent it as 'other' (if 'other' is in the list). Do NOT output the original name if it's not in the list.\n"
                "3. **Coverage**: Only extract polymers where at least one component is effectively identified (or mapped to 'other').\n"
                "\n**Monomer Whitelist**:\n"
            )
            lines = "\n".join(f"- {str(x)}" for x in wl if str(x).strip())
            return f"{header}{rule}{lines}\n\n{base}"
        
        # 如果没有白名单，必须放宽限制，避免因为找不到 "Monomer Library" 而全部忽略
        else:
            fallback_note = (
                "\n\n### IMPORTANT NOTICE: NO MONOMER LIBRARY PROVIDED\n"
                "Since no specific Monomer Library is provided for this document, please IGNORE the instructions about 'Strict JSON Library Mapping'.\n"
                "Instead, extract ALL synthesized monomers using their full chemical names or abbreviations as they appear in the text.\n"
            )
            return f"{base}{fallback_note}"

    def build_json_schema(self) -> dict:
        return json.load(open("/uni-curator/user/lcc/lcc/dataflow-dp/schemas/polymer.json"))
