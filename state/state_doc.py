import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence
import pandas as pd
from common import GlobalFunction
from .base_state import BaseState
from .state_file import COL_HASH, COL_PATH, FILES_ABSTRACT_NAME


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class DocxImage:
    # zhang: docx 中抽取的一张图片。
    index: int
    rel_id: str
    media_name: str
    extension: str
    position: int
    blob: bytes

@dataclass
class DocxTable:
    # zhang: docx 中抽取的一个表格，保留结构化行列和 markdown 文本。
    index: int
    position: int
    title: str
    rows: List[List[str]]
    markdown: str

@dataclass
class HeadingChunk:
    '''pageindex的json树'''
    level: int # 知识库条款的强制程度：强制、推荐和参考，来自标准规范原文的用语，得到的等级
    number: str
    title: str
    content: str
    # 可选字段设默认值
    children: List["HeadingChunk"] = field(default_factory=list)
    node_id: str = ""
    start_index: int = -1
    end_index: int = -1
    summary: str = "" # 自身总结+子节点总结
    prefix_summary: str = "" #自身总结
    use_condition: str = "" # 根节点专用：什么场景下使用该知识树进行审查


# --------------------------------------------------------------------------
# 模块级常量
# --------------------------------------------------------------------------

PARSE_DIR_SUFFIX = "__docx__解析"

REL_NAMESPACE = {
    "r": "http://schemas.openxmlformats.org/package/2006/relationships"
}

_CONF_DIR = Path(__file__).resolve().parent.parent / "workspace" / "conf"
_media_content_types_cache: Optional[dict] = None


def get_media_content_types():
    """读取 workspace/conf/imagetype.json，返回 {扩展名: MIME类型}"""
    global _media_content_types_cache
    if _media_content_types_cache is None:
        import json
        imagetypes_path = _CONF_DIR / "imagetype.json"
        try:
            _media_content_types_cache = json.loads(imagetypes_path.read_text(encoding="utf-8"))
        except Exception:
            _media_content_types_cache = {}
    return _media_content_types_cache

# --------------------------------------------------------------------------
# 模块级工具函数
# --------------------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    # 文件名安全化：移除非法字符，截断至 60 字符
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name.strip())
    safe = safe.strip(". ")
    if len(safe) > 60:
        safe = safe[:60].rstrip(". ")
    return safe or "未命名"


def _safe_write_text(path: Path, content: str, label: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        GlobalFunction.log("文件写入时出现错误", f"{label} [{path}]: {exc}")


# --------------------------------------------------------------------------
# 子状态机 (从拆分文件导入)
# --------------------------------------------------------------------------

from .state_convert import Doc2DocxState, Docx2TextState, Pdf2DocxState, Text2ChunksState
from .state_extract import ExtractDocxImagesState, ExtractDocxTablesState


# --------------------------------------------------------------------------
# 混合编排状态机
# --------------------------------------------------------------------------

class WordParseState(BaseState):
    """文档解析混合状态机：输入文件路径，按阶段依次添加子状态机执行解析管线，最终输出写入磁盘文件。"""

    stateName = "WordParseState"

    # 阶段常量
    STAGE_INIT = "init"
    STAGE_NORMALIZE = "normalize"
    STAGE_EXTRACTING = "extracting"

    def __init__(self, source_path: str,  # 待解析的文件路径（.docx / .pdf / .doc）
                 output_root: Optional[str] = None,  # 解析产物输出根目录，默认同文件目录
                 **kwargs):
        super().__init__(**kwargs)
        self.source_path = source_path
        self.output_root = output_root
        self._duration = 60 * 60 * 1000
        self._stage = self.STAGE_INIT
        self._current_sub_states: List[BaseState] = []
        self._done = False

        # 中间结果
        self._source_hash = ""
        self._docx_path = ""
        self._parsed_root: Optional[Path] = None
        self._image_dir: Optional[Path] = None
        self._table_dir: Optional[Path] = None
        self._text_dir: Optional[Path] = None
        self._full_text_path: Optional[Path] = None
        self._document_dir: Optional[Path] = None
        self._tables: List[DocxTable] = []
        self._images: List[DocxImage] = []
        self._full_text = ""
        self._chunks: List[HeadingChunk] = []
        self._chunks_started = False

    def Enter(self, owner):
        # 检查路径文件是否存在
        source = Path(self.source_path).expanduser().resolve()
        if not source.exists():
            GlobalFunction.log("解析错误", f"源文件不存在: {source}")
            self._done = True
            return

        # 计算路径
        if self.output_root:
            self._document_dir = Path(self.output_root).expanduser().resolve()
        else:
            self._document_dir = source.parent
        self._parsed_root = self._document_dir / f"{source.stem}{PARSE_DIR_SUFFIX}"
        self._image_dir = self._parsed_root / "图"
        self._table_dir = self._parsed_root / "表"
        self._text_dir = self._parsed_root / "文字"
        self._full_text_path = self._parsed_root / f"{source.stem}_全文.txt"
        self._source_hash = self._file_sha256(source)

        # 内存幂等性检查--owner上有没有添加过这个cache
        if not hasattr(owner, "parsed_document_cache"):
            owner.parsed_document_cache = {}

        # 创建目录
        for d in [self._document_dir, self._parsed_root, self._image_dir, self._table_dir, self._text_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 幂等检查
        if self._check_idempotent(owner):
            self._done = True
            owner.event.put("WordParseState_done")
            return

    def Execute(self, owner):
        if self._done:
            owner.remove_state(self)
            return

        if self._stage == self.STAGE_EXTRACTING:
            self._check_extracting_progress(owner)
            return

        # 等待所有子状态完成
        if any(s in owner._states for s in self._current_sub_states):
            return

        if self._stage == self.STAGE_INIT:
            self._advance_from_init(owner)
        elif self._stage == self.STAGE_NORMALIZE:
            self._advance_from_normalize(owner)

    def Exit(self, owner):
        pass

    # --------------------------------------------------------------------------
    # 阶段推进
    # --------------------------------------------------------------------------

    def _advance_from_init(self, owner):
        source_ext = Path(self.source_path).suffix.lower()
        source_stem = Path(self.source_path).stem
        # 无需转换
        if source_ext == ".docx":
            self._docx_path = self.source_path
            self._add_sub_states(owner, [
                ExtractDocxTablesState(self._docx_path),
                ExtractDocxImagesState(self._docx_path),
                Docx2TextState(self._docx_path)
            ])
            self._stage = self.STAGE_EXTRACTING
        elif source_ext == ".pdf":
            # pdf → docx 产物放在解析目录内
            docx_path = str(self._parsed_root / f"{source_stem}.docx")
            self._add_sub_states(owner, [Pdf2DocxState(self.source_path, docx_path)])
            self._stage = self.STAGE_NORMALIZE
        elif source_ext == ".doc":
            # doc → docx 产物放在解析目录内
            docx_path = str(self._parsed_root / f"{source_stem}.docx")
            self._add_sub_states(owner, [Doc2DocxState(self.source_path, docx_path)])
            self._stage = self.STAGE_NORMALIZE
        else:
            GlobalFunction.log("文档解析INIT阶段出错", f"暂不支持的文件类型: {source_ext}")
            self._done = True

    def _advance_from_normalize(self, owner):
        sub_state = self._current_sub_states[0] if self._current_sub_states else None
        if sub_state is not None and sub_state._error is not None:
            GlobalFunction.log("解析错误",
                                f"pdf/doc → docx 转换失败: 源={self.source_path}, "
                                f"目标={self._parsed_root / f'{Path(self.source_path).stem}.docx'}, "
                                f"错误={sub_state._error}")
            self._done = True
            return
        source_stem = Path(self.source_path).stem
        self._docx_path = str(self._parsed_root / f"{source_stem}.docx")
        self._add_sub_states(owner, [
            ExtractDocxTablesState(self._docx_path),
            ExtractDocxImagesState(self._docx_path),
            Docx2TextState(self._docx_path),
        ])
        self._stage = self.STAGE_EXTRACTING

    def _check_extracting_progress(self, owner):
        '''文字提取完成后立即启动切块，不等待表/图;所有子状态完成-->收集结果'''
        text_state = next((s for s in self._current_sub_states if s.__class__.__name__ == "Docx2TextState"), None)
        if not self._chunks_started and text_state and text_state._done:
            self._full_text = getattr(text_state, '_full_text', '')
            chunks_state = Text2ChunksState(self._docx_path, self._full_text)
            self._current_sub_states.append(chunks_state)
            owner.add_state(chunks_state)
            self._chunks_started = True

        # 等待所有子状态（表、图、文字、切块）完成
        if any(s in owner._states for s in self._current_sub_states):
            return

        # 收集结果
        for sub in self._current_sub_states:
            if hasattr(sub, '_tables'):
                self._tables = sub._tables
            if hasattr(sub, '_images'):
                self._images = sub._images
            if hasattr(sub, '_chunks'):
                self._chunks = sub._chunks

        self._write_outputs(owner)
        owner.parsed_document_cache[self._source_hash] = str(self._parsed_root)
        self._done = True
        owner.event.put("WordParseState_done")

    # --------------------------------------------------------------------------
    # 辅助
    # --------------------------------------------------------------------------

    def _add_sub_states(self, owner, states: list):
        '''标记就current sub state；并向owner添加'''
        self._current_sub_states = states
        for s in states:
            owner.add_state(s)

    def _check_idempotent(self, owner) -> bool:
        # 1. 内存缓存：同会话已解析过，直接返回
        if owner.parsed_document_cache.get(self._source_hash) == str(self._parsed_root):
            return True

        # 2. 文件摘要表：hash 匹配说明内容未变更
        abstract_path = Path(owner.workspace_path) / "user" / "files" / FILES_ABSTRACT_NAME
        hash_matched = False
        if abstract_path.exists() and abstract_path.stat().st_size > 0:
            try:
                df = pd.read_parquet(abstract_path)
                if COL_PATH in df.columns:
                    files_dir = Path(owner.workspace_path) / "user" / "files"
                    try:
                        rel_path = str(Path(self.source_path).relative_to(files_dir)).replace("\\", "/")
                    except ValueError:
                        rel_path = Path(self.source_path).name
                    matched = df[df[COL_PATH] == rel_path]
                    if not matched.empty and matched.iloc[0].get(COL_HASH) == self._source_hash:
                        hash_matched = True
            except Exception:
                pass
        if not hash_matched:
            return False

        # 3. 磁盘产物完整性
        if not self._parsed_root.exists():
            return False
        source = Path(self.source_path)
        docx_path = source if source.suffix.lower() == ".docx" else (self._parsed_root / f"{source.stem}.docx")
        if not docx_path.exists():
            return False
        if not self._full_text_path.exists():
            return False
        if self._text_dir.exists():
            content_files = list(self._text_dir.rglob("内容.txt"))
            if not content_files:
                return False
            for path in content_files:
                if any(child.is_dir() for child in path.parent.iterdir()):
                    return False

        owner.parsed_document_cache[self._source_hash] = str(self._parsed_root)
        return True

    # --------------------------------------------------------------------------
    # 输出与工具方法
    # --------------------------------------------------------------------------

    @staticmethod
    def _file_sha256(path: Path) -> str:
        # 计算文件 SHA256，用于幂等判断和变更检测
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while True:
                chunk = file.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _chunk_file_stem(self, chunk: HeadingChunk) -> str:
        # 标题节点 → 安全的目录名（标题_sha1前8位）
        safe_title = _sanitize_name(chunk.title)
        if not safe_title:
            safe_title = _sanitize_name(chunk.number)
        digest = hashlib.sha1((chunk.number + chunk.title).encode("utf-8")).hexdigest()[:8]
        return f"{safe_title}_{digest}"

    def _write_chunk_tree(self, chunk: HeadingChunk, parent_dir: Path) -> None:
        '''递归将标题节点树写入'''
        chunk_dir = parent_dir / self._chunk_file_stem(chunk)
        chunk_dir.mkdir(parents=True, exist_ok=True)

        if not chunk.children:
            content_path = chunk_dir / "内容.txt"
            body = chunk.content.strip()
            content_path.write_text(body + ("\n" if body else ""), encoding="utf-8")

        for child in chunk.children:
            self._write_chunk_tree(child, chunk_dir)

    def _write_heading_outline_files(self, parsed_root: Path, chunks: Sequence[HeadingChunk]) -> None:
        '''三个层级目录的信息写入'''
        level1_lines: List[str] = []
        level2_lines: List[str] = []
        level3_lines: List[str] = []

        for chunk in chunks:
            if chunk.level == 1:
                level1_lines.append(chunk.title)
                for child in chunk.children:
                    if child.level == 2:
                        level2_lines.append(f"  - {child.title}")
                        for grandchild in child.children:
                            if grandchild.level == 3:
                                level3_lines.append(f"{chunk.title} / {child.title}")
                                level3_lines.append(f"    - {grandchild.title}")
            elif chunk.level == 2:
                level2_lines.append(chunk.title)
                for child in chunk.children:
                    if child.level == 3:
                        level3_lines.append(chunk.title)
                        level3_lines.append(f"  - {child.title}")
            elif chunk.level == 3:
                level3_lines.append(chunk.title)

        for stem, lines in [("一级", level1_lines), ("二级", level2_lines), ("三级", level3_lines)]:
            (parsed_root / f"{stem}目录信息.txt").write_text(
                ("\n".join(filter(None, lines)) + "\n") if any(lines) else "",
                encoding="utf-8",
            )

    def _write_outputs(self, owner):
        '''落盘函数'''
        # 将所有中间结果写入磁盘：表格md、图片+元数据、全文txt、目录文件、标题树、manifest
        for table in self._tables:
            stem = f"表{table.index}_{table.position}_{table.title}"
            markdown_path = self._table_dir / f"{stem}.md"
            _safe_write_text(markdown_path, table.markdown, "表格写入失败")

        for image in self._images:
            stem = f"图{image.index}"
            image_path = self._image_dir / f"{stem}{image.extension}"
            try:
                image_path.write_bytes(image.blob)
            except Exception as exc:
                GlobalFunction.log("解析错误", f"图片写入失败 [{image_path}]: {exc}")
                continue
            try:
                rel_img = str(image_path.relative_to(Path(owner.workspace_path))).replace("\\", "/")
            except ValueError:
                rel_img = image_path.name
            output_md = self._image_dir / f"{stem}_图片解析.md"
            GlobalFunction.write_image_meta(str(output_md), {
                "docx_rel_id": image.rel_id,
                "docx_media_name": image.media_name,
                "图片路径": rel_img,
            })

        _safe_write_text(self._full_text_path, self._full_text, "全文写入失败")
        self._write_heading_outline_files(self._parsed_root, self._chunks)

        for chunk in self._chunks:
            self._write_chunk_tree(chunk, self._text_dir)

        manifest_source = os.path.relpath(self.source_path, self._parsed_root)
        manifest_docx = os.path.relpath(self._docx_path, self._parsed_root)
        (self._parsed_root / "parse_manifest.md").write_text(
            "\n".join([
                f"source_hash: {self._source_hash}",
                f"source_path: {manifest_source}",
                f"normalized_docx: {manifest_docx}",
            ]) + "\n",
            encoding="utf-8",
        )


__all__ = ["WordParseState", ]
