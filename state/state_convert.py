"""文档转换相关子状态机：pdf→docx、doc→docx、docx→全文、全文→分块"""

import io
import os
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from docx import Document
from pdf2docx import Converter

from common import GlobalFunction
from .base_state import BaseState
from .state_doc import (
    CHINESE_HEADING_PATTERN,
    HEADING_PATTERN,
    LIST_ITEM_PATTERN,
    NUMERIC_HEADING_PATTERN,
    TOC_STYLE_PATTERN,
    HeadingChunk,
    _sanitize_name,
)


# --------------------------------------------------------------------------
# Pdf2DocxState
# --------------------------------------------------------------------------

class Pdf2DocxState(BaseState):
    """pdf → docx 转换"""
    stateName = "Pdf2DocxState"

    def __init__(self, pdf_path: str, docx_path: str, **kwargs):
        super().__init__(**kwargs)
        self._duration = 10 * 60 * 1000
        self._pdf_path = pdf_path
        self._docx_path = docx_path
        self._thread: Optional[threading.Thread] = None
        self._done = False
        self._error: Optional[BaseException] = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            pdf_path = Path(self._pdf_path)
            docx_path = Path(self._docx_path)
            converter = Converter(str(pdf_path))
            try:
                converter.convert(str(docx_path))
            finally:
                converter.close()
        except BaseException as exc:
            self._error = exc
        finally:
            self._done = True

    def Execute(self, owner):
        if self._error is not None:
            GlobalFunction.log("转换错误", f"pdf → docx 转换失败: {self._error}")
            owner.remove_state(self)
            return
        if not self._done:
            return
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Doc2DocxState
# --------------------------------------------------------------------------

class Doc2DocxState(BaseState):
    """doc → docx 转换 (依赖 LibreOffice)"""
    stateName = "Doc2DocxState"

    def __init__(self, doc_path: str, docx_path: str, **kwargs):
        '''
        参数：
        doc_path: 源 doc 文件路径
        docx_path: 目标 docx 输出路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._duration = 10 * 60 * 1000
        self._doc_path = doc_path
        self._docx_path = docx_path
        self._thread: Optional[threading.Thread] = None
        self._done = False
        self._error: Optional[BaseException] = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            doc_path = Path(self._doc_path)
            docx_path = Path(self._docx_path)
            docx_path.parent.mkdir(parents=True, exist_ok=True)
            soffice = os.getenv("SOFFICE_PATH", "soffice")
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(docx_path.parent),
                    str(doc_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice doc2docx 转换失败: stdout={result.stdout}, stderr={result.stderr}"
                )
            generated_path = docx_path.parent / f"{doc_path.stem}.docx"
            if not generated_path.exists():
                raise RuntimeError(
                    f"LibreOffice 未生成 docx 文件: {generated_path}, stdout={result.stdout}, stderr={result.stderr}"
                )
            if generated_path.resolve() != docx_path.resolve():
                shutil.move(str(generated_path), str(docx_path))
        except BaseException as exc:
            self._error = exc
        finally:
            self._done = True

    def Execute(self, owner):
        if self._error is not None:
            GlobalFunction.log("转换错误", f"doc → docx 转换失败: {self._error}")
            owner.remove_state(self)
            return
        if not self._done:
            return
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Docx2TextState
# --------------------------------------------------------------------------

class Docx2TextState(BaseState):
    """从 docx 提取全文"""
    stateName = "Docx2TextState"

    def __init__(self, docx_path: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path

    def Enter(self, owner):
        with zipfile.ZipFile(io.BytesIO(Path(self._docx_path).read_bytes())) as archive:
            xml_content = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml_content)
        namespace = {
            "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        }
        texts: List[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            parts = [
                node.text
                for node in paragraph.findall(".//w:t", namespace)
                if node.text
            ]
            if parts:
                texts.append("".join(parts))
        self._full_text = "\n".join(texts)

    def Execute(self, owner):
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Text2ChunksState
# --------------------------------------------------------------------------

class Text2ChunksState(BaseState):
    """按标题层级切分文本"""
    stateName = "Text2ChunksState"

    def __init__(self, docx_path: str, full_text: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径（用于读取样式信息）
        full_text: 待切分的全文文本
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path
        self._full_text = full_text

    def Enter(self, owner):
        self._chunks = self._split_text_chunks()

    def _split_text_chunks(self) -> List[HeadingChunk]:
        document = Document(str(self._docx_path))
        blocks: List[Tuple[str, str]] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = getattr(paragraph.style, "name", "") or ""
            blocks.append((style_name, text))

        toc_heading_map: Dict[str, Tuple[int, str, str]] = {}
        for style_name, text in blocks:
            toc_match = TOC_STYLE_PATTERN.match(style_name)
            if toc_match is None:
                continue
            clean_text = text.split("\t", 1)[0].strip()
            parsed = self._parse_heading_text(clean_text)
            if parsed is None:
                continue
            _, heading_number, heading_title = parsed
            toc_heading_map[re.sub(r"\s+", "", clean_text)] = (
                int(toc_match.group(1)),
                heading_number,
                heading_title,
            )

        roots: List[HeadingChunk] = []
        stack: List[HeadingChunk] = []
        intro_lines: List[str] = []

        for style_name, text in blocks:
            if TOC_STYLE_PATTERN.match(style_name):
                continue

            normalized_key = re.sub(r"\s+", "", text.split("\t", 1)[0].strip())
            heading_info: Optional[Tuple[int, str, str]] = toc_heading_map.get(
                normalized_key
            )
            if heading_info is None and not LIST_ITEM_PATTERN.match(text.strip()):
                heading_match = HEADING_PATTERN.match(style_name)
                if heading_match is not None:
                    parsed = self._parse_heading_text(text)
                    if parsed is not None:
                        _, heading_number, heading_title = parsed
                        heading_info = (
                            int(heading_match.group(1)),
                            heading_number,
                            heading_title,
                        )
                    else:
                        heading_info = (
                            int(heading_match.group(1)),
                            _sanitize_name(text),
                            text,
                        )
                else:
                    heading_info = self._parse_heading_text(text)

            if heading_info is None:
                if stack:
                    stack[-1].content = f"{stack[-1].content}\n{text}".strip()
                else:
                    intro_lines.append(text)
                continue

            heading_level, heading_number, heading_title = heading_info
            chunk = HeadingChunk(
                level=heading_level,
                number=heading_number,
                title=heading_title,
                content="",
            )

            while stack and stack[-1].level >= heading_level:
                stack.pop()
            if stack:
                stack[-1].children.append(chunk)
            else:
                roots.append(chunk)
            stack.append(chunk)

        if intro_lines:
            roots.insert(
                0,
                HeadingChunk(
                    level=0,
                    number="0",
                    title="前言",
                    content="\n".join(intro_lines).strip(),
                ),
            )
        elif not roots and self._full_text.strip():
            roots.append(
                HeadingChunk(
                    level=0, number="0", title="全文", content=self._full_text.strip()
                )
            )
        return roots

    def Execute(self, owner):
        owner.remove_state(self)

    @staticmethod
    def _parse_heading_text(text: str) -> Optional[Tuple[int, str, str]]:
        # 中文/数字标题匹配 → (层级, 编号, 标题)，非标题返回 None
        clean_text = text.strip()
        if not clean_text or clean_text == "目录":
            return None

        chinese_match = CHINESE_HEADING_PATTERN.match(clean_text)
        if chinese_match:
            heading_number = chinese_match.group(1).replace("第", "")
            heading_title = chinese_match.group(2).strip()
            return 1, heading_number, heading_title or clean_text

        numeric_match = NUMERIC_HEADING_PATTERN.match(clean_text)
        if numeric_match:
            heading_number = numeric_match.group(1)
            heading_title = numeric_match.group(2).strip(" .。．、")
            if len(heading_title) > 60 or re.search(r"[。；;]", heading_title):
                return None
            if heading_title:
                return heading_number.count(".") + 1, heading_number, heading_title
        return None

    def Exit(self, owner):
        pass
