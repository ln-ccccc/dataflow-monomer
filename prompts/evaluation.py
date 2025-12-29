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
