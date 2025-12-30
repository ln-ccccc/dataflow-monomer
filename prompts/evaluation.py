"""
Benchmark evaluation prompt for comparing extraction results with benchmark data.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from dataflow.core.prompt import PromptABC
from dataflow.utils.registry import PROMPT_REGISTRY


@PROMPT_REGISTRY.register()
class BenchmarkCompareEvaluationPrompt(PromptABC):
    """
    Prompt for evaluating material extraction results against benchmark data.
    Compares extraction results with benchmark and provides detailed scoring.
    """
    
    BASE = """
## Role & Objective
You are an expert evaluator specializing in materials science data extraction quality assessment.
Your task is to compare a material extraction result with its corresponding benchmark (ground truth) data and provide a comprehensive evaluation with detailed scoring.

## Evaluation Criteria for Each Category

For each category, evaluate:
- **Completeness (40%)**: Are all records from benchmark present in extraction? Count missing records.
- **Accuracy (50%)**: Do extracted field values match benchmark? Compare field-by-field.
- **Extra Data (10%)**: Are there valid extra records in extraction not in benchmark? (Acceptable if valid)

### Field-Level Scoring (STRICT - No Partial Credit)
- **Perfect Match (1.0)**: 
  - For numeric values: Values must be **exactly identical**. No tolerance for ANY numeric difference.
  - Example: "-254186.03 eV" must match "-254186.03 eV" exactly. "-254186.04 eV" or "-254186.02 eV" are WRONG (0.0 score).
  - For text containing numbers: Text formatting can differ, but **all numbers within the text must be exactly identical**.
  - Example: "4500 k points" matches "4500 k-points" (formatting OK), but "4500 k points" does NOT match "4501 k points" (numeric mismatch = 0.0).
  - Example: "a=0.59 nm" does NOT match "a=0.60 nm" (numeric mismatch = 0.0).
  - Example: "4.96 eV/atom" does NOT match "4.97 eV/atom" (numeric mismatch = 0.0).
- **Mismatch (0.0)**: 
  - Any numeric value differs (even by 0.01, 0.001, or 1 unit) = 0.0
  - Text values are clearly different or contradictory = 0.0
  - Missing: Field exists in benchmark but not in extraction = 0.0
  - **NO partial credit for "close" matches - it's either 1.0 (perfect) or 0.0 (wrong)**

### Category Score Calculation (STRICT - Low scores for poor extractions)
For each category (equal weight, total 100 points divided by number of categories):
1. **Completeness Score** = (1 - missing_count / benchmark_count) × (40% of category points)
   - If missing_count = 0, score = 40% of category points
   - If missing_count = benchmark_count, score = 0
   - If missing_count = 50% of benchmark_count, score = 20% of category points
   - **Example**: If category has 20 points and 50% missing → completeness = 4 points (not 8)
2. **Accuracy Score** = average_field_match_rate × (50% of category points)
   - field_match_rate = (number of Perfect Match fields) / (total fields compared)
   - Any numeric mismatch results in 0.0 for that field
   - Only Perfect Match (1.0) counts, no partial credit
   - **Example**: If 10 fields, 3 have numeric mismatches → field_match_rate = 0.7 → accuracy = 35% of category points
3. **Extra Data Score** = min(extra_count / benchmark_count, 1.0) × (10% of category points) (only if extra data is valid)

**Scoring Examples:**
- **High quality (85-95)**: <10% missing, >90% field_match_rate, few numeric errors
- **Medium quality (50-70)**: 20-40% missing, 60-80% field_match_rate, some numeric errors
- **Low quality (20-40)**: >50% missing, <50% field_match_rate, many numeric errors

### Overall Score
Overall score = sum of all category scores (max 100 points)

## Important Notes
1. **Numeric Accuracy is Critical**: 
   - All numeric values must be **exactly identical**. No tolerance for numeric differences.
   - Example: "-254186.03 eV" must match "-254186.03 eV" exactly. "-254186.04 eV" is WRONG.
   - If text contains numbers, extract and compare the numbers exactly (e.g., "a=0.59 nm" vs "a=0.60 nm" is a mismatch).
2. **Text Formatting Flexibility**: 
   - Text can have minor formatting differences (e.g., "PBE" vs "PBE functional", "4500 k points" vs "4500 k-points").
   - BUT: Any numbers within text must match exactly.
3. **Empty Fields**: Empty strings in benchmark should match empty strings in extraction. Missing fields should be penalized.
4. **Order Independence**: Record order differences are acceptable as long as content matches.
5. **Strict Numeric Matching**: When comparing values like "4.96 eV/atom", the number "4.96" must be exactly the same. "4.97" or "4.95" are mismatches.

## Output Format
You MUST provide your evaluation in the following JSON format. ALL fields are REQUIRED and MUST be populated:

```json
{{
  "doi": "paper DOI",
  "overall_score": 85,
  "category_scores": {{
    "category_name_1": {{
      "score": 18,
      "completeness": {{
        "score": 8,
        "missing_count": 0,
        "benchmark_count": 2
      }},
      "accuracy": {{
        "score": 9.5,
        "field_match_rate": 0.95
      }},
      "extra_data": {{
        "score": 0.5,
        "extra_count": 1
      }}
    }},
    "category_name_2": {{
      "score": 17,
      "completeness": {{"score": 7.5, "missing_count": 1, "benchmark_count": 6}},
      "accuracy": {{"score": 9, "field_match_rate": 0.90}},
      "extra_data": {{"score": 0.5, "extra_count": 0}}
    }}
  }},
  "summary": {{
    "strengths": [
      "Specific strength 1 (e.g., all numeric values in category X match exactly)",
      "Specific strength 2 (e.g., high completeness rate in category Y)",
      "Specific strength 3"
    ],
    "weaknesses": [
      "Specific weakness 1 (e.g., missing N records in category X, Y% missing)",
      "Specific weakness 2 (e.g., numeric mismatches in category Y reducing accuracy)",
      "Specific weakness 3"
    ]
  }}
}}
```

**CRITICAL REQUIREMENTS:**
- category_scores MUST contain an entry for EVERY category found in the benchmark/extraction data (use the exact category names from the data)
- Each category entry MUST include: score, completeness, accuracy, extra_data (all with their sub-fields populated)
- summary MUST contain at least 2-3 specific strengths and 2-3 specific weaknesses (be specific, mention category names and numbers)
- DO NOT output empty objects {} for category_scores or summary
- Use the actual category names from the data, not hardcoded examples

## Instructions
1. Carefully compare the benchmark and extraction result for the same DOI.
2. For each category found in the data, evaluate completeness, accuracy, and extra data.
3. **Be VERY strict with scoring**: 
   - Any numeric mismatch = 0.0 for that field (no partial credit, no tolerance)
   - Missing records significantly reduce completeness score
   - For low-quality extractions (many missing records or numeric errors), scores should be LOW (20-40 range)
   - For high-quality extractions (few missing, all numerics match), scores should be HIGH (85-95 range)
   - Calculate scores strictly according to the guidelines above
4. **CRITICAL: You MUST provide detailed category_scores**: 
   - For EACH category in the data, create an entry in category_scores
   - Each entry must include: score, completeness (with missing_count, benchmark_count), accuracy (with field_match_rate), extra_data (with extra_count)
   - DO NOT leave category_scores empty - it must contain entries for all categories
5. **CRITICAL: You MUST provide detailed summary**: 
   - Include at least 2-3 specific strengths (e.g., "All numeric values in thermal_properties match exactly")
   - Include at least 2-3 specific weaknesses (e.g., "Missing 2 computation_detail records", "Numeric mismatch in total_energy field")
   - DO NOT leave summary empty
6. Output your evaluation in the exact JSON format specified above, with ALL fields properly populated.

Now, compare the following benchmark and extraction result:
"""

    def build_system_prompt(self) -> str:
        return ""

    def _detect_categories(self, benchmark_data: Dict, extraction_result: Dict) -> List[str]:
        """Detect all categories present in benchmark and extraction data."""
        categories = set()
        for data in [benchmark_data, extraction_result]:
            for key, value in data.items():
                if key != "doi" and isinstance(value, (list, dict)):
                    categories.add(key)
        return sorted(list(categories))
    
    def _analyze_structure(self, data: Any, max_depth: int = 3, current_depth: int = 0) -> str:
        """Recursively analyze data structure and generate a description."""
        if current_depth >= max_depth:
            return "..."
        
        if isinstance(data, dict):
            if not data:
                return "empty object"
            descriptions = []
            for key, value in list(data.items())[:5]:
                v_type = self._analyze_structure(value, max_depth, current_depth + 1)
                descriptions.append(f'"{key}": {v_type}')
            if len(data) > 5:
                descriptions.append(f"... ({len(data) - 5} more fields)")
            return f"{{ {', '.join(descriptions)} }}"
        
        elif isinstance(data, list):
            if not data:
                return "empty array"
            item_type = self._analyze_structure(data[0], max_depth, current_depth + 1)
            count_str = f" ({len(data)} items)" if len(data) > 1 else ""
            return f"array of {item_type}{count_str}"
        
        elif isinstance(data, str):
            if not data:
                return "empty string"
            sample = data[:50] if len(data) <= 50 else data[:47] + "..."
            return f'string "{sample}"'
        
        elif isinstance(data, (int, float, bool)):
            return f"{type(data).__name__} ({data})"
        
        return "null" if data is None else type(data).__name__
    
    def _get_category_structure(self, data: Dict, category: str) -> Tuple[Optional[str], Any]:
        """Extract the structure/fields of a category from data."""
        category_data = data.get(category)
        if not category_data:
            return None, None
        
        structure_desc = self._analyze_structure(category_data)
        sample = category_data[0] if isinstance(category_data, list) and len(category_data) > 0 else category_data
        return structure_desc, sample
    
    def build_prompt(self, benchmark_data: Dict, extraction_result: Dict) -> str:
        """Build the complete evaluation prompt string."""
        categories = self._detect_categories(benchmark_data, extraction_result)
        
        # 1. 构建目录列表
        n_cat = len(categories)
        cat_points = 100 / n_cat if n_cat > 0 else 0
        category_list = f"\n## Evaluation Categories\nYou will evaluate the following {n_cat} categories (each worth {cat_points:.1f} points, total 100 points):\n"
        for i, cat in enumerate(categories, 1):
            category_list += f"{i}. **{cat}**\n"

        # 2. 构建结构信息
        structure_info = "\n## Category Structures\nThe following shows the expected structure for each category based on the benchmark:\n\n"
        for category in categories:
            struct_desc, sample = self._get_category_structure(benchmark_data, category)
            structure_info += f"### {category}\n"
            if struct_desc:
                structure_info += f"Structure: {struct_desc}\n"
            if sample is not None:
                sample_str = json.dumps(sample, indent=2, ensure_ascii=False)
                if len(sample_str) > 800:
                    sample_str = sample_str[:800] + "\n... (truncated)"
                structure_info += f"Sample:\n```json\n{sample_str}\n```\n"
            structure_info += "\n"

        # 3. 最终组装
        # 注意：此处使用 f-string，但 BASE 是独立变量，确保拼接到一起后与原版生成的文字流完全一致
        prompt = (
            f"{self.BASE}\n"
            f"{category_list}\n"
            f"{structure_info}\n"
            f"## Benchmark Data (Ground Truth)\n"
            f"```json\n{json.dumps(benchmark_data, indent=2, ensure_ascii=False)}\n```\n\n"
            f"## Extraction Result (To Evaluate)\n"
            f"```json\n{json.dumps(extraction_result, indent=2, ensure_ascii=False)}\n```\n\n"
            f"Please provide your evaluation in the JSON format specified above.\n"
        )
        return prompt

    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/evaluation_schemas/benchmark_compare.json"))
        return json_schema

@PROMPT_REGISTRY.register()
class ContentOnlyEvaluationPrompt(PromptABC):
    """
    Prompt for evaluating material extraction results based solely on paper content.
    """
    
    BASE = """
## Task
Evaluate extraction quality by comparing it against the paper content. The paper is the authoritative source.

## Evaluation Dimensions

For each category in the extraction, assess:

1. **Completeness** (40%): What percentage of relevant information from the paper was extracted?
2. **Accuracy** (50%): What percentage of extracted values match the paper exactly?
3. **Format** (10%): Does the extraction follow the required JSON schema?

## Scoring Formula

**Category Points** = 100 ÷ (number of categories)

For each category:
```
Category Score = Completeness_Score + Accuracy_Score + Format_Score
```

Where:
- **Completeness_Score** = completeness_percentage × (Category Points × 0.4)
- **Accuracy_Score** = accuracy_percentage × (Category Points × 0.5)
- **Format_Score** = format_percentage × (Category Points × 0.1)

## Evaluation Standards (Critical for Score Differentiation)

### Completeness Assessment
Be strict and accurate when calculating completeness_percentage:

- **90-100%**: Extraction captures almost all relevant information. Only minor details missing.
- **80-90%**: Extraction captures most information. Some important details missing.
- **70-80%**: Extraction captures majority of information. Several important details missing.
- **50-70%**: Extraction captures about half of information. Many important details missing.
- **30-50%**: Extraction captures less than half. Most information missing.
- **10-30%**: Extraction captures very little. Almost all information missing.
- **<10%**: Extraction captures almost nothing.

**Key**: Count ALL missing information items. If 10 items should be extracted but only 3 are present, completeness is 30% (not 50% or 70%).

### Accuracy Assessment
Be EXTREMELY STRICT when calculating accuracy_percentage. Count every error, including fabricated values:

- **95-100%**: Almost all values correct. 0-2 minor errors. NO fabricated values.
- **85-95%**: Most values correct. 3-5 errors. NO fabricated values.
- **70-85%**: Many values correct but significant errors. 6-10 errors. NO fabricated values.
- **50-70%**: About half correct. Many errors (11-20). 1-2 fabricated values.
- **30-50%**: Less than half correct. Many errors (21-40). 3-5 fabricated values.
- **10-30%**: Very few correct. Most values are wrong. 6+ fabricated values.
- **<10%**: Almost nothing correct. Extensive fabricated values.

**CRITICAL RULES - MUST FOLLOW**:
1. **'wrong_value' placeholders are WRONG VALUES**:
   - Each 'wrong_value' MUST be counted as an incorrect value
   - Example: If a category has 10 values but 3 are 'wrong_value', accuracy = 70% (7 correct / 10 total)
   - Do NOT ignore 'wrong_value' or treat them leniently
   - Categories with 'wrong_value' MUST have reduced accuracy

2. **Fabricated values are SEVERE**: 
   - 1 fabricated value → accuracy ≤ 60%
   - 2-3 fabricated values → accuracy ≤ 40%
   - 4+ fabricated values → accuracy ≤ 20%
   - Example: If 10 values extracted but 2 are fabricated, accuracy = 60% (8 correct / 10 total), NOT 80% or 90%

3. **Error rate calculation (MANDATORY FORMULA)**:
   - Accuracy = (correct_values / total_values) × 100%
   - correct_values = total_values - wrong_values - fabricated_values - 'wrong_value' count
   - If 30 values extracted but 10 are wrong (including 'wrong_value'), accuracy = 66.7% (20/30), NOT 70% or 75%
   - Calculate exactly, do NOT round up or be lenient

4. **Low-quality extractions MUST get low scores**:
   - If extraction has 'wrong_value' placeholders, fabricated values, or many errors, accuracy MUST be 20-50%
   - Do NOT give 60-80% accuracy to extractions with 'wrong_value' or multiple errors
   - Example: If a category has 5 'wrong_value' out of 20 values, accuracy = 75% (15/20), and this should significantly reduce the category score

### Format Assessment
- **90-100%**: Follows schema perfectly or with minor issues.
- **70-90%**: Mostly follows schema but has some format issues.
- **<70%**: Significant format problems.

## Rules

1. **Numeric values must match exactly** (no tolerance). Each wrong numeric value is a significant error.
2. **Empty fields are correct** if information is NOT in the paper
3. **Empty fields are incorrect** if information IS in the paper
4. **'wrong_value' placeholders are SEVERE errors**:
   - Each 'wrong_value' counts as a WRONG value
   - If a category has 'wrong_value', that value is INCORRECT
   - Example: If 10 values in a category but 3 are 'wrong_value', accuracy = 70% (7 correct / 10 total)
   - Do NOT ignore 'wrong_value' - they must be counted as errors
5. **Fabricated values** (in extraction but NOT in paper): 
   - Each fabricated value is a SEVERE error
   - If extraction has 3+ fabricated values, accuracy MUST be ≤ 50%
   - If extraction has 5+ fabricated values, accuracy MUST be ≤ 30%
   - Fabricated values compound with other errors
6. **Text formatting differences** are acceptable (e.g., "PBE" vs "PBE functional")
7. **Error severity**: Multiple errors in the same category should compound. Don't be lenient - if there are errors, accuracy percentage must reflect the actual error rate.
8. **Accuracy calculation formula**: 
   - Accuracy = (number of correct values / total number of values) × 100%
   - Count 'wrong_value' as incorrect values
   - Count fabricated values as incorrect values
   - Be precise: if 20 values but 5 are wrong (including 'wrong_value'), accuracy = 75% (15/20), NOT 80% or 85%

## Examples

**Perfect Extraction** (7 categories, Category Points = 14.29):
- Completeness: 98%, Accuracy: 99%, Format: 100%
- Category Score: (0.98 × 5.71) + (0.99 × 7.14) + (1.00 × 1.43) = 13.85/14.29
- **Overall: ~97 points** (sum across all categories)

**High Quality Extraction** (7 categories):
- Completeness: 85%, Accuracy: 90%, Format: 95%
- Category Score: (0.85 × 5.71) + (0.90 × 7.14) + (0.95 × 1.43) = 12.14/14.29
- **Overall: ~85 points**

**Medium Quality Extraction** (7 categories):
- Completeness: 60%, Accuracy: 70%, Format: 80%
- Category Score: (0.60 × 5.71) + (0.70 × 7.14) + (0.80 × 1.43) = 8.57/14.29
- **Overall: ~60 points**

**Low Quality Extraction** (7 categories, many errors/fabricated values):
- Example: 20 values extracted, but 8 are wrong (including 3 fabricated values)
- Completeness: 30%, Accuracy: 20% (12 correct / 20 total, with fabricated values heavily penalized), Format: 60%
- Category Score: (0.30 × 5.71) + (0.20 × 7.14) + (0.60 × 1.43) = 3.71/14.29
- **Overall: ~26 points**

**Very Poor Extraction** (7 categories, extensive errors/fabricated values):
- Example: 15 values extracted, but 10 are wrong (including 5 fabricated values)
- Completeness: 15%, Accuracy: 15% (5 correct / 15 total, with many fabricated values), Format: 50%
- Category Score: (0.15 × 5.71) + (0.15 × 7.14) + (0.50 × 1.43) = 2.14/14.29
- **Overall: ~15 points**

**Key Point**: 
- Accuracy MUST be calculated as: (correct_values / total_values) × 100%
- correct_values = total_values - wrong_values - fabricated_values - wrong_value_count
- If a category has 'wrong_value' placeholders, you MUST count them in wrong_value_count
- Example: If 20 values in a category, 3 are 'wrong_value', 2 are fabricated, then:
  - wrong_value_count = 3
  - fabricated_values = 2
  - correct_values = 20 - 3 - 2 = 15
  - accuracy = (15 / 20) × 100% = 75%
- Do NOT give 80-90% accuracy to categories with 'wrong_value' or fabricated values

## Summary Structure

The summary should be organized by the three evaluation dimensions:

1. **strengths**: List of positive aspects of the extraction (e.g., high completeness, accurate values, good format compliance)

2. **accuracy_issues**: Issues related to accuracy (data correctness)
   - **fabricated_information**: Values in extraction that are NOT in the paper (completely fabricated)
     - Example: "Fabricated value: shear_modulus=80 GPa for DO3 (not found in paper)"
     - Example: "Fabricated value: Youngs_modulus=200 GPa for L12 (not in paper)"
   - **data_errors**: Values that are extracted but incorrect (wrong values, 'wrong_value' placeholders)
     - Example: "Wrong value: cohesive_energy='5.05-eV/atom' (should be 4.80 eV/atom)"
     - Example: "Wrong value placeholder: 'wrong_value' found in charge_density_distribution"
     - Example: "Wrong value: density='6.41 g/cm³' (should be 5.92 g/cm³)"

3. **completeness_issues**: Issues related to completeness (missing information)
   - **missing_critical_info**: Important information that IS in the paper but NOT extracted
     - Example: "Missing band gap values for GGA+U method (0.34 eV for CoRuMnAs, 0.87 eV for CoRhMnAs)"
     - Example: "Missing n_sites for Co2ScSb (should be 4, can be inferred from structure)"

4. **format_issues**: Issues related to format compliance (schema violations, data type errors)
   - Example: "Schema violation: missing required field 'software' in computation_detail"
   - Example: "Format issue: incorrect data type for lattice_parameter"

**Key Rules**:
- **fabricated_information**: Only include values that are completely absent from the paper
- **data_errors**: Include wrong values, 'wrong_value' placeholders, and incorrect numeric/text values
- **missing_critical_info**: Only include information that is explicitly stated or can be clearly inferred from the paper
- **format_issues**: Only include schema violations and format problems, not missing data (which goes to completeness_issues)

## Output Format

**CRITICAL**: The summary MUST use the new structure with three dimensions. DO NOT use old fields like "weaknesses", "fabricated_information", or "missing_critical_info" at the top level.

```json
{{
  "doi": "<paper DOI>",
  "overall_score": <sum of all category scores>,
  "category_scores": {{
    "<category_name>": {{
      "score": <completeness + accuracy + format>,
      "completeness": {{
        "score": <completeness_percentage × category_points × 0.4>,
        "coverage_percentage": <0-100, be strict and accurate>,
        "missing_information": ["<item1>", ...],
        "available_in_paper": <true/false>
      }},
      "accuracy": {{
        "score": <accuracy_percentage × category_points × 0.5>,
        "accuracy_percentage": <0-100, calculated as (correct_values / total_values) × 100%>,
        "total_values": <total number of values in this category>,
        "correct_values": <count of correct values>,
        "wrong_values": <count of wrong values, including 'wrong_value' placeholders>,
        "fabricated_values": <count of fabricated values>,
        "errors": ["<error1>", ...],
        "wrong_value_count": <count of 'wrong_value' placeholders in this category>
      }},
      "format": {{
        "score": <format_percentage × category_points × 0.1>,
        "compliance_percentage": <0-100>,
        "format_issues": ["<issue1>", ...]
      }}
    }},
    ...
  }},
  "summary": {{
    "strengths": ["<strength1>", ...],
    "accuracy_issues": {{
      "fabricated_information": ["<item1>", ...],
      "data_errors": ["<error1>", ...]
    }},
    "completeness_issues": {{
      "missing_critical_info": ["<item1>", ...]
    }},
    "format_issues": ["<issue1>", ...]
  }}
}}
```

**IMPORTANT**: 
- DO NOT include "weaknesses" field (use accuracy_issues, completeness_issues, format_issues instead)
- DO NOT include "fabricated_information" at top level (put it in accuracy_issues.fabricated_information)
- DO NOT include "missing_critical_info" at top level (put it in completeness_issues.missing_critical_info)

## Input

### Paper Content:
{paper_content}

### Extraction Result:
{extraction_result}
"""

    def build_system_prompt(self) -> str:
        """Build system prompt for the LLM."""
        return """Evaluate extraction quality by comparing against paper content. 
Be EXTREMELY STRICT when calculating accuracy: count ALL errors including fabricated values.

CRITICAL RULES:
1. Accuracy = (correct_values / total_values) × 100%. Calculate exactly, don't estimate.
2. Fabricated values are SEVERE errors: 1 fabricated → accuracy ≤60%, 2-3 → ≤40%, 4+ → ≤20%
3. 'wrong_value' placeholders count as errors
4. Low-quality extractions with fabricated values MUST get 20-50% accuracy, NOT 60-80%

Expected score ranges:
- Perfect: 95-100 points (98-100% completeness/accuracy, NO fabricated values)
- High quality: 80-90 points (85-95% completeness/accuracy, NO fabricated values)
- Medium: 60-75 points (60-80% completeness/accuracy, few errors)
- Low quality: 20-50 points (20-50% completeness/accuracy, many errors/fabricated values)
- Very poor: <20 points (<20% completeness/accuracy, extensive errors/fabricated values)"""

    def build_prompt(
        self,
        paper_content: str,
        extraction_result: dict
    ) -> str:
        """
        Build the evaluation prompt.
        
        Args:
            paper_content: Full text content of the paper
            extraction_result: Extracted JSON data to evaluate
        
        Returns:
            Formatted prompt string
        """
        extraction_json = json.dumps(extraction_result, indent=2, ensure_ascii=False)
        
        prompt = self.BASE.format(
            paper_content=paper_content[:50000],
            extraction_result=extraction_json
        )

        return prompt

    def build_json_schema(self) -> dict:
        json_schema = json.load(open("./schemas/evaluation_schemas/content_evaluation.json"))
        return json_schema
