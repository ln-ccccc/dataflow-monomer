from dataflow.utils.registry import PROMPT_REGISTRY
from dataflow.core.prompt import PromptABC

@PROMPT_REGISTRY.register()
class ChartInfoExtractionPrompt(PromptABC):
    def __init__(self):
        pass

    def get_json_schema(self) -> dict:
        return {
        "type": "object",
        "properties": {
            "x_label": {"type": "string"},
            "y_label": {"type": "string"},
            "x_tick_labels": {"type": "array", "items": {"type": "string"}},
            "y_tick_labels": {"type": "array", "items": {"type": "string"}},
            "legend_names": {"type": "array", "items": {"type": "string"}},
            "legend_shapes": {"type": "array", "items": {"type": "string"}},
            "legend_colors": {"type": "array", "items": {"type": "string"}},
            "text": {"type": "array", "items": {"type": "string"}},
            "figure_type": {
                "type": "string",
                "enum": ["line", "curve", "scatter", "dual_y_axis", "bar", "box", "heatmap", "histogram", "other"]
            }
        },
        "required": ["x_label", "y_label", "x_tick_labels", "y_tick_labels", 
                    "legend_names", "legend_shapes", "legend_colors", "text", "figure_type"],
        "additionalProperties": False
        }
    
    def build_prompt(self) -> str:
        prompt = """
            Given the following chart image, please extract and output the following information in structured JSON format
            (if any item cannot be identified, return an empty string or empty list):
            1. The x-axis label (string, use LaTeX syntax)
            2. The y-axis label (string, use LaTeX syntax)
            3. The tick labels of the x-axis (list of strings, use LaTeX syntax)
            4. The tick labels of the y-axis (list of strings, use LaTeX syntax)
            5. The legend names (list of strings, use LaTeX syntax)
            6. The legend marker shapes (list of strings, e.g., 'circle', 'square', 'line', etc.)
            7. The legend marker colors (list of strings, should be 16-bit hex color code, e.g., '#FF0000', '#0000FF', etc.)
            8. The text in the figure (string, use LaTeX syntax)
            9. The type of the figure (string, one of the following: line, curve, scatter, dual_y_axis, bar, box, heatmap, histogram. Other if not sure)

            Output format (JSON):
            {
                "x_label": "...",
                "y_label": "...",
                "x_tick_labels": [...],
                "y_tick_labels": [...],
                "legend_names": [...],
                "legend_shapes": [...],
                "legend_colors": [...],
                "text": [...],
                "figure_type": "..."
            }
            
            Output only the JSON object, no other text.
        """
        return prompt.strip()