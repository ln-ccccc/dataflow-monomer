from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC

import json
from typing import Literal

@PROMPT_REGISTRY.register()
class AlloyNameExtractPrompt(PromptABC):
    """
    System prompt for extracting Alloy.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, materials_name_list) -> str:
        prompt = """You are an expert in high-entropy alloys and materials science.
Your task is to extract structured materials information from the given scientific text and organize it according to the specified schema.

### Instructions:
1. Identify all alloy materials mentioned in the text.
Use the alloy composition or chemical formula (e.g., "CoCrFeNiMn", "Al0.3CoCrFeNi", "IrAl3") as the anchor.
Each alloy should correspond to one independent record in the JSON output.

2. For each alloy, extract the following fields under the specified categories in the JSON schema.  
- If a field is not mentioned in the text, leave it as an empty string `""`.  
- Keep the JSON schema consistent across all outputs.  
- Do not add extra fields beyond the schema. Do not add, rename, or infer any fields.
- Return the output strictly in JSON format, matching the schema exactly.
- Your response must contain only the JSON object — no extra text, symbols, or code blocks (e.g., no ```json or ```).
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/alloy_schemas/basic_schema.json"))
        return json_schema
    
@PROMPT_REGISTRY.register()
class AlloyInfoExtractPrompt(PromptABC):
    """
    System prompt for extracting Alloy.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, materials_name_list) -> str:
        prompt = f"""
      You are an expert in high-entropy alloys and materials science.
      Your task is to extract structured materials information from the given scientific text and organize it according to the specified schema.

      ### Instructions:
      Select alloy materials only from the {materials_name_list} provided.
      Use their compositions or chemical formulas (e.g., “CoCrFeNiMn”, “Al0.3CoCrFeNi”, “IrAl3”) as identifiers, and output each alloy as a separate record in the JSON.

      2. For each alloy, extract the following fields under the specified categories.  
      If a field is not mentioned in the text, leave it as an empty string `""`.  
      Keep the JSON schema consistent across all outputs.  
      Do not add extra fields beyond the schema. Do not add, rename, or infer any fields.
      Return the output strictly in JSON format, matching the schema exactly.
      Your response must contain only the JSON object — no extra text, symbols, or code blocks (e.g., no ```json or ```).
    """
       
        return prompt
    
    def build_json_schema(self, mode: Literal['experimental','DFT','MD']) -> dict:
        if mode == "experimental":
            json_schema = json.load(open("./schemas/alloy_schemas/experimental_schema.json"))
        elif mode == "DFT":
            json_schema = json.load(open("./schemas/alloy_schemas/DFT.json"))
        elif mode == "MD":
            json_schema = json.load(open("./schemas/alloy_schemas/MD.json"))
        else:
            raise ValueError(f"Unknown mode: {mode}")
                
        return json_schema