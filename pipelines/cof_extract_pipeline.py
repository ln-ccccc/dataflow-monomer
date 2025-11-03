import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from dataflow.operators.core_text import PromptedGenerator
from prompts.cof_extract import CofExtractPrompt

from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage
    
json_schema = {
  "$schema": "https://json-schema.org/draft/2020-12/schema#",
  "title": "Material Synthesis Metadata Schema",
  "description": "A schema describing metadata fields commonly used in material synthesis research.",
  "type": "object",
  "properties": {
    "Linkage": {
      "type": "string",
      "description": "The type of chemical bond connecting the building blocks (e.g., Boroxine, Imine, β-Ketoenamine)."
    },
    "Organic Ligands": {
      "type": "string",
      "description": "The organic molecules that coordinate to metal ions or form the framework's backbone (e.g., Carboxylates, Imidazolates, Phosphonates)."
    },
    "Solvents": {
      "type": "string",
      "description": "The solvents used in the synthesis reaction (e.g., Mesitylene, Dioxane, DMF)."
    },
    "Acids/Bases": {
      "type": "string",
      "description": "Any acids or bases used as catalysts or to control crystallization."
    },
    "Reaction Vessels": {
      "type": "string",
      "description": "The type of container used for the reaction (e.g., Pyrex tube, autoclave)."
    },
    "Reaction Type": {
      "type": "string",
      "description": "The general category of the chemical reaction used for synthesis."
    },
    "Chiral Monomer": {
      "type": "string",
      "description": "Monomers that are inherently chiral and used as building blocks."
    },
    "Chiral Inducer": {
      "type": "string",
      "description": "A substance that induces chirality in the final product without being incorporated into the structure."
    },
    "Chiral Group": {
      "type": "string",
      "description": "A functional group that imparts chirality to a molecule."
    },
    "Chiral Crosslinking Agent": {
      "type": "string",
      "description": "A chiral molecule used to link polymer chains."
    },
    "Polymer Matrix": {
      "type": "string",
      "description": "A polymer material in which the synthesized material is embedded (e.g., PVA, Polyurethane)."
    },
    "Photoactive Monomer": {
      "type": "string",
      "description": "Monomers that are responsive to light (e.g., Porphyrins, Pyrene, Thiophene)."
    },
    "Heteroatomic Monomer": {
      "type": "string",
      "description": "Monomers containing atoms other than carbon and hydrogen (e.g., S, N, Se, O)."
    },
    "Heavy Atom Group": {
      "type": "string",
      "description": "Functional groups containing heavy atoms like Iodine or Bromine."
    },
    "Electron Donor/ Acceptor": {
      "type": "string",
      "description": "Molecules or functional groups that can donate or accept electrons."
    },
    "Structural Control": {
      "type": "string",
      "description": "Methods or strategies used to control the material's structure."
    },
    "Defect Engineering": {
      "type": "string",
      "description": "The intentional creation of defects in the material's structure."
    },
    "Nano Structure": {
      "type": "string",
      "description": "The nanoscale structure or morphology of the material."
    },
    "Hybridization": {
      "type": "string",
      "description": "The process of combining the material with other substances to form a hybrid material."
    },
    "X ray absorption": {
      "type": "string",
      "description": "Properties related to the material's ability to absorb X-rays."
    },
    "Thermally Activated Delayed Fluorescence (TADF)": {
      "type": "string",
      "description": "Information related to the material exhibiting TADF properties."
    }
  },
}


class ExtractCOF():
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name="./data/CofExtractPipeline/cof_contents_short.jsonl",
            cache_path="./cof_output",
            cache_type="jsonl",
        )
        self.model_cache_dir = './dataflow_cache'
        self.llm_serving = APILLMServing_request(
                api_url="http://123.129.219.111:3000/v1/chat/completions",
                key_name_of_api_key="DF_API_KEY",
                model_name="gemini-2.5-pro",
                max_workers=200,
        )
        self.prompt = CofExtractPrompt()
        self.prompt_generator = PromptedGenerator(
            llm_serving = self.llm_serving, 
            system_prompt=self.prompt.build_prompt(),
            json_schema=json_schema,
        )

    def forward(self):
        self.prompt_generator.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = "result"
        )


if __name__ == "__main__":
    # This is the entry point for the pipeline
    model = ExtractCOF()
    model.forward()
