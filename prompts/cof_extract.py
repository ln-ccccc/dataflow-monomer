from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC

import json

@PROMPT_REGISTRY.register()
class CofExtractPrompt(PromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """Based on the provided scientific article, please extract the information related to the **design and synthesis entities** of the materials. 
        The output must be a single JSON object. For each key listed below, provide the corresponding information found in the paper. 
        If no information is found for a specific key, use `null` as its value.
        Now the article starts here:
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/design_synthesis.json"))         
        return json_schema