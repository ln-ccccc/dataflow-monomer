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
        return _load_text("/share/lcc/prompt/polymer.md")

    def build_json_schema(self) -> dict:
        return json.load(open("/share/lcc/schema/polymer.json"))
