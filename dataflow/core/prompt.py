class PromptABC:
    def build_system_prompt(self) -> str:
        return ""
    def build_prompt(self, **kwargs):
        return ""
    def build_json_schema(self) -> dict:
        return {}

def prompt_restrict(*args, **kwargs):
    def deco(obj):
        return obj
    return deco

