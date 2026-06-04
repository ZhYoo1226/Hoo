"""从 docx 提取表格和图片的子状态机"""

import zipfile
from pathlib import Path
from typing import List, Tuple
from xml.etree import ElementTree

from docx import Document

from .base_state import BaseState
from .state_doc import (
    REL_NAMESPACE,
    DocxImage,
    DocxTable,
    _sanitize_name,
    get_media_content_types,
)


# --------------------------------------------------------------------------
# ExtractDocxTablesState
# --------------------------------------------------------------------------

class ExtractDocxTablesState(BaseState):
    """从 docx 提取表格"""
    stateName = "ExtractDocxTablesState"

    def __init__(self, docx_path: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path

    def Enter(self, owner):
        self._tables = self._extract_tables()

    def _extract_tables(self) -> List[DocxTable]:
        document = Document(self._docx_path)
        docx_file = Path(self._docx_path)

        with zipfile.ZipFile(docx_file) as archive:
            document_xml = ElementTree.fromstring(archive.read("word/document.xml"))

        table_positions: List[int] = []
        body = document_xml.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body"
        )
        offset = 0
        if body is not None:
            for child in body:
                tag = child.tag.rsplit("}", 1)[-1]
                text_length = sum(
                    len(node.text)
                    for node in child.findall(
                        ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                    )
                    if node.text
                )
                if tag == "tbl":
                    table_positions.append(offset)
                offset += text_length

        tables: List[DocxTable] = []
        for index, table in enumerate(document.tables, start=1):
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            title = rows[0][0] if rows and rows[0] and rows[0][0] else f"表{index}"
            normalized_rows = [
                [cell.replace("\n", " ").strip() for cell in row] for row in rows
            ]
            if normalized_rows:
                column_count = max(len(row) for row in normalized_rows)
                normalized_rows = [
                    row + [""] * (column_count - len(row)) for row in normalized_rows
                ]
                markdown_lines = [
                    "| " + " | ".join(normalized_rows[0]) + " |",
                    "| " + " | ".join(["---"] * column_count) + " |",
                ]
                for row in normalized_rows[1:]:
                    markdown_lines.append("| " + " | ".join(row) + " |")
                markdown = "\n".join(markdown_lines)
            else:
                markdown = ""
            tables.append(
                DocxTable(
                    index=index,
                    position=table_positions[index - 1]
                    if index - 1 < len(table_positions)
                    else 0,
                    title=_sanitize_name(title),
                    rows=rows,
                    markdown=markdown,
                )
            )
        return tables

    def Execute(self, owner):
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# ExtractDocxImagesState
# --------------------------------------------------------------------------

class ExtractDocxImagesState(BaseState):
    """从 docx 提取图片"""
    stateName = "ExtractDocxImagesState"

    def __init__(self, docx_path: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path

    def Enter(self, owner):
        self._images = self._extract_images()

    def _extract_images(self) -> List[DocxImage]:
        images: List[DocxImage] = []
        with zipfile.ZipFile(Path(self._docx_path)) as archive:
            rels_xml = ElementTree.fromstring(
                archive.read("word/_rels/document.xml.rels")
            )
            media_map: Dict[str, str] = {}
            for relationship in rels_xml.findall("r:Relationship", REL_NAMESPACE):
                rel_id = relationship.attrib.get("Id")
                target = relationship.attrib.get("Target", "")
                if rel_id and target.startswith("media/"):
                    media_map[rel_id] = f"word/{target}"

            document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
            body = document_xml.find(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body"
            )
            image_positions: List[Tuple[str, int]] = []
            offset = 0
            if body is not None:
                for child in body:
                    tag = child.tag.rsplit("}", 1)[-1]
                    for drawing in child.findall(
                        ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
                    ):
                        for blip in drawing.findall(
                            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
                        ):
                            rel_id = blip.attrib.get(
                                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                            )
                            if rel_id:
                                image_positions.append((rel_id, offset))
                    text_length = sum(
                        len(node.text)
                        for node in child.findall(
                            ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                        )
                        if node.text
                    )
                    if tag in {"p", "tbl"}:
                        offset += text_length

            for index, (rel_id, pos) in enumerate(image_positions, start=1):
                if rel_id not in media_map:
                    continue
                media_path = media_map[rel_id]
                media_name = Path(media_path).name
                ext = Path(media_name).suffix.lower()
                if ext not in get_media_content_types():
                    continue
                images.append(
                    DocxImage(
                        index=index,
                        rel_id=rel_id,
                        media_name=media_name,
                        extension=ext,
                        position=pos,
                        blob=archive.read(media_path),
                    )
                )
        return images

    def Execute(self, owner):
        owner.remove_state(self)

    def Exit(self, owner):
        pass


__all__ = ["ExtractDocxTablesState", "ExtractDocxImagesState"]
