import importlib.util
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from PIL import Image, ImageFilter, ImageOps
from .ocr_worker import (
    OCR_INSTALL_COMMANDS,
    PersistentOCRPool,
    GPU_MEM,
    get_gpu_free_memory_mb,
)
from .base_state import BaseState

# OCR 配置目录：imagetype.json / paddleocr/deps.json 等
_CONF_DIR = Path(__file__).resolve().parent.parent / "workspace" / "conf"

# 图片后缀 → MIME 映射，_collect_image_paths 以此过滤可识别文件
_MEDIA_CONTENT_TYPES: dict = json.loads(
    (_CONF_DIR / "imagetype.json").read_text(encoding="utf-8")
)


# --------------------------------------------------------------------------
# OCR 基类：图片预处理、进程池管理、结果回写磁盘
# --------------------------------------------------------------------------

class _BaseOCRState(BaseState):

    MEDIA_CONTENT_TYPES = _MEDIA_CONTENT_TYPES

    def __init__(
        self,
        source_path: str,
        auto_install: bool = True,
        use_textline_orientation: bool = True,
        lang: str = "ch",
        batch_index: int = 0,
        **kwargs,
    ):
        '''
        参数：
        source_path: 图片目录路径，内部自动扫描目录下所有图片文件
        auto_install: 是否自动安装 OCR 依赖，默认 True
        use_textline_orientation: 是否启用文本行方向检测，默认 True
        lang: OCR 识别语言，默认 "ch"
        batch_index: 批次序号，用于日志标识
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._duration = 60 * 60 * 1000
        self.source_path = Path(source_path)
        self.auto_install = auto_install
        self.use_textline_orientation = use_textline_orientation
        self.lang = lang
        self.batch_index = batch_index
        self._done = False
        self._error: Optional[BaseException] = None
        self._started = False

    def Enter(self, owner):
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._run_ocr, args=(owner,), daemon=True)
        t.start()

    def Execute(self, owner):
        if self._error is not None:
            owner.remove_state(self)
            raise self._error
        if not self._done:
            return
        owner.remove_state(self)

    def Exit(self, owner):
        pass

    # --------------------------------------------------------------------------
    # 核心流水线
    # --------------------------------------------------------------------------

    def _run_ocr(self, owner):
        try:
            started_at = time.perf_counter()
            image_paths = self._collect_image_paths(self.source_path)
            total_images = len(image_paths)
            owner.sys_breathe_log(f"批次{self.batch_index}: 开始 OCR，图片总数={total_images}")
            self._ensure_ocr_ready(owner)

            enhanced_images = self._enhance_images(image_paths)

            free_mb = get_gpu_free_memory_mb()
            n_workers = max(1, min(free_mb // GPU_MEM, 2))
            pool = PersistentOCRPool(n_workers, self.auto_install, self.lang, self.use_textline_orientation)
            try:
                owner.sys_breathe_log(f"批次{self.batch_index}: 预处理完成，增强={len(enhanced_images)}")
                pending = self._submit_all(pool, enhanced_images)
                self._collect_all(pool, pending, owner)
            finally:
                pool.shutdown()

            owner.sys_breathe_log(f"批次{self.batch_index}: OCR 完成，总耗时={time.perf_counter() - started_at:.2f}s")
        except BaseException as exc:
            self._error = exc
        finally:
            self._done = True
            owner.event.put(f"{self.stateName}_done")

    # --------------------------------------------------------------------------
    # 子类钩子
    # --------------------------------------------------------------------------

    def _submit_all(self, pool, enhanced_images: List[Path]) -> Dict:
        raise NotImplementedError

    def _collect_all(self, pool, pending: Dict, owner) -> None:
        raise NotImplementedError

    # --------------------------------------------------------------------------
    # 共享方法
    # --------------------------------------------------------------------------

    @staticmethod
    def _enhance_images(image_paths: List[Path]) -> List[Path]:
        enhanced: List[Path] = []
        for image_path in image_paths:
            with Image.open(image_path) as img:
                processed = img.convert("RGB")
                min_edge = min(processed.width, processed.height)
                if 0 < min_edge < 1200:
                    scale = 1200 / min_edge
                    processed = processed.resize(
                        (max(1, int(round(processed.width * scale))),
                         max(1, int(round(processed.height * scale)))),
                        Image.Resampling.LANCZOS,
                    )
                processed = processed.convert("L")
                processed = processed.filter(ImageFilter.MedianFilter(size=3))
                processed = processed.filter(ImageFilter.SHARPEN)
                processed = ImageOps.autocontrast(processed)
                processed.save(image_path)
            enhanced.append(image_path)
        return enhanced

    @staticmethod
    def _collect_image_paths(source_dir: Path) -> List[Path]:
        paths: List[Path] = []
        if not source_dir.exists():
            return paths
        for path in source_dir.rglob("*"):
            if path.suffix.lower() in _BaseOCRState.MEDIA_CONTENT_TYPES:
                paths.append(path)
        return sorted(paths)

    def _write_ocr_text_metadata(self, image_path: Path, ocr_text: str, owner) -> None:
        output_path = image_path.parent / f"{image_path.stem}_图片解析.md"

        # 读取已有 JSON（state_doc 或其他阶段可能已写入部分字段）
        result: Dict = {}
        if output_path.exists():
            content = output_path.read_text(encoding="utf-8")
            match = re.search(r"```json\s*\n([\s\S]*?)\n```", content)
            if match:
                try:
                    result = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        # 图片路径：已有则保留，否则自行计算
        if not result.get("图片路径"):
            try:
                rel_path = str(image_path.relative_to(Path(owner.workspace_path))).replace("\\", "/")
            except ValueError:
                rel_path = image_path.name
            result["图片路径"] = rel_path

        # 填入 OCR 负责的字段
        result["图片类型"] = self._image_type
        result["文字识别"] = ocr_text

        # 补充默认值
        result.setdefault("docx_rel_id", "")
        result.setdefault("docx_media_name", "")
        result.setdefault("内容概述", "")

        output_path.write_text(
            f"# {image_path.stem} 图片解析\n\n"
            f"## 图片信息摘要\n\n"
            f"```json\n"
            f"{json.dumps(result, ensure_ascii=False, indent=2)}\n"
            f"```\n\n"
            f"## 图片处理\n",
            encoding="utf-8",
        )

    def _ensure_ocr_ready(self, owner) -> None:
        """仅检查依赖是否已安装，不做 import 以避免在父进程中初始化 CUDA。"""
        if (
            importlib.util.find_spec("paddleocr") is not None
            and importlib.util.find_spec("paddle") is not None
        ):
            return

        if not self.auto_install:
            raise RuntimeError("未检测到 PaddleOCR 依赖，且 auto_install=False")

        for command in OCR_INSTALL_COMMANDS:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"自动安装 OCR 依赖失败: command={' '.join(command)}, stdout={result.stdout}, stderr={result.stderr}"
                )

        if not (
            importlib.util.find_spec("paddleocr") is not None
            and importlib.util.find_spec("paddle") is not None
        ):
            raise RuntimeError("OCR 依赖安装完成后仍不可用")


# --------------------------------------------------------------------------
# ImgOCRState — 文本 OCR
# --------------------------------------------------------------------------

class ImgOCRState(_BaseOCRState):
    """图片文本 OCR 状态机"""
    stateName = "ImgOCRState"
    _image_type = "文本"

    def _submit_all(self, pool, enhanced_images: List[Path]) -> Dict:
        pending: Dict[int, Path] = {}
        for image_path in enhanced_images:
            tid = pool.submit_ocr(str(image_path))
            pending[tid] = image_path
        return pending

    def _collect_all(self, pool, pending: Dict, owner) -> None:
        for _ in range(len(pending)):
            tid, status, data = pool.get_result()
            image_path = pending.pop(tid)
            if status == "error":
                raise RuntimeError(f"Worker 错误 ({image_path.name}): {data}")
            _, worker_result = data
            self._write_ocr_text_metadata(image_path, worker_result, owner)


# --------------------------------------------------------------------------
# TableOCRState — 表格 OCR
# --------------------------------------------------------------------------

class TableOCRState(_BaseOCRState):
    """图片表格 OCR 状态机"""
    stateName = "TableOCRState"
    _image_type = "表格"

    def _submit_all(self, pool, enhanced_images: List[Path]) -> Dict:
        pending: Dict[int, Path] = {}
        for image_path in enhanced_images:
            tid = pool.submit_table(str(image_path))
            pending[tid] = image_path
        return pending

    def _collect_all(self, pool, pending: Dict, owner) -> None:
        for _ in range(len(pending)):
            tid, status, data = pool.get_result()
            image_path = pending.pop(tid)
            if status == "error":
                raise RuntimeError(f"Worker 错误 ({image_path.name}): {data}")
            _, worker_result = data
            recognized_path, ocr_text, regions = worker_result
            self._write_table_outputs(image_path, regions)
            self._write_ocr_text_metadata(image_path, ocr_text, owner)

    @staticmethod
    def _write_table_outputs(
        image_path: Path, regions: Sequence[Dict]
    ) -> List[Dict[str, str]]:
        outputs: List[Dict[str, str]] = []
        output_dir = image_path.parent
        name = image_path.stem.strip()
        name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(". ")
        page_name = name[:60].rstrip(". ") if len(name) > 60 else (name or "未命名")
        for table_index, region in enumerate(regions, start=1):
            html = str(region.get("html", "")).strip()
            if not html:
                continue
            stem = f"{page_name}_TableOCR_{table_index}"
            html_path = output_dir / f"{stem}.html"
            html_path.write_text(html, encoding="utf-8")
            outputs.append({
                "html": str(html_path),
                "bbox": str(region.get("bbox", "")),
            })
        return outputs


__all__ = ["ImgOCRState", "TableOCRState"]
