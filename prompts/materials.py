from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC

import json
from typing import Literal

@PROMPT_REGISTRY.register()
class StructureInfoExtractPrompt(PromptABC):
    """
    System prompt for extracting structure information.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """## Role & Objective
You are an expert in materials science.
Your task is to extract structured information related to materials science from the given scientific text.

## Instructions:
For each material mentioned in the text, extract the following fields under the specified categories.
- **Important: Each material record must be unique and non-repetitive.** If the same material appears multiple times in the text with the same structural information, extract it only once. Each row in the output should represent a distinct material.
- If a field is not mentioned in the text, leave it as an empty string "".    
- Keep the JSON format consistent across all outputs with the specified column order.
- Do not add extra fields beyond the specified columns.
- The "note" field is used to record any key information that does not fit into other specific fields but is important for understanding the material structure.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return the output strictly in JSON format, matching the schema exactly (an array).
2. Each item in the array represents one unique material record. **No duplicate records should appear in the output.**
3. If multiple values are mentioned for a field, list them in a single comma-separated string.
4. If a field is not mentioned, leave it as an empty string (""), unless otherwise specified in the schema.
5. Do not include any explanatory text, markdown, or bullet points.
6. Your response must contain only the JSON object — no extra text, symbols, or code blocks (e.g., no ```json or ```).

Now, read the following scientific text and output the extracted information in JSON format. 
Ensure that there are no duplicate records in the output - each material should appear only once.
Do not add any extra symbols, explanations or text.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/material_schemas/structure_info_schema.json"))
        return json_schema
    
@PROMPT_REGISTRY.register()
class ComputationDetailExtractPrompt(PromptABC):
    """
    System prompt for extracting Computation Details.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        material_indexes = kwargs.get("material_indexes", [])
        prompts = [f"""## Role & Objective
You are an expert in materials science and computational chemistry.
You are given a materials structure list: **{material_index}** from the first extraction step to help index and identify the materials in the text.
The materials structure list contains the following key fields for each material:
- composition
- lattice_parameter
- space_group
- number_of_atoms
- note

Your task is to extract DFT computational details for each identified material from the given scientific text.

## Important: Scope of Extraction
**Only extract computational details for materials that are studied in this work/paper. Do NOT extract computational details from cited references or other researchers' work.**
- Focus on DFT calculations that are performed in the current study, investigation, or paper.
- Ignore computational details mentioned only in the context of literature review, comparison with previous studies, or citations.
- If computational details are mentioned both in the current work and in references, only extract them if they are clearly part of the current study.

## Important Note on Multiple Calculations:
**A single material may have multiple DFT calculations with different computational parameters or settings.**
- Each distinct calculation configuration should be treated as a separate output record.
- If a material has multiple calculations (e.g., different cutoff energies, different exchange-correlation functionals, different software, etc.), extract each calculation as an independent data entry.
- **Important: Each calculation record must be unique and non-repetitive.** If the same calculation configuration appears multiple times in the text, extract it only once.

## Instructions:
1. **Use the provided materials structure list to identify and index materials in the text.**
- The materials structure list from the first extraction step will be provided to help you locate the relevant materials.
- Use the key fields (composition, lattice_parameter, space_group, number_of_atoms, note) to match and identify materials in the text.
- Use the composition or chemical formula as the unique identifier to link calculations to materials.

2. **Identify all DFT calculations for each material (only from the current work, not from references).**
- For each material identified in the text, search for all DFT computational details mentioned in the current study.
- Pay attention to different computational settings, parameters, or calculation methods that may indicate separate calculations.
- Each distinct calculation (with different parameters) should be extracted as a separate record.

3. **Extract computational details for each calculation instance.**
- For each calculation instance, extract the following fields.
- If a field is not mentioned in the text, leave it as an empty string "".
- Keep the CSV format consistent across all outputs with the specified column order.
- Do not add extra fields beyond the specified columns.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. **Only extract computational details from the current work/study, not from cited references or other researchers' work.**
2. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
3. Keep entries concise, precise, and complete, avoiding paraphrasing.
4. If the original text includes units (e.g., eV, Å, k-points) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
5. Use the terminology and style consistent with the scientific text.
6. When multiple calculations exist for the same material, distinguish them based on their computational parameters (e.g., different cutoff energies, different functionals, different software versions).

## Formatting Rules
Follow these formatting principles strictly:
1. Return the output strictly in JSON format, matching the schema exactly (an array).
2. Each item in the array represents one unique material record. **No duplicate records should appear in the output.**
3. If multiple values are mentioned for a field, list them in a single comma-separated string.
4. If a field is not mentioned, leave it as an empty string (""), unless otherwise specified in the schema.
5. Do not include any explanatory text, markdown, or bullet points.
6. Your response must contain only the JSON object — no extra text, symbols, or code blocks (e.g., no ```json or ```).

Now, read the following scientific text and output the extracted information in JSON format. 
Ensure that there are no duplicate records in the output - each material should appear only once.
Do not add any extra symbols, explanations or text.
            """ for material_index in material_indexes]
        return prompts
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/material_schemas/computation_detail_schema.json"))
        return json_schema
    
    
@PROMPT_REGISTRY.register()
class PropertyExtractPrompt(PromptABC):
    """
    System prompt for extracting Alloy.
    """

    def __init__(self, mode: Literal['thermal','mechanical','electrical or magnetic']):
        self.mode = mode

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        computation_indexes = kwargs.get("computation_indexes", [])
        prompts = [f"""
      ## Role & Objective
You are an expert in materials science and computational chemistry.
You are given a list of DFT computational instances: **{computation_index}** from the second extraction step.
Each computational instance contains the following index fields:
- composition
- space_group
- number_of_atoms
- K_points
- theoretical_calculation_method

Your task is to extract {self.mode} properties (calculation results) for each computational instance from the given scientific text.

## Important: Scope of Extraction
**Only extract {self.mode} properties for calculations that are performed in this work/paper. Do NOT extract results from cited references or other researchers' work.**
- Focus on {self.mode} properties that are calculated or reported in the current study.
- Ignore {self.mode} properties mentioned only in the context of literature review, comparison with previous studies, or citations.
- If {self.mode} properties are mentioned both in the current work and in references, only extract them if they are clearly part of the current study's calculations.

## Important Note on One-to-One Correspondence:
**Each computational instance from Step 2 corresponds to exactly one result in Step 3.**
- The number of output records must match the number of computational instances provided.
- Each computational instance has one set of {self.mode} properties results.
- Unlike Step 2 where one material could have multiple calculations, Step 3 extracts results for each existing calculation instance.
- The data count remains constant: one calculation instance = one result record.

## Instructions:
1. **Use the provided computational instances list to identify and index calculations in the text.**
- The computational instances list from the second extraction step will be provided to help you locate the relevant calculations.
- Use the index fields (composition, space_group, number_of_atoms, K_points, theoretical_calculation_method) to match and identify each calculation instance in the text.
- Each computational instance should be matched to its corresponding {self.mode} properties results from the current work.

2. **Extract {self.mode} properties for each computational instance (only from the current work).**
- For each computational instance, extract the corresponding {self.mode} properties from the text.
- If {self.mode} properties are not mentioned for a specific calculation instance, still create a record with empty strings for all property fields.
- The order of output records should correspond to the order of computational instances provided.

3. **For each computational instance, extract the following {self.mode} property fields.**
- If a field is not mentioned in the text, leave it as an empty string "".
- Keep the CSV format consistent across all outputs with the specified column order.
- Do not add extra fields beyond the specified columns.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. **Only extract {self.mode} properties from the current work/study, not from cited references or other researchers' work.**
2. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
3. Keep entries concise, precise, and complete, avoiding paraphrasing.
4. If the original text includes units (e.g., eV, J/mol·K, K⁻¹) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
5. Use the terminology and style consistent with the scientific text.
6. Match each result to its corresponding calculation instance using the index fields.

## Formatting Rules
Follow these formatting principles strictly:
1. Return the output strictly in JSON format, matching the schema exactly (an array).
2. Each item in the array represents one unique material record. **No duplicate records should appear in the output.**
3. If multiple values are mentioned for a field, list them in a single comma-separated string.
4. If a field is not mentioned, leave it as an empty string (""), unless otherwise specified in the schema.
5. Do not include any explanatory text, markdown, or bullet points.
6. Your response must contain only the JSON object — no extra text, symbols, or code blocks (e.g., no ```json or ```).

Now, read the following scientific text and output the extracted information in JSON format. 
Ensure that there are no duplicate records in the output - each material should appear only once.
Do not add any extra symbols, explanations or text.
    """ for computation_index in computation_indexes]
       
        return prompts
    
    def build_json_schema(self) -> dict:
        mode = self.mode
        if mode == 'thermal':
            json_schema = json.load(open("./schemas/material_schemas/thermal_property_schema.json"))
        elif mode == 'mechanical':
            json_schema = json.load(open("./schemas/material_schemas/mechanical_property_schema.json"))
        elif mode == 'electrical or magnetic':
            json_schema = json.load(open("./schemas/material_schemas/electrical_magnetic_property_schema.json"))
        else:
            raise ValueError(f"Unsupported mode: {mode}")
                
        return json_schema