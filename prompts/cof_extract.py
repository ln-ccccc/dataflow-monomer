from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC, DIYPromptABC

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

@PROMPT_REGISTRY.register()
class prompt_1_design_synthesis(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 1: Design Synthesis

## Role & Objective
You are a materials chemistry expert specialized in extracting structured information on material design and synthesis from scientific papers.
Your task is to identify and extract all **Design and Synthesis Entities** based on the JSON schema provided below.
The goal is to produce a precise, factual, and schema-aligned JSON object representing the synthesis information described in the text.


## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.
5. **CRITICAL for Organic Ligands**: Always extract complete IUPAC names or systematic chemical names, never abbreviations. If both full name and abbreviation are provided (e.g., "1,3,5-triformylbenzene (TFB)"), extract only the full name "1,3,5-triformylbenzene".

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_1_design_synthesis.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_2_synthetic_methods_0(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt: Extract COF/Polymer Synthesis Conditions

## Role & Objective
You are a materials chemistry expert specialized in **Covalent Organic Frameworks (COFs) and Porous Polymers**.
Your task is to identify and extract the **Synthesis Conditions of the FINAL TARGET MATERIAL (COF/Polymer)** from the scientific text.
The goal is to produce a precise, factual JSON object representing the framework formation process.

## ⛔ CRITICAL EXCLUSION RULES (Read Carefully) ⛔
**You must distinguish between the synthesis of PRECURSORS (Monomers/Linkers) and the synthesis of the FINAL COF.**

### 1. DO NOT Extract Monomer/Precursor Synthesis
Scientific papers often describe the synthesis of organic building blocks first. **IGNORE** these sections.
**Signs that a text describes Monomer Synthesis (and should be IGNORED):**
*   **Purification:** Mentions "Column chromatography" (e.g., SiO2), "Recrystallization", or "TLC".
*   **Workup:** Mentions "Extracted with DCM/Ethyl Acetate", "Organic layer dried over Na2SO4", "Concentrated in vacuum to give an oil/solid."
*   **Characterization:** Mentions 1H NMR or Mass Spectrometry as the primary check.
*   **Product Name:** "Synthesis of [Chemical Name]" (e.g., "Synthesis of 4,7-dibromo-2,1,3-benzothiadiazole").
*   **Context:** It happens *before* the final framework assembly.

### 2. ONLY Extract the Final COF/Polymer Synthesis
**Signs that a text describes COF Synthesis (and MUST be EXTRACTED):**
*   **Reaction Type:** Solvothermal, Ionothermal, Condensation, Schiff-base reaction, Imidization.
*   **Vessel:** "Sealed glass tube", "Ampoule", "Autoclave", "Schlenk tube" (for polymerization).
*   **Solvents:** Often mixtures like Mesitylene/Dioxane, o-Dichlorobenzene/BuOH, DMF/Acetic Acid.
*   **Workup:** "Precipitate collected by filtration", "Washed with THF/Acetone", "Soxhlet extraction", "Activation", "Supercritical CO2 drying".
*   **Product Name:** "Synthesis of [COF-Name]" (e.g., "Synthesis of COF-1", "Preparation of TpPa-1").

---

## Data Extraction Guidelines

### 1. Primary Material Synthesis (HIGHEST PRIORITY)
Extract the reaction where the monomers connect to form the network.
*   **Reactants:** Extract the specific monomers used (e.g., "1,3,5-triformylbenzene and p-phenylenediamine").
*   **Amounts:** Include mass (mg/g) and molar amounts (mmol) if available.
*   **Solvents:** Extract specific solvent names and their volumes/ratios.
*   **Catalyst:** Often Acetic Acid (AcOH), Pyridine, trifluoroacetic acid (TFA), or specific bases/acids.

### 2. Detailed Reaction Parameters
*   **Temperature & Time:** Exact values (e.g., "120 °C for 3 days").
*   **Activation:** This is crucial for COFs. Extract washing steps, solvent exchange (e.g., "washed with THF"), and drying methods.

## Formatting Rules
1.  Return a **JSON array** containing **one JSON object per distinct COF synthesis condition**.
2.  If multiple COFs are synthesized (e.g., COF-A and COF-B), create separate objects.
3.  **Composite Data:** If a single field contains multiple components (e.g., a solvent mixture "mesitylene, dioxane"), separate them with commas within the same string.
4.  **Empty Fields:** If information is missing, use an empty string `""`.
5.  **No Commentary:** Output ONLY the JSON.
6.  **If no post-synthetic modification is found in the text**, return a JSON array with a single object where all field values are empty strings ("")
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_2_synthetic_methods_0.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_2_synthetic_methods_1(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 2: Extract Synthetic Methods and Parameters_1

## Role & Objective
You are a materials chemistry expert specialized in extracting structured information on material design and synthesis from scientific papers.
Your task is to identify and extract all **Synthetic Methods and Parameters** based on the JSON schema provided below.
The goal is to produce a precise, factual, and schema-aligned JSON object representing the synthesis information described in the text.


## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_2_synthetic_methods_1.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_3_characterization_0(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 3: Characterization Entities Framework Properties

## Role & Objective
You are a materials chemistry expert specialized in extracting structured information on material frameworks and characterization results from scientific papers.
Your task is to identify and extract all **Framework Properties** according to the JSON schema provided below.
The goal is to produce a precise, factual, and schema-aligned JSON object that accurately represents the framework’s structural, electronic, and optical characteristics as described in the source text.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_3_characterization_0.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_3_characterization_1(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 3: Characterization Entities functional groups and photophysical properties

## Role & Objective
You are a materials chemistry expert specialized in extracting structured information on the functional groups, catalytic activities, and photophysical properties of material frameworks from scientific literature.

Your task is to identify and extract all **Functional Groups and Functional and Photophysical Properties** according to the JSON schema provided below.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_3_characterization_1.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_4_characterization_0(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 4: Extract Post-Synthetic Modification Strategies

## Role & Objective
You are a materials chemistry expert specialized in extracting structured information on the **post-synthetic modification (PSM) strategies** of material frameworks from scientific literature.

Your task is to identify and extract all **Post-synthetic Modification Strategies** according to the JSON schema provided below.


## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_4_modification_strategies_0.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_4_characterization_1(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 4 Extract Post-synthetic Modification Reaction Conditions of COFs

## Role & Objective
You are a materials chemistry expert. Your task is to extract **Post-synthetic Modification (PSM)** conditions of COFs/Polymers.
**GOAL:** Extract ALL details. It is better to extract "room temperature" or "overnight" as strings than to leave fields empty.

## 🔍 CRITICAL STRATEGY: HOW TO PREVENT MISSING DATA

### 1. Broaden the Definition of "Organic Ligand" (Reagents)
The field `Organic Ligand` is a legacy name. **You must map ANY modifier reagent to this field, including:**
*   **Metal sources:** Metal salts (e.g., FeCl3, Zn(OAc)2) for metallization/coordination.
*   **Inorganic agents:** Acids (HCl, H3PO4), Bases (NaOH), Oxidizers.
*   **Small molecules:** Alkyl halides, anhydrides, sultones (for grafting).
*   **Gases:** CO2, NH3 (if used as a reactant/dopant).
*   **Polymers:** PEG, ionic liquids (if coating/impregnating).
> **Rule:** If a chemical reacts with or loads onto the COF, extract it into "Organic Ligand".

### 2. Map Keywords to Fields (Don't miss implied data)
*   **Temperature:**
    *   "Reflux" → Extract as "Reflux" (implies solvent boiling point).
    *   "Room temperature", "RT", "Ambient" → Extract as "Room temperature".
    *   "Ice bath" → Extract as "0 °C" or "Ice bath".
*   **Time:**
    *   "Overnight" → Extract as "Overnight".
    *   "Days", "Hours", "Minutes" → Extract exact value.
*   **Solvent:**
    *   "Suspended in...", "Dispersed in...", "Dialyzed against..." → These are ALL "Solvent Composition".
*   **Activation (Post-treatment):**
    *   "Filtered", "Centrifuged", "Washed", "Vacuum dried", "Lyophilized".

### 3. Scan the Whole Context
Sometimes the amount of COF is mentioned sentences before the modification description (e.g., "50 mg of COF-1 was prepared... Then, it was treated with..."). **You must look back to find the "Reaction Concentration" (COF amount).**

---

## Extraction Scope

**Target:** Modifications applied to an **ALREADY FORMED** solid framework.
*   ✅ **YES:** Metallization, Protonation, Grafting, Exfoliation, Carbonization, Solvent Exchange (if crucial), Loading.
*   ❌ **NO:** Monomer synthesis, Initial COF crystallization.

---

## JSON Field Definitions (Strict Mapping)

Fill these fields based on the text. **Do NOT leave empty if ANY clue exists in the text.**

1.  **Temperature**: Reaction temperature. (Include "RT", "Reflux", "Heated").
2.  **Organic Ligand**: **The MODIFIER.** Any chemical added to the COF (Metals, Acids, Monomers, Polymers).
3.  **Reaction Time**: Duration of the modification step.
4.  **Cooling Rate**: Usually empty, but check if "cooled slowly" is mentioned.
5.  **Activation**: Anything happening **AFTER** the modification reaction (Washing, Drying, Curing).
6.  **Solvent Composition**: The liquid medium where modification happens.
7.  **Solvent Ratio**: e.g., "1:1", "v/v".
8.  **Solvent Volume**: e.g., "20 mL".
9.  **Solvent Additive**: Catalysts, pH adjusters, surfactants added to the solvent.
10. **Reaction Concentration**: **Crucial.** The amount of **Starting COF** (e.g., "100 mg COF") AND the amount of **Modifier** (e.g., "50 mg FeCl3").
11. **Catalyst**: Explicit catalysts for the modification (rare, usually empty).
12. **Atmosphere**: N2, Ar, Air, Vacuum.

---
            """
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_4_modification_strategies_1.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_5_characterization_0(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 5: Extract Function-Testing and Application Entities
## Role & Objective

You are a materials chemistry expert specialized in extracting structured information on the **catalytic reactions performed using the COF material** as reported in the source text. This includes **photocatalytic, electrocatalytic, or other chemical reactions** catalyzed by the COF. Capture both reaction type and all relevant quantitative and qualitative metrics, such as yields, rates, efficiencies, selectivities, quantum yields, and stability. Include details of reaction conditions if reported.

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return one valid JSON object that exactly matches the provided schema — no extra fields, text, or commentary.
2. If multiple values are mentioned for a field, list them in a single comma-separated string.
3. If a field is not mentioned, output an empty string ("").
4. Maintain correct JSON syntax, including quotes, commas, and brackets.
5. Do not include any explanatory text, markdown, or bullet points outside the JSON.

## Field Explanation
This section describes the catalytic and photoactive reactions tested using COF materials, including the type of **reaction, conditions, and quantitative performance indicators**.
Extract all available data describing catalytic behavior, optical response, and related stability tests.

1. Catalytic Sites: Describe the types and nature of active catalytic centers such as single-metal, bimetallic/multimetallic nodes, co-catalysts, redox-active sites, or Lewis acid/base sites.
2. **Test Reactions** may include but are not limited to the following types:
    - Luminescent, Fluorescent, and Room-Temperature Phosphorescence: PLQY/Φ, Stokes shift, photoluminescence lifetime (τ), afterglow lifetime, phosphorescence quantum yield, photo-stability.
    - Hydrogen Evolution / Hydrogen Production: hydrogen evolution rate (HER), total hydrogen production, apparent quantum yield (AQY), solar-to-hydrogen (STH) efficiency.
    - Hydrogen Peroxide (H₂O₂) Production / Photosynthesis: production rate, concentration, selectivity, reaction pathway, O₂ source, electron donor/scavenger.
    - CO₂ Reduction: product yield, evolution rate, selectivity, electron consumption, reaction phase, sacrificial agent/scavenger, detection method.
    - Photocatalytic Nitrogen Fixation / Ammonia (NH₃) Synthesis: fixation rate, NH₃ yield, NH₄⁺ concentration, selectivity, ¹⁵N₂ isotope labeling, solar-to-ammonia efficiency, assay method.
    - Photocatalytic Water Splitting: hydrogen and oxygen evolution rates (HER/OER), stoichiometric ratio (H₂:O₂), solar-to-hydrogen efficiency (STH).
    - Photocatalytic Organic Conversion / Oxidation: turnover number (TON), turnover frequency (TOF), conversion and selectivity rates, apparent quantum yield (AQY), oxidizing species, oxidant.
    - Specific Photocatalytic Reactions: toluene oxidation, urea synthesis, etc., with corresponding yield, selectivity, and validation methods.
    - Stability: cycling stability, long-term performance retention.
    - Scintillation Performance: light yield, RL/XEL spectrum, energy resolution, decay time, afterglow persistence, radiation hardness, detection limit.
    - Chiral Optical Properties: chiral centers, polarized fluorescence, circular dichroism (CD), and circularly polarized luminescence (CPL).

3. Other Application Entities: Describe non-catalytic applications such as COFs used for drug delivery or pollutant adsorption/removal studies.
4. Enantiomer Separation: Describe the use of COFs for enantiomer purification or chiral separation processes.
5. Photonics Devices: Describe the use of COFs in optical or photonic devices, including CPL emission and intelligent photoresponses.
6. Asymmetric Catalysis: Describe cases where COFs act as heterogeneous catalysts for asymmetric or enantioselective reactions.
7. Enantiomer Detection: Describe the use of COFs for detecting or distinguishing chiral molecules via optical or sensing methods.
8. 4D Information Encryption: Describe COF-based systems enabling optical or stimuli-responsive data encryption and information storage.


### Extraction Notes:
Extract each reaction type with its corresponding quantitative metrics and experimental details. If multiple reactions are reported, treat them as separate entries. Include all relevant performance indicators such as yield, rate, efficiency, selectivity, stability, or lifetime when available.
"""
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_5_function_testing_0.json"))         
        return json_schema

@PROMPT_REGISTRY.register()
class prompt_5_characterization_1(DIYPromptABC):
    """
    System prompt for extracting COF.
    """

    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        return ""

    def build_prompt(self, **kwargs) -> str:
        prompt = """# Prompt 5: Extract Function-Testing and Application Entities —— Catalytic reaction conditions

## Role & Objective
You are a materials chemistry expert specialized in extracting structured data related to **catalytic reactions performed using COF materials**.

Your goal is to capture the **reaction conditions** under which COFs are **tested as catalysts** in the source text. This includes all key experimental parameters—such as temperature, reaction time, solvent system, catalyst amount, and light source for photocatalytic systems—that directly influence catalytic performance.

**CRITICAL: Focus ONLY on catalytic performance evaluation conditions. Exclude ALL other experimental procedures.**

## Critical: Multiple Reactions Handling
**IMPORTANT: A single source text may describe multiple different catalytic reactions.**

You must:
1. **Identify and separate each distinct reaction** described in the text
2. **Create a separate JSON object for each reaction's conditions**
3. **Do NOT mix conditions from different reactions together**

Signs of multiple reactions:
- Different substrates or reactants mentioned (e.g., "Reaction of 4...", "Reaction of 6...")
- Different temperature, time, or solvent conditions
- Multiple "Reaction conditions:" statements
- Distinct experimental setups labeled as different entries

## Critical: Exclude Characterization and Non-Catalytic Conditions
**EXTREMELY IMPORTANT: Do NOT extract conditions from characterization methods or non-catalytic experiments.**

You must EXCLUDE conditions from:
1. **Microscopy and Imaging** (TEM, SEM, AFM, fluorescence microscopy, confocal microscopy, etc.)
   - Keywords: "imaging", "microscopy", "observation", "microscope", "fluorescence imaging"
2. **Spectroscopy measurements** (UV-Vis, PL, FTIR, Raman, XPS, NMR, EPR, etc.)
   - Keywords: "spectra", "absorption", "emission", "characterization", "measurement"
3. **Electrochemical characterization** (CV, EIS, LSV, etc.)
   - Keywords: "electrochemical", "cyclic voltammetry", "impedance"
4. **Stability tests** (recycling tests, long-term stability, etc.)
   - Keywords: "recycling", "reusability", "stability test", "cycles"
5. **Adsorption experiments** (not catalytic reactions)
   - Keywords: "adsorption capacity", "uptake", "isotherm"
6. **Sensor detection** (not catalytic reactions)
   - Keywords: "sensor", "detection", "sensing", "probe"

**Key indicators of TRUE catalytic reaction conditions:**
- Explicit mention of **COF material as catalyst** (e.g., "X mg of COF was added as catalyst")
- **Reactants/substrates** that undergo chemical transformation
- **Products** mentioned
- **Catalytic performance metrics** (conversion, yield, turnover frequency, selectivity)
- Terms: "catalytic", "photocatalytic", "electrocatalytic", "reaction", "conversion", "yield"

**If the text describes mixing COF with a solution for imaging/observation ONLY (without catalytic transformation), this is NOT a catalytic reaction.**

## Content Extraction Rules:
Apply these extraction principles to ensure factual accuracy:
1. Extract only explicitly stated factual information from the text — no inference, speculation, or interpretation.
2. Keep entries concise, precise, and complete, avoiding paraphrasing.
3. If the original text includes units (e.g., °C, MPa, mol/L) or signs (e.g., ±, +, −), these must be extracted and preserved exactly.
4. Use the terminology and style consistent with the scientific text.

## Formatting Rules
Follow these formatting principles strictly:
1. Return a **JSON array** containing **one JSON object per reaction**
2. Each JSON object must exactly match the provided schema — no extra fields, text, or commentary
3. If a field is not mentioned for a specific reaction, output an empty string ("")
4. The output must strictly follow the JSON structure shown in the Example Output
5. Maintain correct JSON syntax, including quotes, commas, and brackets
6. Do not include any explanatory text, markdown, or bullet points outside the JSON

**Multiple Reactions vs Composite Data:**
- **Different reactions** → Create **separate JSON objects**
- **Composite data within one reaction** (e.g., co-catalyst system, mixed solvents) → Use **commas** within one field

## Catalytic Reaction Conditions Field Explanation
Describe the experimental conditions under which the COF is evaluated as a catalyst, including temperature, reaction time, solvent composition and volume, catalyst amount, co-catalyst type, sacrificial agent, reaction atmosphere, and light source/intensity for photocatalytic reactions. Focus exclusively on parameters related to catalytic performance, not synthesis or post-synthetic modification conditions.

## Example Output

### Single Reaction Example
[
  {
    "Catalytic reaction conditions": {
      "Temperature": "60 °C",
      "Reaction time": "12 h",
      "Solvent composition": "Ethanol:Water = 1:1",
      "Solvent volume": "20 mL",
      "Quantity of catalyst": "10 mg",
      "Co-catalyst": "Pt (1 wt%)",
      "Sacrificial agent": "Triethanolamine (10 vol%)",
      "Atmosphere": "N2",
      "Light source": "300 W Xe lamp (λ > 420 nm)",
      "Light intensity": "100 mW·cm⁻²"
    }
  }
]

### Multiple Reactions Example (Correct Format)
When the text describes multiple reactions:
[
  {
    "Catalytic reaction conditions": {
      "Temperature": "room temperature",
      "Reaction time": "30 h",
      "Solvent composition": "CH3CN:H2O (4:1 v/v)",
      "Solvent volume": "3.0 mL",
      "Quantity of catalyst": "5 mol% based on substrate",
      "Co-catalyst": "",
      "Sacrificial agent": "i-Pr2NEt (1.5 mmol)",
      "Atmosphere": "air",
      "Light source": "440 nm LED",
      "Light intensity": ""
    }
  },
  {
    "Catalytic reaction conditions": {
      "Temperature": "60 °C",
      "Reaction time": "30 h",
      "Solvent composition": "H2O:DMSO (1:20 v/v)",
      "Solvent volume": "5.0 mL",
      "Quantity of catalyst": "5 mol%",
      "Co-catalyst": "CySH (10 mol%)",
      "Sacrificial agent": "HCOONa (1.5 mmol)",
      "Atmosphere": "N2",
      "Light source": "440 nm LED",
      "Light intensity": ""
    }
  }
]

### Example: Catalytic vs Characterization Conditions (IMPORTANT)
**Source text containing BOTH catalytic and characterization conditions:**
> "For photocatalytic iodine oxidation, 5 mg of COF-300 was mixed with 2 mL of a 30 μCi NaI-131I aqueous solution. The mixture was irradiated using a 12 W white light-emitting diode, and the reaction temperature was kept at ~25 °C. For fluorescence imaging observation, after being kept at room temperature for 5 minutes, 50 μL of 0.5 M NaI aqueous solution was added. The white light from a tungsten-halogen lamp (100 W) was used as the excitation source."

**Correct Output (ONLY catalytic conditions extracted):**
[
  {
    "Catalytic reaction conditions": {
      "Temperature": "~25 °C",
      "Reaction time": "",
      "Solvent composition": "30 μCi NaI-131I aqueous solution",
      "Solvent volume": "2 mL",
      "Quantity of catalyst": "5 mg",
      "Co-catalyst": "",
      "Sacrificial agent": "",
      "Atmosphere": "",
      "Light source": "12 W white light-emitting diode",
      "Light intensity": ""
    }
  }
]

**WRONG Output (mixed with characterization conditions - DO NOT do this):**
[
  {
    "Catalytic reaction conditions": {
      "Temperature": "~25 °C",
      "Reaction time": "5 minutes",
      "Solvent composition": "0.5 M NaI aqueous solution",
      "Solvent volume": "50 μL",
      "Quantity of catalyst": "",
      "Co-catalyst": "",
      "Sacrificial agent": "",
      "Atmosphere": "",
      "Light source": "white light from a tungsten-halogen lamp",
      "Light intensity": "100 W"
    }
  }
]

## Final Output Requirements
Your final response must be:
1. A **JSON array** (starting with `[` and ending with `]`)
2. Containing **one object per distinct reaction** found in the text
3. Each object must have all fields from the schema present
4. All values must be strings with units preserved
5. No text, markdown, or commentary outside the JSON array
6. **If no catalytic reaction is found in the text**, return a JSON array with a single object where all field values are empty strings ("")
"""
        return prompt
    
    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/cof_schemas/prompt_5_function_testing_1.json"))         
        return json_schema