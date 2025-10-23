import argparse
import json
import os
from typing import Iterator


def iter_png_files(root_dir: str) -> Iterator[str]:
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(".png"):
                yield os.path.abspath(os.path.join(dirpath, filename))


def iter_pdf_files(root_dir: str) -> Iterator[str]:
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(".pdf"):
                yield os.path.abspath(os.path.join(dirpath, filename))


def write_jsonl(input_root: str, out_jsonl: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_jsonl)), exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        # Write PNG entries
        for png_path in iter_png_files(input_root):
            record = {
                # FigureInfoGenerator 支持当 pdf_path 为 PNG 路径时跳过 PDF 提取
                "input_path": png_path,
                # PNG 路径场景不需要 parser，但为对齐示例字段保留该键
                "uniparser_json": "",
                # 缺省时下游会使用 PNG 所在目录作为输出
                "output_dir": os.path.dirname(png_path),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Write PDF entries
        for pdf_path in iter_pdf_files(input_root):
            record = {
                "input_path": pdf_path,
                "uniparser_json": os.path.splitext(pdf_path)[0] + "_uniparser.json",
                # 缺省时下游会使用 PDF 所在目录作为输出
                "output_dir": os.path.dirname(pdf_path),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate JSONL listing PNG and PDF files for chart extraction pipeline")
    parser.add_argument("input_dir", help="Directory containing PNG/PDF files (recursively scanned)")
    parser.add_argument("out_jsonl", help="Output JSONL file path")
    args = parser.parse_args()

    write_jsonl(args.input_dir, args.out_jsonl)


if __name__ == "__main__":
    main()

