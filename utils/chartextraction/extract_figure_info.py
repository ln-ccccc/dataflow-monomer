from typing import List, Dict, Any, Tuple
from functools import cmp_to_key


def compare_boxes(a: Dict[str, Any], b: Dict[str, Any], top_thresh: float = 0.05) -> int:
    """
    Compare two boxes by their position (left-to-right, top-to-bottom).
    
    Args:
        a: Dict containing 'float_xyxy' key with [left, top, right, bottom] coordinates
        b: Dict containing 'float_xyxy' key with [left, top, right, bottom] coordinates
        top_thresh: float - threshold for considering boxes to be in the same row
        
    Returns:
        int: -1 if a comes before b, 1 if b comes before a, 0 if equal
    """
    box_a = a["float_xyxy"]
    box_b = b["float_xyxy"]
    top_a, left_a = box_a[1], box_a[0]
    top_b, left_b = box_b[1], box_b[0]
    
    if abs(top_a - top_b) < top_thresh:
        # Same row, sort by left coordinate
        return -1 if left_a < left_b else (1 if left_a > left_b else 0)
    else:
        # Different rows, sort by top coordinate
        return -1 if top_a < top_b else 1


def sort_components(components: List[Dict[str, Any]], top_thresh: float = 0.05) -> List[Dict[str, Any]]:
    """
    Sort components by their position (left-to-right, top-to-bottom).
    
    Args:
        components: List of component dictionaries
        top_thresh: float - threshold for considering components to be in the same row
        
    Returns:
        List[Dict[str, Any]]: Sorted list of components
    """
    return sorted(components, key=cmp_to_key(lambda a, b: compare_boxes(a, b, top_thresh)))


def is_box_inside(inner_box: List[float], outer_box: List[float], tolerance: float = 0.0) -> bool:
    """
    Check if one bounding box is inside another bounding box.
    
    Args:
        inner_box: List[float] - [left, top, right, bottom] coordinates of inner box
        outer_box: List[float] - [left, top, right, bottom] coordinates of outer box
        tolerance: float - tolerance value for containment check (default: 0.0)
        
    Returns:
        bool: True if inner_box is inside outer_box (with tolerance), False otherwise
    """
    return (inner_box[0] >= outer_box[0] - tolerance and  # left
            inner_box[1] >= outer_box[1] - tolerance and  # top
            inner_box[2] <= outer_box[2] + tolerance and  # right
            inner_box[3] <= outer_box[3] + tolerance)     # bottom


def group_figures(parser_result: dict) -> List[Dict[str, Any]]:
    """
    Group figures and captions from a JSON file containing OCR data.
    
    Args:
        parser_result (dict): Parser result containing OCR data
        
    Returns:
        List[Dict[str, Any]]: List of dictionaries containing figure information.
        Each dictionary contains:
            - bounding_box: List[float] - coordinates [left, top, right, bottom]
            - caption: str - figure caption text
            - page: int - page number (1-based)
    """
    # Load and parse JSON data
    data = parser_result["objects"]
    
    # Separate figures and captions
    figures = []
    captions = []
    for obj in data:
        if obj["class"] in ["chart", "figure"]:
            figures.append(obj)
        elif obj["class"] == "caption":
            captions.append(obj)
    
    # Map figures to their nearest captions
    figures_info = {}
    for index, figure in enumerate(figures):
        page = figure["page"]
        bottom = figure["float_xyxy"][3]
        distance = 1
        possible_caption = {
            "class": "caption",
            "confidence": 0,
            "float_xyxy": [1, 1, -1, -1],
            "page": page,
            "str": "No caption found.",
            "id": ""
        }
        
        # Find nearest caption below the figure
        for caption in captions:
            if caption["page"] != page:
                continue
            if caption["float_xyxy"][1] < bottom:
                continue
            if caption["float_xyxy"][1] - bottom < distance:
                distance = caption["float_xyxy"][1] - bottom
                possible_caption = caption
                
        # If caption is too short, try to find next caption
        other_captions = captions.copy()
        while len(possible_caption["str"]) < 50:
            try:
                other_captions.remove(possible_caption)
            except:
                break
            if not other_captions:
                break
                
            distance = 1
            for caption in other_captions:
                if caption["page"] != page:
                    continue
                if caption["float_xyxy"][1] < bottom:
                    continue
                if caption["float_xyxy"][1] - bottom < distance:
                    distance = caption["float_xyxy"][1] - bottom
                    possible_caption = caption
        
        figures_info[str(index)] = possible_caption
    
    # Deduplicate captions and merge associated figures
    grouped_figures = []
    unique_captions = []
    for caption in figures_info.values():
        if caption not in unique_captions:
            unique_captions.append(caption)
    
    # Process each unique caption
    for caption in unique_captions:
        # Find bounding box encompassing all figures with this caption
        left = caption["float_xyxy"][0]
        top = caption["float_xyxy"][1]
        right = caption["float_xyxy"][2]
        bottom = caption["float_xyxy"][3]
        
        # Merge bounding boxes of all figures with this caption
        for idx, figure in enumerate(figures):
            if figures_info[str(idx)] == caption:
                left = min(figure["float_xyxy"][0], left)
                top = min(figure["float_xyxy"][1], top)
                right = max(figure["float_xyxy"][2], right)
                bottom = max(figure["float_xyxy"][3], bottom)
        
        # Create figure info dictionary
        figure_info = {
            "float_xyxy": [left, top, right, bottom],
            "caption": caption["str"],
            "page": caption["page"]
        }
        
        grouped_figures.append(figure_info)
    
    return grouped_figures


def extract_figure_components(parser_result: dict) -> List[Dict[str, Any]]:
    """
    Extract figure information including their components from parser result.
    
    Args:
        parser_result (dict): Parser result containing OCR data
        
    Returns:
        List[Dict[str, Any]]: List of dictionaries containing figure information.
        Each dictionary contains:
            - bounding_box: List[float] - coordinates [left, top, right, bottom]
            - caption: str - figure caption text
            - page: int - page number
            - components: List[Dict] - list of component dictionaries, each containing:
                - type: str - component type (e.g., "text", "figure", "chart", "caption" etc.)
                - box: List[float] - component bounding box coordinates
                - text: str - OCR text result (if applicable)
    """
    # First get grouped figures
    grouped_figures = group_figures(parser_result)
    
    # Get all objects from parser result
    objects = parser_result["objects"]
    
    # For each grouped figure, find its components
    for figure in grouped_figures:
        components = []
        page = figure["page"]
        figure_box = figure["float_xyxy"]
        
        # Check each object in the parser result
        for obj in objects:
            # Skip if not on the same page
            if obj["page"] != page:
                continue
                
            # Check if object is inside the figure box
            if is_box_inside(obj["float_xyxy"], figure_box, tolerance=0.01):
                components.append(obj)
        
        # Sort components left-to-right, top-to-bottom
        components = sort_components(components)
        
        # Add components to the figure info
        figure["components"] = components
    
    return grouped_figures