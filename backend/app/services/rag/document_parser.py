import os
import re
import logging
from typing import Dict, Any, List
from app.services.rag.mineru_adapter import mineru_adapter

logger = logging.getLogger(__name__)


class UnifiedDocumentParser:
    """
    Unified Multi-Format Educational Document Parser
    Supports:
    1. PDF (.pdf): Layout analysis with MinerU / fallback, formula ($/$$) & table preservation.
    2. Word (.docx, .doc): Paragraph styles (H1/H2/H3/Bullets), tables to GFM Markdown tables, math formulas.
    3. Markdown (.md) & Plain Text (.txt): Direct structural parsing and header tree extraction.
    """

    @classmethod
    def parse_file(cls, file_path: str, output_dir: str = "./temp_uploads") -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return cls._parse_pdf(file_path, output_dir)
        elif ext in [".docx", ".doc"]:
            return cls._parse_docx(file_path)
        elif ext in [".md", ".markdown"]:
            return cls._parse_markdown(file_path)
        elif ext in [".txt"]:
            return cls._parse_text(file_path)
        else:
            # Fallback text reader
            return cls._parse_text(file_path)

    @classmethod
    def _parse_pdf(cls, file_path: str, output_dir: str) -> Dict[str, Any]:
        """Parses PDF via MinerU layout adapter."""
        result = mineru_adapter.parse_pdf(file_path, output_dir)
        result["doc_type"] = "PDF"
        return result

    @classmethod
    def _parse_docx(cls, file_path: str) -> Dict[str, Any]:
        """Parses Word .docx document preserving headings, lists, tables and LaTeX."""
        try:
            from docx import Document
            doc = Document(file_path)
        except Exception as e:
            logger.error(f"[DocumentParser] Error reading docx: {e}")
            return cls._parse_text(file_path)

        md_lines = []
        formula_count = 0
        table_count = 0

        # Read document elements (paragraphs & tables)
        for element in doc.element.body:
            # Check if element is a paragraph
            if element.tag.endswith('p'):
                for p in doc.paragraphs:
                    if p._element == element:
                        text = p.text.strip()
                        if not text:
                            continue

                        # Detect LaTeX in text
                        formula_count += len(re.findall(r"\$[^$]+\$", text))

                        style_name = p.style.name.lower()
                        if "heading 1" in style_name:
                            md_lines.append(f"\n# {text}\n")
                        elif "heading 2" in style_name:
                            md_lines.append(f"\n## {text}\n")
                        elif "heading 3" in style_name:
                            md_lines.append(f"\n### {text}\n")
                        elif "list" in style_name or "bullet" in style_name:
                            md_lines.append(f"- {text}")
                        else:
                            md_lines.append(f"\n{text}\n")
                        break

            # Check if element is a table
            elif element.tag.endswith('tbl'):
                for table in doc.tables:
                    if table._element == element:
                        table_count += 1
                        table_md = cls._convert_docx_table_to_md(table)
                        if table_md:
                            md_lines.append("\n" + table_md + "\n")
                        break

        full_markdown = "\n".join(md_lines)
        if not full_markdown.strip():
            # If empty, fallback to simple text extraction
            full_markdown = "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])

        sections = cls._extract_sections(full_markdown)

        # Estimate page count: roughly 500 characters per standard A4 Word page
        char_count = len(full_markdown)
        estimated_pages = max(1, char_count // 500 + (1 if char_count % 500 > 0 else 0))

        return {
            "markdown_content": full_markdown,
            "page_count": estimated_pages,
            "sections": sections,
            "formula_count": formula_count or len(re.findall(r"\$[^$]+\$", full_markdown)),
            "table_count": table_count,
            "doc_type": "WORD"
        }

    @classmethod
    def _convert_docx_table_to_md(cls, table) -> str:
        """Converts docx.table to standard GitHub Flavored Markdown table."""
        rows = table.rows
        if not rows:
            return ""

        lines = []
        # Header row
        header_cells = [cell.text.replace("\n", " ").strip() for cell in rows[0].cells]
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("| " + " | ".join(["---"] * len(header_cells)) + " |")

        # Data rows
        for row in rows[1:]:
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    @classmethod
    def _parse_markdown(cls, file_path: str) -> Dict[str, Any]:
        """Parses Markdown file directly."""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        formula_count = len(re.findall(r"\$[^$]+\$", content)) + content.count("$$") // 2
        table_count = content.count("|---")
        sections = cls._extract_sections(content)
        pages = max(1, len(content) // 500)

        return {
            "markdown_content": content,
            "page_count": pages,
            "sections": sections,
            "formula_count": formula_count,
            "table_count": table_count,
            "doc_type": "MARKDOWN"
        }

    @classmethod
    def _parse_text(cls, file_path: str) -> Dict[str, Any]:
        """Parses Plain Text file."""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        pages = max(1, len(content) // 500)
        return {
            "markdown_content": content,
            "page_count": pages,
            "sections": [{"level": 1, "title": os.path.basename(file_path)}],
            "formula_count": len(re.findall(r"\$[^$]+\$", content)),
            "table_count": 0,
            "doc_type": "TXT"
        }

    @classmethod
    def _extract_sections(cls, markdown: str) -> List[Dict[str, Any]]:
        sections = []
        for line in markdown.splitlines():
            line_s = line.strip()
            if line_s.startswith("#"):
                match = re.match(r"^(#+)\s+(.+)$", line_s)
                if match:
                    sections.append({
                        "level": len(match.group(1)),
                        "title": match.group(2).strip()
                    })
        return sections


unified_document_parser = UnifiedDocumentParser()
