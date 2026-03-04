import json
from dataflow.core.prompt import PromptABC


class MarkdownSchemaPrompt(PromptABC):
    def __init__(self, md_path: str, schema_path: str):
        self.md_path = md_path
        self.schema_path = schema_path

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        with open(self.md_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def build_json_schema(self) -> dict:
        with open(self.schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
