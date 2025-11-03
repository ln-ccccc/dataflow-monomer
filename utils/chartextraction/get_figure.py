import os
from typing import Tuple, List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class ExtractedRegion:
    """Class to hold information about an extracted region."""
    file_path: str
    page_number: int
    width: int
    height: int
    dpi: int
    coordinates: Tuple[float, float, float, float]
    success: bool
    error_message: Optional[str] = None
    skipped: bool = False  # New field to indicate if extraction was skipped


def extract_pdf_region_to_png(
    pdf_path: str,
    output_png_path: str,
    page_num: int,
    percent_rect: Tuple[float, float, float, float],
    dpi: int = 300,
    doc: Optional[fitz.Document] = None,
    grayscale: bool = False,
    skip_existing: bool = True,  # New parameter to control skipping behavior
) -> ExtractedRegion:
    """
    Extract a specific region from a PDF page using percentage coordinates and save as PNG.

    Args:
        pdf_path (str): Path to the input PDF file
        output_png_path (str): Path where the output PNG file will be saved
        page_num (int): Page number to extract from (1-based indexing)
        percent_rect (tuple): Region coordinates as percentages (left, top, right, bottom)
        dpi (int, optional): Resolution for output image. Defaults to 300.
        doc (fitz.Document, optional): Open PDF document for batch processing. Defaults to None.
        grayscale (bool, optional): Whether to convert the output to grayscale. Defaults to False.
        skip_existing (bool, optional): Whether to skip extraction if output file exists. Defaults to True.

    Returns:
        ExtractedRegion: Object containing extraction information and results

    Raises:
        FileNotFoundError: If the input PDF file does not exist
        ValueError: If page_num is less than 1 or if percent_rect values are invalid
        RuntimeError: If the PDF file is corrupted or cannot be read
    """
    # Initialize result object
    result = ExtractedRegion(
        file_path=output_png_path,
        page_number=page_num,
        width=0,
        height=0,
        dpi=dpi,
        coordinates=percent_rect,
        success=False,
        skipped=False
    )

    try:
        # Check if output file already exists
        if skip_existing and os.path.exists(output_png_path):
            result.success = True
            result.skipped = True
            return result

        # Validate input PDF exists
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"Input PDF file not found: {pdf_path}")

        # Validate page number
        if page_num < 1:
            raise ValueError("Page number must be greater than 0")

        # Validate percent_rect values
        for value in percent_rect:
            if not 0 <= value <= 1:
                raise ValueError("Percent coordinates must be between 0 and 1")

        # Create output directory if it doesn't exist
        output_dir = os.path.dirname(output_png_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Handle document opening/closing
        should_close_doc = False
        try:
            if doc is None:
                doc = fitz.open(pdf_path)
                should_close_doc = True

            # Validate PDF is readable
            if not doc.is_pdf:
                raise RuntimeError("The file is not a valid PDF document")

            # Validate PDF is not encrypted
            if doc.is_encrypted:
                raise RuntimeError("The PDF file is encrypted and cannot be read")

            # Validate page number is within document bounds
            if page_num > len(doc):
                raise ValueError(f"Page number {page_num} exceeds document length of {len(doc)}")

            # Get the specified page (convert from 1-based to 0-based indexing)
            page = doc[page_num - 1]

            # Get the actual page dimensions
            page_width = page.rect.width
            page_height = page.rect.height

            # Update result with dimensions
            result.width = int(page_width)
            result.height = int(page_height)

            # Convert percentage coordinates to actual pixel coordinates
            left = percent_rect[0] * page_width
            top = percent_rect[1] * page_height
            right = percent_rect[2] * page_width
            bottom = percent_rect[3] * page_height
            clip_rect = fitz.Rect(left, top, right, bottom)

            # Render the page and crop to the specified region
            pix = page.get_pixmap(
                clip=clip_rect,
                dpi=dpi,
                alpha=False,  # No alpha channel needed for PNG
                colorspace=fitz.csGRAY if grayscale else fitz.csRGB
            )

            # Save the cropped region as PNG
            pix.save(output_png_path)
            result.success = True

        finally:
            # Clean up: close the document if we opened it
            if should_close_doc and doc:
                doc.close()

    except Exception as e:
        result.error_message = str(e)
        raise

    return result


def batch_extract_figures_and_components(
    pdf_path: str,
    output_dir: str,
    figures_info: List[Dict[str, Any]],
    dpi: int = 300,
    grayscale: bool = False,
    skip_existing: bool = True,
) -> List[ExtractedRegion]:
    """
    Extract figures and their components from a PDF file based on provided figure information.

    Args:
        pdf_path (str): Path to the input PDF file
        output_dir (str): Directory where output PNG files will be saved
        figures_info (List[Dict]): List of dictionaries containing figure information.
            Each dictionary should contain:
                - float_xyxy: List[float] - normalized coordinates [left, top, right, bottom] (0-1)
                - page: int - page number
                - components: List[Dict] - list of component dictionaries
        dpi (int, optional): Resolution for output images. Defaults to 300.
        grayscale (bool, optional): Whether to convert output to grayscale. Defaults to False.
        skip_existing (bool, optional): Whether to skip extraction if output file exists. Defaults to True.

    Returns:
        List[ExtractedRegion]: List of extraction results for all generated files

    Raises:
        FileNotFoundError: If the input PDF file does not exist
        ValueError: If figure information is invalid
        RuntimeError: If the PDF file is corrupted or cannot be read
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # List to store all extraction results
    extraction_results = []

    # Open the PDF document once for all extractions
    with fitz.open(pdf_path) as doc:
        # Process each figure
        for fig_idx, figure in enumerate(figures_info):
            page_num = figure["page"] + 1
            page_coords = figure["float_xyxy"]  # Already normalized coordinates (0-1)

            # Create output filename for main figure
            main_figure_path = os.path.join(output_dir, f"page{page_num-1}_fig{fig_idx + 1}_main.png")

            # Skip this figure if the file exists and skip_existing is True
            if skip_existing and os.path.exists(main_figure_path):
                result = ExtractedRegion(
                    file_path=main_figure_path,
                    page_number=page_num,
                    width=0,
                    height=0,
                    dpi=dpi,
                    coordinates=tuple(page_coords),
                    success=True,
                    skipped=True
                )
                extraction_results.append(result)
                continue

            try:

                # Use coordinates directly since they're already normalized
                percent_rect = tuple(page_coords)  # Convert list to tuple

                # Extract main figure
                result = extract_pdf_region_to_png(
                    pdf_path=pdf_path,
                    output_png_path=main_figure_path,
                    page_num=page_num,
                    percent_rect=percent_rect,
                    dpi=dpi,
                    doc=doc,
                    grayscale=grayscale,
                    skip_existing=skip_existing
                )
                extraction_results.append(result)
                
                # Only continue with components if main figure extraction was successful
                if result.success:
                    for index, comp in enumerate(figure["components"]):
                        comp_type = comp["class"]
                        if comp_type != "chart":
                            continue  # 只存为chart的
                        comp_coords = comp["float_xyxy"]  # Already normalized coordinates (0-1)
                        # Create component filename using simplified format
                        comp_filename = f"page{page_num-1}_fig{fig_idx + 1}_{index}_{comp_type}.png"
                        comp_path = os.path.join(output_dir, comp_filename)
                        
                        # Use coordinates directly since they're already normalized
                        comp_percent_rect = tuple(comp_coords)  # Convert list to tuple
                        
                        # Extract component
                        result = extract_pdf_region_to_png(
                            pdf_path=pdf_path,
                            output_png_path=comp_path,
                            page_num=page_num,
                            percent_rect=comp_percent_rect,
                            dpi=dpi,
                            doc=doc,
                            grayscale=grayscale,
                            skip_existing=skip_existing
                        )
                        extraction_results.append(result)

            except Exception as e:
                print(f"Error processing figure {fig_idx + 1} on page {page_num}: {str(e)}")
                continue

    return extraction_results
