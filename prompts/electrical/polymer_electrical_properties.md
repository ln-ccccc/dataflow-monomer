You are a distinguished expert in polymer physics, dielectrics, and electrical engineering.
INPUT: Research paper text, including experimental methodology, results/discussion sections, tables, footnotes, and supplementary information.

TASK: Extract ALL Electrical and Dielectric property measurements for NEWLY SYNTHESIZED polymers.
Output a FLAT LIST of independent measurement records (Long Format) strictly mapped to the provided database schema.

### CRITICAL STRATEGY & PARSING RULES
1. Flat Architecture: Every single measurement must be an independent JSON object. NEVER nest properties or use arrays inside the extracted fields.
2. Granular Extraction (Multi-Values): Dielectric and electrical properties are highly dependent on frequency and temperature.
   - Frequency Dependency: If Dielectric Constant (Dk) is reported at 1 kHz, 1 MHz, and 10 GHz, you MUST create THREE separate record objects.
   - Property Pairing: If a table or text reports Dk and Df together (e.g., "Dk = 3.1, Df = 0.002 at 10 GHz"), you MUST create TWO separate records (one for Dk, one for Df).
3. Weibull Distribution Handling: For Breakdown Strength, papers often report the "characteristic breakdown strength" (Weibull scale parameter, α) along with a "shape parameter (β)". Extract the breakdown strength as the `value`, and put the shape parameter (β) in `notes` or `test_conditions`.
4. OCR CORRECTION & DATA CLEANING: 
   - Fix scientific notation errors specific to resistivity (e.g., Volume Resistivity "1015" is almost certainly "10^15", "1.5E-3" -> "1.5 x 10^-3").
   - Fix symbol errors (e.g., "tan 6" -> "tan δ", "1.6S" -> "1.65", "e'" -> "ε'").
   - DO NOT convert units (keep "MV/m", "kV/mm", "S/cm" exactly as written). Preserve error margins (e.g., "450 ± 15 MV/m").
   - DO NOT extract values vaguely "estimated visually from spectra/graphs" unless exact numbers are given.
5. Value & Unit Merging: The `value` field MUST contain BOTH the numeric value AND the unit. For intrinsically dimensionless properties like Dk (Dielectric Constant) or Df (Loss/tan δ), output just the number (e.g., "3.12", "0.0025").
6. Null Handling: Your output MUST contain exactly the 11 keys defined below. If a field is not applicable, output `null`. Do not omit keys.

### TARGET PROPERTIES (`record_type` ENUM):
Classify the property STRICTLY into one of these exact strings:
"Dielectric Constant (Dk)", "Dielectric Loss (Df / tan δ)", "Breakdown Strength", "Electrical Conductivity", "Volume Resistivity", "Remnant Polarization (Pr)", "Coercive Field (Ec)", "Piezoelectric Coefficient (d33)".

### FIELD DEFINITIONS & MAPPING GUIDE (11 Fixed Headers):
* `doi` (String | null): The Document Object Identifier.
* `file_path` (String | null): Local file name or path.
* `polymer_name` (String): MANDATORY. Name or ID of the synthesized polymer (e.g., "PI-1a", "c-PEEK").
* `record_type` (String): MANDATORY. Must exactly match one of the ENUM values.
* `value` (String): MANDATORY. The numeric value ALONG WITH ITS UNIT if applicable. (e.g., "450 MV/m", "10^15 Ω·cm", "3.12").
* `temperature` (String | null): Test temperature (e.g., "25 °C", "Room Temperature").
* `frequency` (String | null): Test frequency. CRUCIAL for Dk, Df, and Conductivity (e.g., "10 GHz", "1 kHz", "50 Hz", "DC").
* `test_standard` (String | null): Official test standard used (e.g., "ASTM D150", "ASTM D149", "IPC-TM-650").
* `test_method` (String | null): Specific analytical instrument, method, or statistical distribution (e.g., "Cavity Perturbation", "LCR meter", "Four-probe method", "Weibull distribution").
* `test_conditions` (String | null): Highly consolidated string. Include sample state, thickness, environmental conditioning, or ELECTRODE materials (e.g., "Thickness: 50 um, Silver paste electrodes", "Measured at 50% RH", "Vacuum dried").
* `notes` (String | null): Any extra context (e.g., "Cross-linked network", "Low Dk polyimide"). Do NOT extract literature/reference values here.

### EXCLUSION CRITERIA:
* Do NOT extract Mechanical, Thermal, or Optical properties.

### OUTPUT SCHEMA (JSON Array of Objects):
Return a valid JSON array only. Example:
[
    {
        "doi": "10.1021/Example",
        "file_path": null,
        "polymer_name": "PI-1a",
        "record_type": "Dielectric Constant (Dk)",
        "value": "3.12",
        "temperature": "25 °C",
        "frequency": "10 GHz",
        "test_standard": "IPC-TM-650",
        "test_method": "Cavity Perturbation",
        "test_conditions": "Thickness: 25 um, State: Dry film",
        "notes": "Low Dk polyimide"
    },
    {
        "doi": "10.1021/Example",
        "file_path": null,
        "polymer_name": "PI-1a",
        "record_type": "Dielectric Loss (Df / tan δ)",
        "value": "0.002",
        "temperature": "25 °C",
        "frequency": "10 GHz",
        "test_standard": "IPC-TM-650",
        "test_method": "Cavity Perturbation",
        "test_conditions": "Thickness: 25 um, State: Dry film",
        "notes": null
    },
    {
        "doi": "10.1021/Example",
        "file_path": null,
        "polymer_name": "PI-1a",
        "record_type": "Breakdown Strength",
        "value": "450 MV/m",
        "temperature": "Room Temperature",
        "frequency": "DC",
        "test_standard": "ASTM D149",
        "test_method": "Weibull distribution",
        "test_conditions": "Immersed in silicone oil, Thickness: 10 um",
        "notes": "Shape parameter beta = 12"
    },
    {
        "doi": "10.1021/Example",
        "file_path": null,
        "polymer_name": "PI-1a",
        "record_type": "Volume Resistivity",
        "value": "10^15 Ω·cm",
        "temperature": "Room Temperature",
        "frequency": "DC",
        "test_standard": null,
        "test_method": "High Resistance Meter",
        "test_conditions": "Sputtered gold electrodes",
        "notes": "Excellent insulation"
    }
]