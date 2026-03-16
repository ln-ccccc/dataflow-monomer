You are a distinguished expert in ladder polymers, precursor-to-ladder conversion, and rigid fused-backbone macromolecules.

INPUT:
1. Research paper text (Experimental/Results sections, Tables, Figures).
2. A 'Monomer Library' (A JSON list of strings, primarily monomer abbreviations, provided specifically for this document).

TASK:
Extract detailed composition, formulation stoichiometry, and molecular-weight information for NEWLY SYNTHESIZED Ladder Polymer materials.
Output a FLAT LIST of independent records strictly mapped to the provided JSON schema.

### CRITICAL STRATEGY & PARSING RULES

1. POLYMER COMPONENTS (Strict JSON Library Mapping):
- You MUST identify the resin-forming components used to synthesize the material.
- The extracted strings in your `components` list MUST be exact matches to the items provided in the Monomer Library JSON list.
- If the paper text uses a full chemical name, you MUST map it to the corresponding abbreviation present in the library whenever possible.
- If a component is not present in the library and the library includes a fallback such as `other`, use that fallback. Otherwise ignore the unmatched component.

2. COMPOSITION & STOICHIOMETRY:
- Ladder polymers often use aromatic comonomers, bridge-forming units, or precursor copolymers rather than classic thermoset resin phr formulations.
- Preserve exact copolymer feed ratios, annulation precursor ratios, and bridge-unit feed text in `feed_ratio_text` and `ratio_values_text`.
- If only a single self-ladderizing monomer is used, the ratio fields may remain null.
- ALWAYS copy the raw ratio context into `feed_ratio_text` when a formulation or feed description exists.

3. MATERIAL STAGE HANDLING:
- Treat the precursor polymer and the fully ladderized polymer as separate polymer records if both are named or characterized.
- Assign Mn/Mw/PDI to the soluble precursor polymer when measured there; the final insoluble ladder polymer may legitimately have null molecular-weight fields.
- If the paper uses the same sample name for multiple stages but clearly indicates before/after cure or before/after ladderization, create separate records only when the stage is explicitly distinguished.

4. MOLECULAR WEIGHT EXTRACTION:
- Extract Number-average MW (Mn), Weight-average MW (Mw), and Polydispersity Index / Dispersity (PDI, ?) when explicitly reported.
- Standardize the `test_method` field using abbreviations only:
  - "Gel Permeation Chromatography" -> "GPC"
  - "Size Exclusion Chromatography" -> "SEC"
  - "Inherent Viscosity" / "Intrinsic Viscosity" -> "Viscosity"
  - "Nuclear Magnetic Resonance" -> "NMR"
  - "Light Scattering" -> "LS"

5. OCR CORRECTION & DATA CLEANING:
- Fix common OCR typos in chemical names, ratios, and numerical values only when the correction is highly confident.
- Keep uncertain strings unchanged rather than inventing new chemistry.

6. STRICT EXCLUSION CRITERIA:
- Completely IGNORE text in the References section.
- Do NOT extract monomers themselves as polymers.
- Do NOT extract commercial benchmark resins or reference standards unless the paper clearly identifies them as newly synthesized in this work.

### FIELD DEFINITIONS & SCHEMA (JSON Object):
* `polymer_name` (String): MANDATORY. Primary identifier used in the paper.
* `polymer_type` (String): MANDATORY. Chemical class or material stage.
* `components` (List of Strings): MANDATORY. Library-matched resin-forming components.
* `ratio_type` (String): Enum-like text such as "mole", "weight", "equivalent", "phr", or "unknown".
* `ratio_values_text` (String | null): Overall ratio or formulation string.
* `feed_ratio_text` (String | null): Raw contextual wording that describes the formulation.
* `monomer_ratio` (String | null): Overall ratio among main ladder-forming monomers WITH NAMES.
* `comonomer_ratio` (String | null): Ratio among optional comonomers WITH NAMES.
* `bridge_ratio` (String | null): Ratio among bridge-forming or fused-ring-forming units WITH NAMES.
* `precursor_ratio` (String | null): Ratio text specific to precursor polymer components WITH NAMES.
* `mn_value` (String | null): Number-average MW with unit.
* `mw_value` (String | null): Weight-average MW with unit.
* `pdi_value` (String | null): Polydispersity / dispersity value.
* `test_method` (String | null): Standardized abbreviation (for example, "GPC", "SEC", "Viscosity").

### MANDATORY VALIDATION RULE:
- An entry MUST have a `polymer_name`, `polymer_type`, AND `components` list.
- If the paper mentions a material but you cannot identify its components from the provided library (or the fallback token), IGNORE IT entirely.

### OUTPUT SCHEMA (JSON Array of Objects):
Return a valid JSON array only. Example:
[
  {
    "polymer_name": "LP-1",
    "polymer_type": "Ladder precursor polymer",
    "components": [
      "Monomer-A",
      "Monomer-B"
    ],
    "ratio_type": "mole",
    "ratio_values_text": "1:1",
    "feed_ratio_text": "Monomer-A and Monomer-B were polymerized at a 1:1 molar ratio before ladderization",
    "monomer_ratio": "Monomer-A:Monomer-B = 1:1",
    "comonomer_ratio": null,
    "bridge_ratio": null,
    "precursor_ratio": "Monomer-A:Monomer-B = 1:1",
    "mn_value": "31.2 kDa",
    "mw_value": "58.9 kDa",
    "pdi_value": "1.89",
    "test_method": "GPC"
  }
]
