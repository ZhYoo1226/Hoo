import importlib
import json
import multiprocessing
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Tuple

# 强制 spawn 子进程，避免 fork 导致 CUDA 上下文冲突
multiprocessing.set_start_method("spawn", force=True)

os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")

_CONF_DIR = Path(__file__).resolve().parent.parent / "workspace" / "conf" / "paddleocr"

_DEPS = json.loads((_CONF_DIR / "deps.json").read_text(encoding="utf-8"))
_OCR_CONF = json.loads((_CONF_DIR / "models.json").read_text(encoding="utf-8"))

# 计算模式：改 deps.json 中 use_gpu 即可切换 CPU/GPU
USE_GPU: bool = _DEPS.get("use_gpu", False)
GPU_MEM: int = _DEPS.get("gpu_mem", 10000)                # 单卡显存上限 MB
os.environ.setdefault("FLAGS_gpu_memory_limit_mb", str(GPU_MEM))

# 根据 use_gpu 自动选取对应的 Paddle 版本和模型组
_paddle_ver_key = "paddlepaddle_gpu" if USE_GPU else "paddlepaddle_cpu"
PINNED_PADDLE_VERSION: str = _DEPS[_paddle_ver_key]       # pip install 用到的版本号
PINNED_PADDLEOCR_VERSION: str = _DEPS["paddleocr"]

_models_key = "gpu" if USE_GPU else "cpu"
_MODELS = _OCR_CONF[_models_key]                          # 检测 / 识别模型名

# 自动安装命令，常驻子进程启动和 state_ocr 兜底安装均依赖此常量
OCR_INSTALL_COMMANDS = [
    [sys.executable, "-m", "pip", "install", f"paddlepaddle=={PINNED_PADDLE_VERSION}"],
    [sys.executable, "-m", "pip", "install", f"paddleocr=={PINNED_PADDLEOCR_VERSION}"],
]

# 模块级引擎缓存：常驻子进程各自调用 init_all_engines 后赋值
_OCR_WORKER_ENGINE = None
_OCR_WORKER_USE_TEXTLINE_ORIENTATION = True
_TABLE_OCR_WORKER_ENGINE = None


# --------------------------------------------------------------------------
# 依赖安装与验证
# --------------------------------------------------------------------------

def _ensure_ocr_deps(auto_install: bool) -> None:
    """验证 Python 版本并确保 PaddleOCR 依赖可用，失败时按 auto_install 自动安装。"""
    try:
        importlib.import_module("paddle")
        importlib.import_module("paddleocr")
    except BaseException:
        if not auto_install:
            raise
        for command in OCR_INSTALL_COMMANDS:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"自动安装 OCR 依赖失败: command={' '.join(command)}, stdout={result.stdout}, stderr={result.stderr}"
                )
        importlib.import_module("paddle")
        importlib.import_module("paddleocr")


# --------------------------------------------------------------------------
# 引擎初始化
# --------------------------------------------------------------------------

def init_all_engines(auto_install: bool, lang: str, use_textline_orientation: bool) -> None:
    """加载全部 OCR 引擎：文字识别、方向检测、表格识别。"""
    _ensure_ocr_deps(auto_install)
    paddleocr = importlib.import_module("paddleocr")
    PaddleOCR = paddleocr.PaddleOCR
    PPStructureV3 = paddleocr.PPStructureV3

    ocr_kwargs = dict(
        lang=lang,
        text_detection_model_name=_MODELS["text_detection_model_name"],
        text_recognition_model_name=_MODELS["text_recognition_model_name"],
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=bool(use_textline_orientation),
    )
    table_kwargs = dict(
        lang=lang,
        text_detection_model_name=_MODELS["text_detection_model_name"],
        text_recognition_model_name=_MODELS["text_recognition_model_name"],
        use_table_recognition=True,
        use_chart_recognition=False,
        use_seal_recognition=False,
        use_formula_recognition=False,
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )

    global _OCR_WORKER_ENGINE, _OCR_WORKER_USE_TEXTLINE_ORIENTATION
    global _TABLE_OCR_WORKER_ENGINE

    _OCR_WORKER_ENGINE = PaddleOCR(**ocr_kwargs)
    _OCR_WORKER_USE_TEXTLINE_ORIENTATION = use_textline_orientation
    _TABLE_OCR_WORKER_ENGINE = PPStructureV3(**table_kwargs)


# --------------------------------------------------------------------------
# 识别任务
# --------------------------------------------------------------------------

# 常驻进程入口：普通文字 OCR → 返回识别文本
def recognize_image_worker(image_path: str) -> str:
    if _OCR_WORKER_ENGINE is None:
        raise RuntimeError("OCR worker 未初始化")
    output = list(
        _OCR_WORKER_ENGINE.predict(
            image_path,
            use_doc_unwarping=False,
            use_textline_orientation=bool(_OCR_WORKER_USE_TEXTLINE_ORIENTATION),
        )
    )
    texts: List[str] = []
    for res in output:
        for ocr in res.res.get("ocr_result", []):
            for t in ocr.get("rec_texts", []):
                if t and str(t).strip():
                    texts.append(str(t).strip())
    return "\n".join(texts)


# 常驻进程入口：表格 OCR → 返回 (图片路径, 文字文本, [表格md+html+bbox])
def recognize_table_worker(image_path: str) -> Tuple[str, str, List[Dict]]:
    if _TABLE_OCR_WORKER_ENGINE is None:
        raise RuntimeError("表格 OCR worker 未初始化")
    output = list(_TABLE_OCR_WORKER_ENGINE.predict(image_path) or [])
    table_regions: List[Dict] = []
    ocr_lines: List[str] = []
    for res in output:
        data = res.res
        for ocr in data.get("ocr_result", []):
            for t in ocr.get("rec_texts", []):
                if t and str(t).strip():
                    ocr_lines.append(str(t).strip())
        for tbl in data.get("table_result", []):
            html = str(tbl.get("html", "") or "")
            md = str(tbl.get("markdown", "") or "")
            if not html.strip() and not md.strip():
                continue
            table_regions.append({
                "html": html,
                "markdown": md,
                "bbox": str(tbl.get("bbox", "")),
            })
    return image_path, "\n".join(ocr_lines), table_regions


# --------------------------------------------------------------------------
# 常驻进程池
# --------------------------------------------------------------------------

# 常驻子进程主循环：加载全部引擎 → 循环取任务 → 识别 → 回结果
def _persistent_worker(task_queue, result_queue, auto_install, lang, use_textline_orientation):
    try:
        init_all_engines(auto_install, lang, use_textline_orientation)
    except BaseException as exc:
        result_queue.put(("init_error", str(exc)))
        return

    result_queue.put(("ready", None))

    while True:
        item = task_queue.get()
        if item is None:
            break

        task_type, task_id, payload = item
        try:
            if task_type == 1:  # OCR
                image_path = payload
                result = recognize_image_worker(image_path)
                result_queue.put((task_id, "success", ("ocr", result)))
            elif task_type == 2:  # Table
                image_path = payload
                result = recognize_table_worker(image_path)
                result_queue.put((task_id, "success", ("table", result)))
        except BaseException as exc:
            result_queue.put((task_id, "error", str(exc)))


# 常驻 OCR 进程池：启动 N 个预加载全部模型的子进程，通过 Queue 收发任务
class PersistentOCRPool:
    TASK_OCR = 1
    TASK_TABLE = 2

    # 启动 n_workers 个子进程，等待全部就绪后返回
    def __init__(self, n_workers: int, auto_install: bool, lang: str, use_textline_orientation: bool):
        self._task_queue = multiprocessing.Queue()
        self._result_queue = multiprocessing.Queue()
        self._workers: List[multiprocessing.Process] = []
        self._next_id = 0
        self._lock = threading.Lock()

        for _ in range(n_workers):
            p = multiprocessing.Process(
                target=_persistent_worker,
                args=(self._task_queue, self._result_queue, auto_install, lang, use_textline_orientation),
                daemon=True,
            )
            p.start()
            self._workers.append(p)

        ready = 0
        while ready < n_workers:
            msg_type, payload = self._result_queue.get()
            if msg_type == "ready":
                ready += 1
            elif msg_type == "init_error":
                raise RuntimeError(f"常驻 worker 初始化失败: {payload}")

    def _next_task_id(self) -> int:
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            return tid

    # 提交文字 OCR 任务，返回 task_id
    def submit_ocr(self, image_path: str) -> int:
        tid = self._next_task_id()
        self._task_queue.put((self.TASK_OCR, tid, image_path))
        return tid

    # 提交表格 OCR 任务，返回 task_id
    def submit_table(self, image_path: str) -> int:
        tid = self._next_task_id()
        self._task_queue.put((self.TASK_TABLE, tid, image_path))
        return tid

    # 阻塞等待任意一个任务结果
    def get_result(self):
        return self._result_queue.get()

    # 发送退出信号并等待全部子进程结束
    def shutdown(self):
        for _ in self._workers:
            self._task_queue.put(None)
        for p in self._workers:
            p.join(timeout=60)
            if p.is_alive():
                p.terminate()
