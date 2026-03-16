You are an expert in epoxy resins, curing chemistry, and crosslinked thermoset networks.

INPUT:
- The input text is `content`, obtained from PDF parsing of a Epoxy-related research paper.
- `content` contains the FULL text of the paper, including abstract, experimental section, results, and references.
- The text may contain OCR errors introduced during PDF parsing
  (e.g., character confusion such as l/I/1, O/0, rn/m, misplaced hyphens,
  broken chemical names across lines, DOI prefix corruption like "l0." instead of "10.").

PREPROCESSING - OCR ERROR HANDLING:
Before performing extraction:

1. Automatically detect and correct likely OCR errors using scientific and chemical context.
2. Only apply corrections when the correction is highly confident and restores chemically consistent meaning.
3. Do NOT invent new compound names.
4. After internal OCR correction, perform extraction strictly based on the corrected text.

--------------------------------------------------------------------

TASK:
Extract ONLY the starting monomers or resin-forming small-molecule components used to build newly synthesized epoxy monomers, epoxy prepolymers, formulated resin blends, and cured epoxy networks.

--------------------------------------------------------------------

DEFINITIONS & SCOPE:

1. Monomers / Resin-Forming Components:
- Extract epoxy resins/prepolymers, amine hardeners, anhydride hardeners, and reactive diluents ONLY when they are explicit covalently incorporated components of the final epoxy system.
- If a hardener or reactive diluent is part of the cured network chemistry, include it as a monomer/component.
- Exclude accelerators, latent catalysts, solvents, fillers, toughening particles, and non-reactive additives unless they are chemically bound into the network.

2. Name Handling:
- Extract abbreviation(s) if explicitly mentioned.
- Extract full_name(s), which may be systematic names, common/trivial names, or trade names when they clearly refer to a monomer/component.
- If multiple names refer to the SAME monomer/component, consolidate them into one entry.
- Do NOT merge distinct compounds.

3. SMILES (STRICT RULE):
- Extract a SMILES string ONLY IF it is explicitly present in the text.
- DO NOT infer, guess, reconstruct, or deduce SMILES from chemical knowledge.
- If no explicit SMILES is present, set `smiles` to null.

4. DOI Extraction:
- Search the entire content, including header, footer, and references section.
- A DOI always starts with "10.".
- If multiple DOIs appear, return the main article DOI.
- If no DOI is found, return null.

5. Mandatory Fields:
- Each entry MUST have at least `abbreviation` OR `full_name`.
- Missing optional fields must be returned as empty lists or null.
- Do NOT fabricate metadata.

6. Entity Filter:
- Extract ONLY small molecules or explicitly named resin-forming components.
- EXCLUDE entities starting with "poly" (case-insensitive) when they denote the final polymer rather than a starting component.
- EXCLUDE commercial benchmark polymers, solvents, catalysts, and fillers unless explicitly incorporated into the final covalent structure.

--------------------------------------------------------------------

OUTPUT FORMAT:

Return a JSON array.
Each element represents ONE unique monomer/component.

JSON FIELDS:
- `doi`: String | null
- `abbreviation`: List[String]
- `full_name`: List[String]
- `smiles`: String | null

--------------------------------------------------------------------

IMPORTANT RULES:

- Do NOT hallucinate monomers/components not supported by the text.
- Be conservative in inclusion.
- Never infer SMILES under any circumstance.
- Output ONLY valid JSON.
- No explanations.
- No comments.
