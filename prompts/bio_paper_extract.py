from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC


@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt(PromptABC):
    """
    System prompt for extracting structured biomedical paper information from parsed markdown content.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        prompt = (
            "You are an expert biomedical information extraction assistant. You will be given the markdown text of a scientific paper. "
            "Your task is to extract key structured information about the paper.\n\n"
            f"Paper ID: {literature_id}\n\n"
            "Instructions:\n"
            "- Read the content carefully and extract important entities and attributes such as: title, authors, journal, year, abstract, keywords, tasks, datasets, methods/models, key results, conclusions, and limitations.\n"
            "- If specific fields are not present, return null for that field.\n"
            "- Be concise; avoid copying excessively long spans.\n"
            "- When applicable, summarize long sections into short, faithful descriptions.\n\n"
            "Output format: Return a JSON object with keys including at least: \n"
            "{\"paper_id\": str, \"title\": str|null, \"authors\": [str]|null, \"journal\": str|null, \"year\": int|null, \"abstract\": str|null, \"keywords\": [str]|null, \"tasks\": [str]|null, \"datasets\": [str]|null, \"methods\": [str]|null, \"results\": str|null, \"conclusions\": str|null, \"limitations\": str|null}.\n"
            "The JSON must be valid and concise.\n\n"
            f"{content}"
        )
        return prompt

@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt5(PromptABC):
    BASE = """## ROLE AND OBJECTIVE
You are an expert biochemist specializing in enzyme reactions. Your task is to extract and structure detailed biochemical reaction information from the provided scientific texts, focusing exclusively on experimentally investigated enzyme-catalyzed reactions in the provided document.

## SCOPE LIMITATIONS
- Extract ONLY reactions with direct experimental investigation/characterization in THIS document
- DO NOT extract reactions merely cited from other literature
- DO NOT extract enzymes mentioned only in metabolic pathway contexts without direct experimental data

## OUTPUT FORMAT
Your response must be valid JSON with this structure:
```json
{
  "literature_id": "Document_Identifier",
  "biochemical_reactions": [
    // Array of reaction objects following the schema below
  ]
}
```

## REACTION SCHEMA
For each experimentally studied reaction, extract these fields:

### Core Reaction Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_id` | Unique identifier within document | Yes | String (e.g., "reaction_1") |
| `reaction_equation` | Balanced chemical equation | Yes | String with standard biochemical notation |
| `substrates` | Direct reactants consumed | Yes | Array of strings |
| `products` | Direct products formed | Yes | Array of strings |

### Enzyme Details
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `enzyme_details` | Information about the primary enzyme | Yes | Object with subfields below |
| → `name` | Primary recommended enzyme name | Yes | String |
| → `synonyms` | Other names used for the enzyme | No | Array of strings |
| → `gene_name` | Gene names encoding the enzyme | No | Array of strings |
| → `organism` | Source organism of the enzyme | Yes | String |
| → `ec_number` | Enzyme Commission number | No | String |
| → `genbank_id` | GenBank accession ID | No | String |
| → `pdb_id` | PDB IDs for enzyme structure | No | Array of strings |
| → `uniprot_id` | UniProt accession ID | No | String |

### Experimental Conditions
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `experimental_conditions` | Conditions of study | Yes | Object with subfields below |
| → `assay_type` | Type of assay conducted | Yes | Object: `{"value": "in vitro" or "in vivo", "details": "optional string"}` |
| → `solvent_buffer` | Buffer system used | Yes | String |
| → `ph` | pH of reaction | Yes | Object: `{"value": number, "details": "optional string"}` |
| → `temperature` | Temperature of reaction | Yes | Object: `{"value": number, "unit": "°C"}` |

### Activity and Performance
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_type_reversible` | Whether reaction is reversible | Yes | "Yes", "No", or "Not specified" |
| `kinetic_parameters` | Kinetic data for wild-type | No | Complex object (see below) |
| `conversion_rate` | Percent conversion for wild-type | No | Object: `{"value": number, "unit": "%"}` |
| `product_yield` | Yield for wild-type | No | Object: `{"value": number, "unit": "string"}` |
| `selectivity` | Selectivity data for wild-type | No | Object (see below) |

### Additional Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `mutants_characterized` | Data on enzyme mutants | No | Array of mutant objects |
| `auxiliary_cofactors_or_proteins` | Essential non-stoichiometric factors | No | Array of strings |
| `optimal_conditions` | Conditions for optimal activity | No | Object with pH and temperature data |
| `inhibition_data` | Data on inhibitors | No | Array of inhibitor objects |
| `expression_system_host` | Host for enzyme expression | No | String |
| `expression_details` | Expression methodology | No | Object with vector and induction details |
| `subcellular_localization_of_enzyme` | Where enzyme functions | No | String |
| `notes` | Additional relevant information | No | String |

## COMPLEX OBJECT STRUCTURES

### Kinetic Parameters
```json
"kinetic_parameters": {
  "km": [
    {
      "substrate_name": "Substrate X",
      "value": 300.0,
      "unit": "µM",
      "error_margin": "± 50.0"
    }
  ],
  "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.05"},
  "vmax": {"value": 20.0, "unit": "µM min⁻¹", "error_margin": "± 5.0"},
  "kcat_km": {"value": 500.0, "unit": "M⁻¹min⁻¹"},
  "specific_activity": {"value": 2.5, "unit": "U/mg", "error_margin": "± 0.3"},
  "other_params": []
}
```

### Selectivity
```json
"selectivity": {
  "regioselectivity": "Description of positional selectivity",
  "stereoselectivity": "Description of stereochemical preference",
  "enantiomeric_excess": {"value": 95, "unit": "%"}
}
```

### Mutant Object
```json
{
  "mutation_description": "Description of the mutation",
  "kinetic_parameters": {}, // Same structure as above
  "activity_qualitative": "Qualitative description if no kinetics",
  "conversion_rate": {"value": 35, "unit": "%"},
  "product_yield": {"value": 250, "unit": "mg/L"},
  "selectivity": {} // Same structure as above
}
```

### Inhibitor Object
```json
{
  "inhibitor_name": "Inhibitor X",
  "ki": {"value": 50, "unit": "nM", "error_margin": "± 10"},
  "ic50": {"value": 200, "unit": "nM", "error_margin": "± 50"},
  "kd": {"value": 30, "unit": "nM", "error_margin": "± 5"},
  "inhibition_type": "Competitive"
}
```

## SPECIAL CASES AND NOTATIONS
1. For **translocases** (EC 7), specify locations: `"Substrate[side 1] + n H+[side 1] → Substrate[side 2] + n H+[side 2]"`
2. For **missing data**: Set value to `null` or omit optional fields entirely
3. For **error margins**: Use format `"± value"` or include only the SD value if that's all that's provided

## EXTRACTION PROCESS
1. Read the entire document thoroughly to identify all experimentally studied reactions
2. For each reaction, systematically extract all required and available optional fields
3. Structure the data according to the JSON schema provided
4. Verify that all extracted reactions have sufficient experimental support in the document
5. Check final JSON structure for completeness and accuracy

## EXAMPLE
```json
{
  "literature_id": "example_paper_id",
  "biochemical_reactions": [
    {
      "reaction_id": "reaction_1",
      "reaction_equation": "Testosterone + NAD(P)H + H+ + O2 → 2β-hydroxytestosterone + NAD(P)+ + H2O",
      "substrates": ["Testosterone", "NAD(P)H", "H+", "O2"],
      "products": ["2β-hydroxytestosterone", "NAD(P)+", "H2O"],
      "enzyme_details": {
        "name": "CYP105D7",
        "synonyms": ["SAV_7469"],
        "gene_name": ["SAV_7469", "hk1"]
        "organism": "Streptomyces avermitilis",
        "ec_number": null,
        "genbank_id": null,
        "pdb_id": ["4UBS"],
        "uniprot_id": "Q825I8"
      },
      "mutants_characterized": [
        {
          "mutation_description": "R70A single mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 390.10, "unit": "µM", "error_margin": "± 235.40"}],
            "kcat": {"value": 0.19, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 494.74, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 31, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (80%) : 16β-OH (P1, 14%) : 2β,16β-diOH (P2, 6%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        },
        {
          "mutation_description": "R70A/R190A double mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 303.10, "unit": "µM", "error_margin": "± 206.6"}],
            "kcat": {"value": 0.18, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 581.99, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 53, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (78%) : 16β-OH (P1, 10%) : 2β,16β-diOH (P2, 12%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        }
      ],
      "auxiliary_cofactors_or_proteins": ["Heme (intrinsic to P450)", "Pdx/Pdr (from Pseudomonas putida)", "RhFRED (from Rhodococcus sp. NCIMB 9784)", "FdxH/FprD (from Streptomyces avermitilis)"],
      "reaction_type_reversible": "No",
      "experimental_conditions": {
        "assay_type": {"value": in vitro, "details": "in vitro enzyme assay (kinetics)"},
        "solvent_buffer": "50 mM NaH2PO4, pH 7.4, 0.1 mM EDTA, 10% glycerol, 0.1 mM DTT",
        "ph": "7.4",
        "temperature": {"value": 30, "unit": "°C"}
      },
      "optimal_conditions": null,
      "kinetic_parameters": {
        "km": [{"substrate_name": "Testosterone", "value": 355.60, "unit": "µM", "error_margin": "± 189.80"}],
        "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.06"},
        "vmax": {"value": 17.33, "unit": "µM min⁻¹", "error_margin": "± 7.213"},
        "kcat_km": {"value": 431.66, "unit": "M⁻¹min⁻¹"}
      },
      "inhibition_data": null,
      "conversion_rate": {"value": 6.21, "unit": "%"},
      "product_yield": null,
      "selectivity": {
         "regioselectivity": "2β-hydroxytestosterone (100% of characterized product)",
         "stereoselectivity": "C-2β hydroxylation determined by NMR",
         "enantiomeric_excess": null
      },
      "expression_system_host": "Escherichia coli BL21 CodonPlus (DE3) RIL",
      "expression_details": {
        "vector_plasmid": "pET28b",
        "induction_conditions": "0.2 mM IPTG, 22°C, 20h"
      },
      "subcellular_localization_of_enzyme": "Cytoplasmic (recombinant expression in E. coli)",
      "notes": "Pdx/Pdr system was found most efficient for in vivo testosterone conversion. Different redox partners tested."
    }
    // ... more reactions if present
  ]
}
```
"""
    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        return f"{self.BASE}\n\nPaper ID: {literature_id}\n\n{content}"


@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt6(PromptABC):
    BASE = """
    ## ROLE AND OBJECTIVE
You are an expert biochemist specializing in enzyme reactions. Your task is to extract and structure detailed biochemical reaction information from the provided scientific texts, focusing exclusively on experimentally investigated enzyme-catalyzed reactions in the provided document.

## SCOPE LIMITATIONS
- Extract ONLY reactions with direct experimental investigation/characterization in THIS document
- DO NOT extract reactions merely cited from other literature
- DO NOT extract enzymes mentioned only in metabolic pathway contexts without direct experimental data

## OUTPUT FORMAT
Your response must be valid JSON with this structure:
```json
{
  "literature_id": "Document_Identifier",
  "biochemical_reactions": [
    // Array of reaction objects following the schema below
  ]
}
```

## REACTION SCHEMA
For each experimentally studied reaction, extract these fields:

### Core Reaction Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_id` | Unique identifier within document | Yes | String (e.g., "reaction_1") |
| `reaction_equation` | Balanced chemical equation | Yes | String with standard biochemical notation |
| `substrates` | Direct reactants consumed | Yes | Array of strings |
| `substrates_smiles` | SMILES notations for substrates | No | Array of objects `{"name": "substrate name", "smiles": "SMILES notation"}` |
| `products` | Direct products formed | Yes | Array of strings |
| `products_smiles` | SMILES notations for products | No | Array of objects `{"name": "product name", "smiles": "SMILES notation"}` |

### Enzyme Details
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `enzyme_details` | Information about the primary enzyme | Yes | Object with subfields below |
| → `name` | Primary recommended enzyme name | Yes | String |
| → `synonyms` | Other names used for the enzyme | No | Array of strings |
| → `gene_name` | Gene names encoding the enzyme | No | Array of strings |
| → `organism` | Source organism of the enzyme | Yes | String |
| → `ec_number` | Enzyme Commission number | No | String |
| → `genbank_id` | GenBank accession ID | No | String |
| → `pdb_id` | PDB IDs for enzyme structure | No | Array of strings |
| → `uniprot_id` | UniProt accession ID | No | String |

### Experimental Conditions
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `experimental_conditions` | Conditions of study | Yes | Object with subfields below |
| → `assay_type` | Type of assay conducted | Yes | Object: `{"value": "in vitro" or "in vivo", "details": "optional string"}` |
| → `solvent_buffer` | Buffer system used | Yes | String |
| → `ph` | pH of reaction | Yes | Object: `{"value": number, "details": "optional string"}` |
| → `temperature` | Temperature of reaction | Yes | Object: `{"value": number, "unit": "°C"}` |

### Activity and Performance
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_type_reversible` | Whether reaction is reversible | Yes | "Yes", "No", or "Not specified" |
| `kinetic_parameters` | Kinetic data for wild-type | No | Complex object (see below) |
| `conversion_rate` | Percent conversion for wild-type | No | Object: `{"value": number, "unit": "%"}` |
| `product_yield` | Yield for wild-type | No | Object: `{"value": number, "unit": "string"}` |
| `selectivity` | Selectivity data for wild-type | No | Object (see below) |

### Additional Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `mutants_characterized` | Data on enzyme mutants | No | Array of mutant objects |
| `auxiliary_cofactors_or_proteins` | Essential non-stoichiometric factors | No | Array of strings |
| `optimal_conditions` | Conditions for optimal activity | No | Object with pH and temperature data |
| `inhibition_data` | Data on inhibitors | No | Array of inhibitor objects |
| `expression_system_host` | Host for enzyme expression | No | String |
| `expression_details` | Expression methodology | No | Object with vector and induction details |
| `subcellular_localization_of_enzyme` | Where enzyme functions | No | String |
| `notes` | Additional relevant information | No | String |

## COMPLEX OBJECT STRUCTURES

### Kinetic Parameters
```json
"kinetic_parameters": {
  "km": [
    {
      "substrate_name": "Substrate X",
      "value": 300.0,
      "unit": "µM",
      "error_margin": "± 50.0"
    }
  ],
  "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.05"},
  "vmax": {"value": 20.0, "unit": "µM min⁻¹", "error_margin": "± 5.0"},
  "kcat_km": {"value": 500.0, "unit": "M⁻¹min⁻¹"},
  "specific_activity": {"value": 2.5, "unit": "U/mg", "error_margin": "± 0.3"},
  "other_params": []
}
```

### Selectivity
```json
"selectivity": {
  "regioselectivity": "Description of positional selectivity",
  "stereoselectivity": "Description of stereochemical preference",
  "enantiomeric_excess": {"value": 95, "unit": "%"}
}
```

### Mutant Object
```json
{
  "mutation_description": "Description of the mutation",
  "kinetic_parameters": {}, // Same structure as above
  "activity_qualitative": "Qualitative description if no kinetics",
  "conversion_rate": {"value": 35, "unit": "%"},
  "product_yield": {"value": 250, "unit": "mg/L"},
  "selectivity": {} // Same structure as above
}
```

### Inhibitor Object
```json
{
  "inhibitor_name": "Inhibitor X",
  "ki": {"value": 50, "unit": "nM", "error_margin": "± 10"},
  "ic50": {"value": 200, "unit": "nM", "error_margin": "± 50"},
  "kd": {"value": 30, "unit": "nM", "error_margin": "± 5"},
  "inhibition_type": "Competitive"
}
```

## SPECIAL CASES AND NOTATIONS
1. For **translocases** (EC 7), specify locations: `"Substrate[side 1] + n H+[side 1] → Substrate[side 2] + n H+[side 2]"`
2. For **missing data**: Set value to `null` or omit optional fields entirely
3. For **error margins**: Use format `"± value"` or include only the SD value if that's all that's provided
4. For **SMILES notations**: Extract if explicitly mentioned in the document; otherwise, leave as null or omit

## EXTRACTION PROCESS
1. Read the entire document thoroughly to identify all experimentally studied reactions
2. For each reaction, systematically extract all required and available optional fields
3. Structure the data according to the JSON schema provided
4. Extract SMILES notations for substrates and products when available in the document
5. Verify that all extracted reactions have sufficient experimental support in the document
6. Check final JSON structure for completeness and accuracy

## EXAMPLE
```json
{
  "literature_id": "example_paper_id",
  "biochemical_reactions": [
    {
      "reaction_id": "reaction_1",
      "reaction_equation": "Testosterone + NAD(P)H + H+ + O2 → 2β-hydroxytestosterone + NAD(P)+ + H2O",
      "substrates": ["Testosterone", "NAD(P)H", "H+", "O2"],
      "substrates_smiles": [
        {"name": "Testosterone", "smiles": "CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C"},
        {"name": "NAD(P)H", "smiles": "NC(=O)C1=CN(C=CC1[N+](=O)[O-])C2C(C(C(O2)COP(=O)(O)OP(=O)(O)OCC3C(C(C(O3)N4C=NC5=C4N=CN=C5N)O)O)O)O"}
      ],
      "products": ["2β-hydroxytestosterone", "NAD(P)+", "H2O"],
      "products_smiles": [
        {"name": "2β-hydroxytestosterone", "smiles": "CC12CCC3C(C1C(CC2O)O)CCC4=CC(=O)CCC34C"},
        {"name": "H2O", "smiles": "O"}
      ],
      "enzyme_details": {
        "name": "CYP105D7",
        "synonyms": ["SAV_7469"],
        "gene_name": ["SAV_7469", "hk1"],
        "organism": "Streptomyces avermitilis",
        "ec_number": null,
        "genbank_id": null,
        "pdb_id": ["4UBS"],
        "uniprot_id": "Q825I8"
      },
      "reaction_type_reversible": "No",
      "experimental_conditions": {
        "assay_type": {"value": "in vitro", "details": "in vitro enzyme assay (kinetics)"},
        "solvent_buffer": "50 mM NaH2PO4, pH 7.4, 0.1 mM EDTA, 10% glycerol, 0.1 mM DTT",
        "ph": {"value": 7.4, "details": null},
        "temperature": {"value": 30, "unit": "°C"}
      },
      "optimal_conditions": null,
      "kinetic_parameters": {
        "km": [{"substrate_name": "Testosterone", "value": 355.60, "unit": "µM", "error_margin": "± 189.80"}],
        "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.06"},
        "vmax": {"value": 17.33, "unit": "µM min⁻¹", "error_margin": "± 7.213"},
        "kcat_km": {"value": 431.66, "unit": "M⁻¹min⁻¹"}
      },
      "conversion_rate": {"value": 6.21, "unit": "%"},
      "product_yield": null,
      "selectivity": {
         "regioselectivity": "2β-hydroxytestosterone (100% of characterized product)",
         "stereoselectivity": "C-2β hydroxylation determined by NMR",
         "enantiomeric_excess": null
      },
      "mutants_characterized": [
        {
          "mutation_description": "R70A single mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 390.10, "unit": "µM", "error_margin": "± 235.40"}],
            "kcat": {"value": 0.19, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 494.74, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 31, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (80%) : 16β-OH (P1, 14%) : 2β,16β-diOH (P2, 6%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        },
        {
          "mutation_description": "R70A/R190A double mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 303.10, "unit": "µM", "error_margin": "± 206.6"}],
            "kcat": {"value": 0.18, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 581.99, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 53, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (78%) : 16β-OH (P1, 10%) : 2β,16β-diOH (P2, 12%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        }
      ],
      "auxiliary_cofactors_or_proteins": ["Heme (intrinsic to P450)", "Pdx/Pdr (from Pseudomonas putida)", "RhFRED (from Rhodococcus sp. NCIMB 9784)", "FdxH/FprD (from Streptomyces avermitilis)"],
      "inhibition_data": null,
      "expression_system_host": "Escherichia coli BL21 CodonPlus (DE3) RIL",
      "expression_details": {
        "vector_plasmid": "pET28b",
        "induction_conditions": "0.2 mM IPTG, 22°C, 20h"
      },
      "subcellular_localization_of_enzyme": "Cytoplasmic (recombinant expression in E. coli)",
      "notes": "Pdx/Pdr system was found most efficient for in vivo testosterone conversion. Different redox partners tested."
    }
    // ... more reactions if present
  ]
}
```
    """
    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        return f"{self.BASE}\n\nPaper ID: {literature_id}\n\n{content}"


@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt7(PromptABC):
    BASE = """
    ## ROLE AND OBJECTIVE
You are an expert biochemist specializing in enzyme reactions. Your task is to extract and structure detailed biochemical reaction information from the provided scientific texts, focusing exclusively on experimentally investigated enzyme-catalyzed reactions in the provided document.

## SCOPE LIMITATIONS
- Extract ONLY reactions with direct experimental investigation/characterization in THIS document
- DO NOT extract reactions merely cited from other literature
- DO NOT extract enzymes mentioned only in metabolic pathway contexts without direct experimental data

## OUTPUT FORMAT
Your response must be a valid JSON with this structure:
```json
{
  "literature_id": "Document_Identifier",
  "biochemical_reactions": [
    // Array of reaction objects following the schema below
  ]
}
```

## REACTION SCHEMA
For each experimentally studied reaction, extract these fields:

### Core Reaction Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_id` | Unique identifier within document | Yes | String (e.g., "reaction_1") |
| `reaction_equation` | Balanced chemical equation | Yes | String with standard biochemical notation |
| `substrates` | Direct reactants consumed | Yes | Array of strings |
| `substrates_smiles` | SMILES notations for substrates | No | Array of objects `{"name": "substrate name", "smiles": "SMILES notation"}` |
| `substrates_sequence` | Single-letter sequence for nucleic acid or peptide substrates | No | Array of objects `{"name": "substrate name", "sequence": "single-letter sequence"}` |
| `products` | Direct products formed | Yes | Array of strings |
| `products_smiles` | SMILES notations for products | No | Array of objects `{"name": "product name", "smiles": "SMILES notation"}` |
| `products_sequence` | Single-letter sequence for nucleic acid or peptide products | No | Array of objects `{"name": "product name", "sequence": "single-letter sequence"}` |


### Enzyme Details
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `enzyme_details` | Information about the primary enzyme | Yes | Object with subfields below |
| → `name` | Primary recommended enzyme name | Yes | String |
| → `synonyms` | Other names used for the enzyme | No | Array of strings |
| → `gene_name` | Gene names encoding the enzyme | No | Array of strings |
| → `organism` | Source organism of the enzyme | Yes | String |
| → `ec_number` | Enzyme Commission number | No | String |
| → `genbank_id` | GenBank accession ID | No | String |
| → `pdb_id` | PDB IDs for enzyme structure | No | Array of strings |
| → `uniprot_id` | UniProt accession ID | No | String |

### Experimental Conditions
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `experimental_conditions` | Conditions of study | Yes | Object with subfields below |
| → `assay_type` | Type of assay conducted | Yes | Object: `{"value": "in vitro" or "in vivo", "details": "optional string"}` |
| → `solvent_buffer` | Buffer system used | Yes | String |
| → `ph` | pH of reaction | Yes | Object: `{"value": number, "details": "optional string"}` |
| → `temperature` | Temperature of reaction | Yes | Object: `{"value": number, "unit": "°C"}` |

### Activity and Performance
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `reaction_type_reversible` | Whether reaction is reversible | Yes | "Yes", "No", or "Not specified" |
| `kinetic_parameters` | Kinetic data for wild-type | No | Complex object (see below) |
| `conversion_rate` | Percent conversion for wild-type | No | Object: `{"value": number, "unit": "%"}` |
| `product_yield` | Yield for wild-type | No | Object: `{"value": number, "unit": "string"}` |
| `selectivity` | Selectivity data for wild-type | No | Object (see below) |

### Additional Data
| Field | Description | Required | Format |
|-------|-------------|----------|--------|
| `mutants_characterized` | Data on enzyme mutants | No | Array of mutant objects |
| `auxiliary_cofactors_or_proteins` | Essential non-stoichiometric factors | No | Array of strings |
| `optimal_conditions` | Conditions for optimal activity | No | Object with pH and temperature data |
| `inhibition_data` | Data on inhibitors | No | Array of inhibitor objects |
| `expression_system_host` | Host for enzyme expression | No | String |
| `expression_details` | Expression methodology | No | Object with vector and induction details |
| `subcellular_localization_of_enzyme` | Where enzyme functions | No | String |
| `notes` | Additional relevant information | No | String |

## COMPLEX OBJECT STRUCTURES

### Kinetic Parameters
```json
"kinetic_parameters": {
  "km": [
    {
      "substrate_name": "Substrate X",
      "value": 300.0,
      "unit": "µM",
      "error_margin": "± 50.0"
    }
  ],
  "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.05"},
  "vmax": {"value": 20.0, "unit": "µM min⁻¹", "error_margin": "± 5.0"},
  "kcat_km": {"value": 500.0, "unit": "M⁻¹min⁻¹"},
  "specific_activity": {"value": 2.5, "unit": "U/mg", "error_margin": "± 0.3"},
  "other_params": []
}
```

### Selectivity
```json
"selectivity": {
  "regioselectivity": "Description of positional selectivity",
  "stereoselectivity": "Description of stereochemical preference",
  "enantiomeric_excess": {"value": 95, "unit": "%"}
}
```
### Sequence Data
```json
"substrates_sequence": [
  {
    "name": "Substrate Peptide A",
    "sequence": "AGVFRT"
  }
],
"products_sequence": [
  {
    "name": "Cleaved Peptide B",
    "sequence": "AGVF"
  }
]
```

### Mutant Object
```json
{
  "mutation_description": "Description of the mutation",
  "kinetic_parameters": {}, // Same structure as above
  "activity_qualitative": "Qualitative description if no kinetics",
  "conversion_rate": {"value": 35, "unit": "%"},
  "product_yield": {"value": 250, "unit": "mg/L"},
  "selectivity": {} // Same structure as above
}
```

### Inhibitor Object
```json
{
  "inhibitor_name": "Inhibitor X",
  "ki": {"value": 50, "unit": "nM", "error_margin": "± 10"},
  "ic50": {"value": 200, "unit": "nM", "error_margin": "± 50"},
  "kd": {"value": 30, "unit": "nM", "error_margin": "± 5"},
  "inhibition_type": "Competitive"
}
```

## SPECIAL CASES AND NOTATIONS
1.  For **translocases** (EC 7), specify locations: `"Substrate[side 1] + n H+[side 1] → Substrate[side 2] + n H+[side 2]"`
2.  If a reaction is described using a general structure with **R-groups** to represent multiple substrates (e.g., 'R-X → R-Y'), create a separate and complete reaction entry for *each* experimentally tested substrate.
3.  For substrates or products that are **nucleic acids or peptides**, provide the single-letter sequence in the `substrates_sequence` or `products_sequence` fields, respectively, if available.
4.  For **missing data**: Set value to `null` or omit optional fields entirely.
5.  For **error margins**: Use format `"± value"` or include only the SD value if that's all that's provided.
6.  For **SMILES notations**: Extract if explicitly mentioned in the document; otherwise, leave as null or omit.

## EXTRACTION PROCESS
1.  Read the entire document thoroughly to identify all experimentally studied reactions.
2.  **Disambiguate Reactions**: If a single reaction description in the text uses a general structure (e.g., with R-groups) to represent multiple tested substrates, treat each specific substrate as a separate reaction. Create a unique reaction object for each one.
3.  For each unique reaction, systematically extract all required and available optional fields.
4.  Structure the data according to the JSON schema provided.
5.  Extract SMILES notations for substrates and products when available in the document.
6.  Extract single-letter sequences for peptide/nucleic acid substrates and products when available in the document.
7.  Verify that all extracted reactions have sufficient experimental support in the document.
8.  Check final JSON structure for completeness and accuracy.

## EXAMPLE
```json
{
  "literature_id": "example_paper_id",
  "biochemical_reactions": [
    {
      "reaction_id": "reaction_1",
      "reaction_equation": "Testosterone + NAD(P)H + H+ + O2 → 2β-hydroxytestosterone + NAD(P)+ + H2O",
      "substrates": ["Testosterone", "NAD(P)H", "H+", "O2"],
      "substrates_smiles": [
        {"name": "Testosterone", "smiles": "CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C"},
        {"name": "NAD(P)H", "smiles": "NC(=O)C1=CN(C=CC1[N+](=O)[O-])C2C(C(C(O2)COP(=O)(O)OP(=O)(O)OCC3C(C(C(O3)N4C=NC5=C4N=CN=C5N)O)O)O)O"}
      ],
      "substrates_sequence": null,
      "products": ["2β-hydroxytestosterone", "NAD(P)+", "H2O"],
      "products_smiles": [
        {"name": "2β-hydroxytestosterone", "smiles": "CC12CCC3C(C1C(CC2O)O)CCC4=CC(=O)CCC34C"},
        {"name": "H2O", "smiles": "O"}
      ],
      "products_sequence": null,
      "enzyme_details": {
        "name": "CYP105D7",
        "synonyms": ["SAV_7469"],
        "gene_name": ["SAV_7469", "hk1"],
        "organism": "Streptomyces avermitilis",
        "ec_number": null,
        "genbank_id": null,
        "pdb_id": ["4UBS"],
        "uniprot_id": "Q825I8"
      },
      "reaction_type_reversible": "No",
      "experimental_conditions": {
        "assay_type": {"value": "in vitro", "details": "in vitro enzyme assay (kinetics)"},
        "solvent_buffer": "50 mM NaH2PO4, pH 7.4, 0.1 mM EDTA, 10% glycerol, 0.1 mM DTT",
        "ph": {"value": 7.4, "details": null},
        "temperature": {"value": 30, "unit": "°C"}
      },
      "optimal_conditions": null,
      "kinetic_parameters": {
        "km": [{"substrate_name": "Testosterone", "value": 355.60, "unit": "µM", "error_margin": "± 189.80"}],
        "kcat": {"value": 0.15, "unit": "min⁻¹", "error_margin": "± 0.06"},
        "vmax": {"value": 17.33, "unit": "µM min⁻¹", "error_margin": "± 7.213"},
        "kcat_km": {"value": 431.66, "unit": "M⁻¹min⁻¹"}
      },
      "conversion_rate": {"value": 6.21, "unit": "%"},
      "product_yield": null,
      "selectivity": {
          "regioselectivity": "2β-hydroxytestosterone (100% of characterized product)",
          "stereoselectivity": "C-2β hydroxylation determined by NMR",
          "enantiomeric_excess": null
      },
      "mutants_characterized": [
        {
          "mutation_description": "R70A single mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 390.10, "unit": "µM", "error_margin": "± 235.40"}],
            "kcat": {"value": 0.19, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 494.74, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 31, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (80%) : 16β-OH (P1, 14%) : 2β,16β-diOH (P2, 6%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        },
        {
          "mutation_description": "R70A/R190A double mutant",
          "kinetic_parameters": {
            "km": [{"substrate_name": "Testosterone", "value": 303.10, "unit": "µM", "error_margin": "± 206.6"}],
            "kcat": {"value": 0.18, "unit": "min⁻¹", "error_margin": "± 0.09"},
            "kcat_km": {"value": 581.99, "unit": "M⁻¹min⁻¹"}
          },
          "activity_qualitative": "Significantly increased conversion",
          "conversion_rate": {"value": 53, "unit": "%"},
          "product_yield": null,
          "selectivity": {
            "regioselectivity": "2β-OH (78%) : 16β-OH (P1, 10%) : 2β,16β-diOH (P2, 12%)",
            "stereoselectivity": "High regio- and stereoselectivity implied",
            "enantiomeric_excess": null
          }
        }
      ],
      "auxiliary_cofactors_or_proteins": ["Heme (intrinsic to P450)", "Pdx/Pdr (from Pseudomonas putida)", "RhFRED (from Rhodococcus sp. NCIMB 9784)", "FdxH/FprD (from Streptomyces avermitilis)"],
      "inhibition_data": null,
      "expression_system_host": "Escherichia coli BL21 CodonPlus (DE3) RIL",
      "expression_details": {
        "vector_plasmid": "pET28b",
        "induction_conditions": "0.2 mM IPTG, 22°C, 20h"
      },
      "subcellular_localization_of_enzyme": "Cytoplasmic (recombinant expression in E. coli)",
      "notes": "Pdx/Pdr system was found most efficient for in vivo testosterone conversion. Different redox partners tested."
    }
    // ... more reactions if present
  ]
}
```
    """
    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        return f"{self.BASE}\n\nPaper ID: {literature_id}\n\n{content}"


@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt8(PromptABC):
    BASE = """
    ## **ROLE AND OBJECTIVE**

You are an expert nanomaterials scientist specializing in **nanozymes** (nanomaterials with enzyme-like characteristics). Your task is to meticulously extract and structure detailed information about nanozymes from the provided scientific texts. You must focus exclusively on nanozymes and their associated catalytic reactions and applications that are **directly and experimentally investigated** within the provided document.

## **SCOPE LIMITATIONS**

  - Extract ONLY nanozymes and their corresponding data that have direct experimental investigation, characterization, or application testing in THIS document.
  - DO NOT extract information about nanozymes or reactions that are merely cited from other literature or mentioned in passing (e.g., in introduction/review sections).
  - DO NOT extract data for conventional/natural enzymes unless they are used as a benchmark for direct comparison with a nanozyme. If so, clearly note this in the `notes` field.

## **OUTPUT FORMAT**

Your response must be a single, valid JSON object with the following top-level structure:

```json
{
  "literature_id": "A_Unique_Document_Identifier",
  "nanozyme_entries": [
    // Array of nanozyme entry objects. Each object represents a unique nanozyme material catalyzing a specific type of reaction.
  ]
}
```

## **NANOZYME ENTRY SCHEMA**

For each unique **combination of a nanozyme material and a specific mimicked enzyme activity**, create one distinct entry object. All applications derived from that specific activity should be listed within that single entry.

### **1. Nanozyme Identification & Characterization**

This section describes the physical and chemical properties of the nanomaterial itself.

| Field | Description | Specific Requirements | Example |
| :--- | :--- | :--- | :--- |
| `entry_id` | Unique identifier within the document. | Required. Format as "nanozyme\_[number]". | `"nanozyme_1"` |
| `nanozyme_details` | A container object for all characterization data. | Required. Must be an object. | `{...}` |
| → `nanozyme_name` | The primary, most descriptive name used in the text. | Required. Capture the full name as given, including modifications. | `"Fe-N single site embedded graphene"` |
| → `synonyms` | Other names or abbreviations for the nanozyme used in the text. | Optional. List all found abbreviations. | `["Fe-N-rGO"]` |
| → `material_class` | High-level classification of the material. | Required. Use a controlled vocabulary: "Metal Oxide", "Noble Metal", "Carbon-based Material", "MOF", "Hybrid Material", "Sulfide", etc. | `"Carbon-based Material"` |
| → `composition` | Detailed chemical and structural composition. | Required. Use the sub-object format. Use `null` for fields that are not applicable. | `{"core": "rGO", "shell": null, "doping": "Fe (1.8 wt%), N (8.3 wt%)", "surface_modification": "Fe-N₄ sites"}` |
| → `synthesis_method` | The main technique used for preparation. | Optional. Briefly describe the method. | `"Heat treatment of GO and Fe precursor in NH₃ atmosphere."` |
| → `morphology` | The observed physical shape or structure. | Optional. | `"Nanosheet"` |
| → `size_parameters`| All dimensional measurements of the nanozyme. | Optional. Must be an array of objects following the **Complex Object Structures** section. | `[{"value": 3.36, "error_margin": 0.23, "unit": "nm", "method": "TEM", "description": "lateral size"}]` |
| → `surface_area` | Specific surface area measurement. | Optional. Follow the specified object format. | `{"value": 284.52, "unit": "m²/g", "method": "BET"}` |
| → `zeta_potential`| Surface charge measurement. | Optional. Must include the pH at which the measurement was taken. | `{"value": -19.7, "unit": "mV", "ph": 7.4}` |
| → `crystallinity` | Crystalline properties of the material. | Optional. Describe phase and structure, citing evidence like XRD. | `{"phase": null, "structure": "Square-pyramidal Fe-N₄ structure", "comment": "Confirmed by XANES and EXAFS"}` |

### **2. Catalytic Activity & Kinetics**

This section describes the specific enzyme-like reaction and its properties.

| Field | Description | Specific Requirements | Example |
| :--- | :--- | :--- | :--- |
| `catalytic_activity`| A container object for all reaction-related data. | Required. Must be an object. | `{...}` |
| → `mimicked_enzyme_type` | The enzyme activity being mimicked. **This is a key identifier for the entry.** | Required. | `"Peroxidase (POD)"` |
| → `reaction_equation` | A representation of the chemical transformation. | Required. Show substrates transforming to products, catalyzed by the nanozyme. | `"2 TMB + H2O2 --[Fe-N-rGO]--> Oxidized TMB + 2 H2O"` |
| → `substrates` | Direct reactants consumed by the nanozyme. | Required. Must be an array of strings. | `["TMB", "H2O2"]` |
| → `substrates_smiles`| SMILES notations for each substrate. | Optional. Extract ONLY if explicitly provided. Use format `[{"name": "...", "smiles": "..."}]`. If not provided, use `null`. | `[{"name": "H2O2", "smiles": "OO"}]` |
| → `products` | Direct products formed from the reaction. | Required. Must be an array of strings. | `["Oxidized TMB", "H2O"]` |
| → `products_smiles` | SMILES notations for each product. | Optional. Extract ONLY if explicitly provided. Use format `[{"name": "...", "smiles": "..."}]`. If not provided, use `null`. | `[{"name": "H2O", "smiles": "O"}]` |
| → `selectivity` | Notes on reaction selectivity or presence of multiple activities. | Optional. | `"Excellent selectivity toward H2O2 without any oxidizing, catalase, or SOD-like activity."` |
| → `experimental_conditions`| Conditions under which activity/kinetics were measured. | Required if kinetic data is present. Must be an object. | `{"assay_type": "in vitro", "solvent_buffer": "Sodium acetate buffer", "ph": {...}, "temperature": {...}}` |
| → `kinetic_parameters`| All measured kinetic data. | Optional. Follow the detailed structure in **Complex Object Structures**. | `{"km": [{"substrate_name": "TMB", "value": 0.165, "unit": "mM"}], ...}` |

### **3. Application Details**

This section lists **all applications** that leverage the specific catalytic activity described above.

| Field | Description | Specific Requirements | Example |
| :--- | :--- | :--- | :--- |
| `application_details`| A list of all demonstrated practical use cases. | Optional. Must be an **array of objects**. If no application is mentioned, this can be an empty array `[]` or `null`. | `[{...}, {...}]` |
| → `application_category`| Broad area of application. | Required within an application object. Controlled vocabulary: "Biosensing", "Antibacterial", "Cancer Therapy", "Environmental Remediation", "Bioimaging", "Neuroprotective Therapy", etc. | `"Biosensing"` |
| → `test_model` | The model used for application testing. | Required within an application object. | `"in vitro (Human blood serum)"` |
| → `target_analyte_or_disease`| The specific molecule, organism, or condition targeted. | Required within an application object. | `"Acetylcholine (ACh)"` |
| → `synergistic_agent`| Any co-administered agent or external stimulus needed for the application. | Optional. | `"Acetylcholine esterase (AChE), Choline oxidase (ChOx)"` |
| → `metrics` | Quantitative performance metrics of the application. | Optional. Must be an array of objects following the **Complex Object Structures** section. **The `value` field must be a number.** | `[{"metric_name": "Detection Limit", "value": 20, "value_range": null, "unit": "nM", "target": "Acetylcholine"}]` |

### **4. Additional Data**

| Field | Description | Specific Requirements | Example |
| :--- | :--- | :--- | :--- |
| `notes` | Any other relevant information, context, or key findings from the text. | Optional. | `"Catalytic efficiency (kcat/Km) is ~700 times higher than undoped rGO."` |

## **COMPLEX OBJECT STRUCTURES**

### **Size Parameters**

An array of objects, where each object represents a distinct size measurement.

```json
"size_parameters": [
  { "value": 240, "error_margin": null, "unit": "nm", "method": "TEM", "description": "average diameter" },
  { "value": 275, "error_margin": null, "unit": "nm", "method": "DLS", "description": "hydrodynamic diameter" }
]
```

### **Kinetic Parameters (Aligned with Enzyme Template)**

An object containing arrays for constants that are substrate-dependent, and single objects for reaction-level constants.

```json
"kinetic_parameters": {
  "km": [ { "substrate_name": "TMB", "value": 0.3022, "unit": "mM", "error_margin": null } ],
  "vmax": [ { "substrate_name": "TMB", "value": 5.62e-9, "unit": "M/s", "error_margin": null } ],
  "kcat": { "value": null, "unit": "s⁻¹", "error_margin": null },
  "kcat_km": { "value": null, "unit": "M⁻¹s⁻¹", "error_margin": null },
  "specific_activity": { "value": 7.5, "unit": "U/mg", "error_margin": null }
}
```

### **Application Metrics**

An array of objects. **The `value` field must be a number.** Use `value_range` for text-based range descriptions. If only a single value is given, `value_range` must be `null`.

```json
"metrics": [
  {
    "metric_name": "Limit of Detection (LOD)",
    "value": 20,
    "value_range": null,
    "unit": "nM",
    "target": "Acetylcholine"
  },
  {
    "metric_name": "Linear Range",
    "value": 50,
    "value_range": "50-1000",
    "unit": "nM",
    "target": "Acetylcholine"
  },
  {
    "metric_name": "Recovery Rate",
    "value": null,
    "value_range": "98.8-108.9",
    "unit": "%",
    "target": "ACh in human blood serum"
  }
]
```

## **SPECIAL CASES AND NOTATIONS**

1.  **Definition of Nanozyme Activity Unit (U):** Unless otherwise specified, one unit (U) is the amount of nanozyme that catalyzes the conversion of **1 µmol of substrate per minute** under specified conditions.
2.  **SMILES Notation:** Extract SMILES strings **only if explicitly written in the source document**. Do not generate them. If not provided, the field must be `null`.
3.  **Missing Data:** For any field where data is not available, set the value to `null` or omit the field if optional.
4.  **Error Margins:** If standard deviation/error_margin is provided, include it in the `error_margin` subfield.

## **EXTRACTION PROCESS**

1.  **Identify Unique Nanozymes:** First, read the document to identify each unique nanozyme material based on its composition and structure (`nanozyme_details`).
2.  **Identify Unique Catalytic Activities:** For that unique nanozyme, identify all the different enzyme-like activities (e.g., POD, CAT, OXD) that were experimentally characterized.
3.  **Create Entries:** Create one `nanozyme_entry` object for **each unique combination of (Nanozyme Material + Mimicked Enzyme Type)**.
4.  **Populate Catalytic Data:** Fill in the `catalytic_activity` object for that specific reaction, including kinetics and conditions.
5.  **List All Related Applications:** Within that same entry, find **all applications** that rely on this specific catalytic activity and list them as objects inside the `application_details` array.
6.  **Verify and Finalize:** Ensure the final output is a single, valid JSON object.

## **EXAMPLE**

This example demonstrates how a single nanozyme (`Fe-N-rGO`) with one type of catalytic activity (`POD`) but multiple applications is structured into a **single entry**, incorporating the refined `metrics` structure.

```json
{
  "literature_id": "Kim_et_al_Adv_Funct_Mater_2020_1905410",
  "nanozyme_entries": [
    {
      "entry_id": "nanozyme_1",
      "nanozyme_details": {
        "nanozyme_name": "Fe-N single site embedded graphene",
        "synonyms": ["Fe-N-rGO"],
        "material_class": "Carbon-based Material",
        "composition": { "core": "rGO", "shell": null, "doping": "Fe (1.8 wt%), N (8.3 wt%)", "surface_modification": "Fe-N₄ sites" },
        "synthesis_method": "Heat treatment of GO and Fe precursor in NH₃ atmosphere.",
        "morphology": "Nanosheet",
        "size_parameters": null, "surface_area": null, "zeta_potential": null,
        "crystallinity": { "phase": null, "structure": "Square-pyramidal Fe-N₄ structure", "comment": "Confirmed by XANES and EXAFS." }
      },
      "catalytic_activity": {
        "mimicked_enzyme_type": "Peroxidase (POD)",
        "reaction_equation": "2 TMB + H2O2 --[Fe-N-rGO]--> Oxidized TMB + 2 H2O",
        "substrates": ["TMB", "H2O2"],
        "substrates_smiles": [
        {"name": "H2O2", "smiles": "OO"},
      ],
        "products": ["Oxidized TMB", "H2O"],
        "products_smiles": [
        {"name": "H2O", "smiles": "O"},
      ],
        "selectivity": "Excellent selectivity toward H2O2 without any oxidizing, catalase, or SOD-like activity.",
        "experimental_conditions": {
          "assay_type": "in vitro", "solvent_buffer": "Sodium acetate buffer",
          "ph": { "value": 4.0, "details": "Optimum pH" },
          "temperature": { "value": 25, "unit": "°C" }
        },
        "kinetic_parameters": {
          "km": [
            {"substrate_name": "TMB", "value": 0.165, "unit": "mM", "error_margin": null},
            {"substrate_name": "H2O2", "value": 0.183, "unit": "mM", "error_margin": null}
          ],
          "vmax": null, "kcat": null, "kcat_km": null, "specific_activity": null
        }
      },
      "application_details": [
        {
          "application_category": "Biosensing",
          "test_model": "in vitro (Human blood serum)",
          "target_analyte_or_disease": "Acetylcholine (ACh)",
          "synergistic_agent": "Acetylcholine esterase (AChE), Choline oxidase (ChOx)",
          "metrics": [
            { "metric_name": "Limit of Detection (LOD)", "value": 20, "value_range": null, "unit": "nM", "target": "Acetylcholine" },
            { "metric_name": "Linear Range", "value": null, "value_range": "50-1000", "unit": "nM", "target": "Acetylcholine" }
          ]
        },
        {
          "application_category": "Bioimaging / Detection",
          "test_model": "cellular (SH-SY5Y, HCT116, H4 cells)",
          "target_analyte_or_disease": "H2O2 released from cancerous cells",
          "synergistic_agent": "fMLP (stimulant)",
          "metrics": [
            { "metric_name": "Detected Concentration", "value": 1.61e-13, "value_range": null, "unit": "M/cell", "target": "H2O2" }
          ]
        }
      ],
      "notes": "The catalytic efficiency (kcat/Km) for TMB and H2O2 was calculated to be 6.79e4 M⁻¹s⁻¹ and 7.6e4 M⁻¹s⁻¹ respectively based on provided data."
    }
  ]
}
```
    """
    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        return f"{self.BASE}\n\nPaper ID: {literature_id}\n\n{content}"


@PROMPT_REGISTRY.register()
class BioPaperInfoExtractPrompt10(PromptABC):
    BASE = """
    ## ROLE AND OBJECTIVE
You are an expert molecular biologist specializing in nucleic acid aptamer development. Your task is to extract and structure detailed experimental information about aptamer screening processes from the provided scientific texts. Focus exclusively on the specific SELEX (Systematic Evolution of Ligands by Exponential Enrichment) or related screening experiments that are directly performed and detailed within the provided document.

## SCOPE LIMITATIONS
- Extract ONLY the aptamer screening experiment(s) directly investigated and described in THIS document.
- DO NOT extract screening methods that are only cited from other literature or mentioned in general review sections.
- DO NOT extract general theoretical principles of SELEX unless they are parameters of the described experiment.
- If multiple distinct screening campaigns are described (e.g., against different targets), treat each as a separate object within the `aptamer_screening_campaigns` array.

## OUTPUT FORMAT
Your response must be a single, valid JSON object with the following structure:
```json
{
  "literature_id": "Document_Identifier",
  "aptamer_screening_campaigns": [
    // Array of screening campaign objects following the schema below
  ]
}
```

## SELEX_EXPERIMENT_SCHEMA
For each complete aptamer screening campaign, extract the following fields:

### 1. Project Information
| Field | Description | Required | Format |
|---|---|---|---|
| `screening_id` | Unique identifier for this campaign within the document. | Yes | String (e.g., "screening_1") |
| `target_name` | The primary molecule, cell, or complex targeted for binding. | Yes | String |
| `target_type` | The classification of the target. | Yes | String (Enum: "Protein", "Small_Molecule", "Whole_Cell", "Peptide_MHC_Complex", "Other") |
| `selection_methodology` | The core screening technique employed. | Yes | String (e.g., "Cell-SELEX", "SELEX", "MB-SELEX", "CE-SELEX", "Blocker-SELEX") |

### 2. Library Specification
| Field | Description | Required | Format |
|---|---|---|---|
| `library_specification`| Details of the initial nucleic acid library. | Yes | Object |
| → `nucleic_acid_type`| Type of nucleic acid used. | Yes | String (Enum: "ssDNA", "RNA") |
| → `sequence_template`| The template sequence of the library, with the random region denoted by `(N)`. | Yes | String (e.g., "5'-GTT...-N(36)-TGA...-3'") |
| → `random_region_length`| The length of the randomized nucleotide section. | Yes | Integer |
| → `primer_forward` | The sequence of the forward primer. | Yes | String |
| → `primer_reverse` | The sequence of the reverse primer. | Yes | String |
| → `initial_amount` | The starting amount of the library. | Yes | Object: `{"value": number, "unit": "string"}` (e.g., `{"value": 1, "unit": "nmol"}`) |

### 3. Target Preparation
| Field | Description | Required | Format |
|---|---|---|---|
| `target_preparation` | How the target was prepared for selection. | Yes | Object |
| → `immobilization_method`| Method used to fix the target to a solid phase. Use "None" for cell-based or free-solution methods. | Yes | String (Enum: "None", "Magnetic_Beads", "Sepharose_Column", "Nanoparticles", "Plate_Coating") |
| → `immobilization_chemistry`| The chemical reaction used for coupling. | No | String (e.g., "Amine coupling", "Biotin-Streptavidin") |
| → `target_details` | For non-cell targets, specify the form used. For cell targets, provide cell line info. | Yes | String (e.g., "Recombinant Human LRP6", "Mouse podocyte cell line (MPC5)") |

### 4. SELEX Rounds Protocol
| Field | Description | Required | Format |
|---|---|---|---|
| `selex_rounds` | An array detailing the protocol for each round or block of rounds. | Yes | Array of `selex_round` objects (see complex structures) |

### 5. Post-SELEX Analysis
| Field | Description | Required | Format |
|---|---|---|---|
| `sequencing_and_bioinformatics`| How final aptamer candidates were identified. | Yes | Object |
| → `sequencing_rounds`| Which rounds were subjected to sequencing. | Yes | Array of integers |
| → `sequencing_technology`| The sequencing platform or method used. | Yes | String (e.g., "High-Throughput Sequencing", "NGS", "MPS") |
| → `bioinformatics_tools`| Software or platforms used for sequence analysis. | No | Array of strings (e.g., "AptaSUITE", "mfold", "MEME") |

### 6. Characterized Aptamers
| Field | Description | Required | Format |
|---|---|---|---|
| `characterized_aptamers`| An array detailing the properties of validated candidate aptamers. | Yes | Array of `candidate_aptamer` objects (see complex structures) |

### 7. Additional Information
| Field | Description | Required | Format |
|---|---|---|---|
| `notes`| Any other critical information or unique aspects of the methodology. | No | String |

## COMPLEX OBJECT STRUCTURES

### SELEX Round Object
This object details the conditions for a specific round or a block of identical rounds. Create a new object for each change in conditions.
```json
{
  "round_number": "1-3",
  "selection_strategy": {
    "positive_selection_target": "Injured Podocytes (ADR, PAN, HG models)",
    "counter_selection_target": "Normal Podocytes (used in first round)"
  },
  "binding_step": {
    "input_dna_amount": {"value": 100, "unit": "pmol"},
    "target_amount": {"value": 5e6, "unit": "cells"},
    "binding_buffer": "DPBS with 2 g/L glucose, 5 mM MgCl2, 1 mg/mL BSA, 1 mg/mL yeast tRNA",
    "incubation_temperature_c": 4,
    "incubation_time_min": 60
  },
  "partitioning_step": {
    "method": "Centrifugation and washing",
    "wash_cycles": 3,
    "wash_buffer": "DPBS with 2 g/L glucose, 5 mM MgCl2"
  },
  "elution_step": {
    "method": "Heat Denaturation",
    "elution_buffer": "Water",
    "elution_temperature_c": 95,
    "elution_time_min": 10
  },
  "amplification_step": {
    "pcr_type": "Symmetric",
    "pcr_polymerase": null,
    "pcr_cycles": 15,
    "primer_modifications": ["FAM-labeled forward primer"]
  },
  "ssdna_generation": {
    "method": "Denaturing PAGE"
  },
  "monitoring_method": "Flow Cytometry"
}
```

### Candidate Aptamer Object
This object contains all information for a single, validated aptamer candidate.
```json
{
  "aptamer_id": "RLS-2",
  "sequence_full": "Full sequence including primers if used in characterization",
  "sequence_core": "Core binding sequence after truncation (if applicable)",
  "aptamer_affinity": [ // Array to hold different affinity measurements
    {
      "target": "Injured Podocytes (ADR-induced)",
      "assay_method": "Flow Cytometry",
      "kd": {"value": 54.23, "unit": "nM", "error_margin": "± 4.73"},
      "ic50": null
    },
    {
      "target": "Injured Podocytes (PAN-induced)",
      "assay_method": "Flow Cytometry",
      "kd": {"value": 54.13, "unit": "nM", "error_margin": "± 3.23"},
      "ic50": null
    }
  ],
  "specificity_results": {
    "summary": "Specifically binds to injured podocytes over normal podocytes and other kidney cell lines (HK-2, HMC, HRGEC).",
    "off_targets_tested": ["Normal podocytes", "HK-2", "HMC", "HRGEC"]
  },
  "optimization_details": {
    "parent_aptamer": "S7",
    "strategy": "Truncation and Phosphorothioate modification",
    "outcome": "Superior affinity and improved stability."
  },
  "validation": {
    "in_vitro": "Internalized by podocytes, localized to lysosomes, successfully delivered siRNA.",
    "in_vivo": "Preferentially accumulates within glomeruli in adriamycin-induced and diabetic nephropathy mice."
  },
  "binding_target_protein": "EPB41L5"
}
```

## SPECIAL CASES AND NOTATIONS
1.  **Iterative Stringency**: If conditions change across rounds (e.g., target concentration decreases, wash cycles increase), create a *new* `selex_round` object for each set of changed conditions. Use a range for `round_number` (e.g., "5-7") only if all parameters within that block are identical.
2.  **Missing Data**: If a field is not mentioned in the text, set its value to `null` or omit the field if it is optional (marked "No" in the "Required" column).
3.  **Error Margins**: If reported, include standard deviation or error as a string in the `error_margin` field (e.g., "± 5.0").
4.  **Complex Targets**: For Cell-SELEX, `target_name` should describe the cell state (e.g., "Adriamycin-injured podocytes"). For Blocker-SELEX, `target_name` should describe the protein-protein interaction (e.g., "SCAF4-RNAP2 interaction").

## EXTRACTION PROCESS
1.  Read the document thoroughly to identify the complete aptamer screening campaign.
2.  For the campaign, systematically extract data for each field defined in the `SELEX_EXPERIMENT_SCHEMA`.
3.  Pay close attention to the `selex_rounds` section, detailing the parameters for each round or block of rounds to capture the iterative increase in stringency.
4.  For each validated aptamer, create a `candidate_aptamer` object and populate it with its sequence, affinity data, and characterization results.
5.  Structure all extracted information into the final JSON format.
6.  Verify that all extracted values are directly supported by the provided text and that the final JSON is well-formed.

## EXAMPLE
```json
{
  "literature_id": "2025-周超-Advanced Science.pdf",
  "aptamer_screening_campaigns": [
    {
      "screening_id": "screening_1_podocyte",
      "target_name": "Injured Podocytes",
      "target_type": "Whole_Cell",
      "selection_methodology": "Cell-SELEX",
      "library_specification": {
        "nucleic_acid_type": "ssDNA",
        "sequence_template": "5'-GTT CGT GGT GTG CTG GAT GT-N(36)-TGA CAC ATC CAG CAG CAC GA-3'",
        "random_region_length": 36,
        "primer_forward": "5'-FAM-GTT CGT GGT GTG CTG GAT GT-3'",
        "primer_reverse": "5'-AAA.../iSp18/TCG TGC TGC TGG ATG TGT CA-3'",
        "initial_amount": {
          "value": 1e+15,
          "unit": "sequences"
        }
      },
      "target_preparation": {
        "immobilization_method": "None",
        "immobilization_chemistry": null,
        "target_details": "Mouse podocyte cell line (MPC5) stimulated with adriamycin (ADR), puromycin aminonucleoside (PAN), or high glucose (HG)."
      },
      "selex_rounds": [
        {
          "round_number": "1",
          "selection_strategy": {
            "positive_selection_target": "Injured MPC5 cells (ADR, PAN, or HG)",
            "counter_selection_target": "Normal MPC5 cells"
          },
          "binding_step": {
            "input_dna_amount": null,
            "target_amount": null,
            "binding_buffer": "DPBS with glucose, MgCl2, BSA, yeast tRNA, salmon sperm DNA",
            "incubation_temperature_c": 4,
            "incubation_time_min": 60
          },
          "partitioning_step": {
            "method": "Centrifugation and washing",
            "wash_cycles": null,
            "wash_buffer": null
          },
          "elution_step": null,
          "amplification_step": {
            "pcr_type": "Symmetric",
            "pcr_polymerase": null,
            "pcr_cycles": 15,
            "primer_modifications": [
              "FAM-labeled forward primer",
              "Biotin-labeled reverse primer is NOT mentioned, but a polyA tail and spacer are"
            ]
          },
          "ssdna_generation": {
            "method": "Denaturing PAGE"
          },
          "monitoring_method": "Flow Cytometry"
        },
        {
          "round_number": "2-14",
          "selection_strategy": {
            "positive_selection_target": "Injured MPC5 cells (ADR, PAN, or HG)",
            "counter_selection_target": "Normal MPC5 cells"
          },
          "binding_step": null,
          "partitioning_step": null,
          "elution_step": null,
          "amplification_step": null,
          "ssdna_generation": null,
          "monitoring_method": "Flow Cytometry, with enrichment noted after 5-7 rounds and plateau at 11-14 rounds."
        }
      ],
      "sequencing_and_bioinformatics": {
        "sequencing_rounds": [
          2,
          11,
          14
        ],
        "sequencing_technology": "High-Throughput Sequencing",
        "bioinformatics_tools": [
          "MEGA 11 (Phylogenetic tree)",
          "mfold (Secondary structure)"
        ]
      },
      "characterized_aptamers": [
        {
          "aptamer_id": "S7",
          "sequence_full": null,
          "sequence_core": null,
          "aptamer_affinity": [
            {
              "target": "Injured Podocytes (ADR-induced)",
              "assay_method": "Flow Cytometry",
              "kd": { "value": 66.55, "unit": "nM", "error_margin": "± 9.29" },
              "ic50": null
            }
          ],
          "specificity_results": {
            "summary": "Showed highest binding affinity to injured podocytes among initial candidates.",
            "off_targets_tested": []
          },
          "optimization_details": null,
          "validation": null,
          "binding_target_protein": null
        },
        {
          "aptamer_id": "RLS-2",
          "sequence_full": null,
          "sequence_core": "Derived from S7-2 by truncation",
          "aptamer_affinity": [
            {
              "target": "Injured Podocytes (ADR-induced)",
              "assay_method": "Flow Cytometry",
              "kd": { "value": 54.23, "unit": "nM", "error_margin": "± 4.73" },
              "ic50": null
            },
            {
              "target": "Injured Podocytes (PAN-induced)",
              "assay_method": "Flow Cytometry",
              "kd": { "value": 54.13, "unit": "nM", "error_margin": "± 3.23" },
              "ic50": null
            },
            {
              "target": "Injured Podocytes (HG-induced)",
              "assay_method": "Flow Cytometry",
              "kd": { "value": 72.18, "unit": "nM", "error_margin": "± 5.78" },
              "ic50": null
            }
          ],
          "specificity_results": {
            "summary": "Specifically binds injured podocytes (mouse and human) over other kidney cells. Competes with S7.",
            "off_targets_tested": [
              "Normal podocytes",
              "HK-2",
              "HMC",
              "HRGEC"
            ]
          },
          "optimization_details": {
            "parent_aptamer": "S7",
            "strategy": "Truncation of S7-2 variant; Phosphorothioate modification for stability.",
            "outcome": "Superior affinity (lower Kd) and improved stability compared to S7."
          },
          "validation": {
            "in_vitro": "Internalized by podocytes via caveolae-dependent pathway and macropinocytosis, localizes to lysosomes, no cytotoxicity, successfully used for siRNA delivery.",
            "in_vivo": "Preferentially accumulates in glomeruli of ADR-induced and diabetic nephropathy mice, colocalizes with podocyte marker synaptopodin."
          },
          "binding_target_protein": "EPB41L5"
        }
      ],
      "notes": "The study successfully identified an aptamer (S7) and optimized it (RLS-2) to specifically target injured podocytes both in vitro and in vivo. The target protein was identified as EPB41L5."
    }
  ]
}
```
    """
    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, content: str, literature_id: str = "example_paper_id") -> str:
        return f"{self.BASE}\n\nPaper ID: {literature_id}\n\n{content}"


