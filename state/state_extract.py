"""从 docx 提取表格和图片的子状态机"""

import zipfile
from pathlib import Path
from typing import List, Tuple
from xml.etree import ElementTree
import threading
from docx import Document
from common import GlobalFunction
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
        self._done = False
        self._thread = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._extract_tables,daemon=True)
        self._thread.start()

    def _extract_tables(self) -> List[DocxTable]:
        try:
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

            WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

            tables: List[DocxTable] = []
            for index, table in enumerate(document.tables, start=1):
                # python-docx 会将合并单元格展开为多个 cell（共享同一个 _tc 元素），
                # 需要按 _tc 去重后读取真实的 gridSpan / vMerge 构建网格
                grid: List[List[dict]] = []
                total_cols = 0

                for row in table.rows:
                    row_data: List[dict] = []
                    seen_tc = set()

                    for cell in row.cells:
                        tc = cell._tc
                        tc_id = id(tc)
                        if tc_id in seen_tc:
                            continue
                        seen_tc.add(tc_id)

                        tcPr = tc.find(f"{{{WML_NS}}}tcPr")
                        grid_span = 1
                        v_merge = None  # None | 'restart' | 'continue'

                        if tcPr is not None:
                            gs = tcPr.find(f"{{{WML_NS}}}gridSpan")
                            if gs is not None:
                                grid_span = int(gs.get(f"{{{WML_NS}}}val", "1"))
                            vm = tcPr.find(f"{{{WML_NS}}}vMerge")
                            if vm is not None:
                                v_merge = vm.get(f"{{{WML_NS}}}val", "continue")

                        text = cell.text.strip() if v_merge != 'continue' else ""
                        row_data.append({"text": text, "span": grid_span, "v_merge": v_merge})

                    grid.append(row_data)
                    total_cols = max(total_cols, sum(c["span"] for c in row_data))

                # 展开为固定列数的二维数组
                expanded_rows: List[List[str]] = []
                for row_data in grid:
                    expanded: List[str] = []
                    for c in row_data:
                        expanded.append(c["text"])
                        for _ in range(c["span"] - 1):
                            expanded.append("")
                    while len(expanded) < total_cols:
                        expanded.append("")
                    expanded_rows.append(expanded)

                title = expanded_rows[0][0] if expanded_rows and expanded_rows[0] else f"表{index}"

                if expanded_rows:
                    markdown_lines = [
                        "| " + " | ".join(expanded_rows[0]) + " |",
                        "| " + " | ".join(["---"] * total_cols) + " |",
                    ]
                    for row in expanded_rows[1:]:
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
                        rows=expanded_rows,
                        markdown=markdown,
                    )
                )
            self._tables = tables
            return tables
        except Exception as e:
            GlobalFunction.log('提取表格线程出错：',f'错误为:{e}')
        finally:
            self._done = True

    def Execute(self, owner):
        if self._done:
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
        self._done = False
        self._thread = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._extract_images,daemon=True)
        self._thread.start()

    def _extract_images(self) -> List[DocxImage]:
        try:
            images: List[DocxImage] = []
            with zipfile.ZipFile(Path(self._docx_path)) as archive:
                rels_xml = ElementTree.fromstring(
                    archive.read("word/_rels/document.xml.rels")
                )
                media_map: dict[str, str] = {}
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
            self._images = images
            return images
        except Exception as e:
            GlobalFunction.log('提取图片线程出错',f'错误为：{e}')
        finally:
            self._done = True

    def Execute(self, owner):
        if self._done:
            owner.remove_state(self)

    def Exit(self, owner):
        pass


__all__ = ["ExtractDocxTablesState", "ExtractDocxImagesState"]
