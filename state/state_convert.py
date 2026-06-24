"""文档转换相关子状态机：pdf→docx、doc→docx、docx→全文、全文→分块"""

import io
import os
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from pdf2docx import Converter

from common import GlobalFunction
from .base_state import BaseState, StateOwner
from .state_doc import HeadingChunk


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
            GlobalFunction.log("转换错误", f"pdf → docx 转换失败: 源={self._pdf_path}, 目标={self._docx_path}, 错误={self._error}")
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
            GlobalFunction.log("转换错误", f"doc → docx 转换失败: 源={self._doc_path}, 目标={self._docx_path}, 错误={self._error}")
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

    def Enter(self, owner: StateOwner):
        self.full_text = self._full_text #当xxx后续要用到，内存。如果其余地方要使用_内存-->常规属性
        actions = owner.execute_prompt('提取文档标题层级',self,None,[])
        tree = actions[0].get('params',{}).get('tree',[])
        self._chunks = self._json_to_chunks(tree)
    
    # 正则方案--太脆了
    
    def _json_to_chunks(self, tree):
        result = []
        for node in tree:          # tree 是列表，node 是字典
            number = node.get("number", "")
            title = node.get("title", "")
            level = node.get("level", 1)
            content = node.get("content", "")
            children = node.get("children", [])   # children 也是列表

            child_chunks = self._json_to_chunks(children)  # 递归
            chunk = HeadingChunk(
                level=level,
                number=number,
                title=title,
                content=content,
                children=child_chunks,
            )
            result.append(chunk)
        return result


    def Execute(self, owner):
        owner.remove_state(self)

    def Exit(self, owner):
        pass
