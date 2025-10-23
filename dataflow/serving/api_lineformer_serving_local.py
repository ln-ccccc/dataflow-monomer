import os
import sys
import json
import base64
import time
from typing import Any, Dict, List, Optional, Tuple
from multiprocessing import Pool, cpu_count
from pathlib import Path

# Chart extraction specific dependencies - lazy import with helpful error messages
try:
    import cv2
except ImportError:
    raise Exception(
        """
OpenCV (opencv-python) is not installed in this environment yet.
Chart extraction requires opencv-python. Install with:
  pip install -e .[chartextract]
  # or directly: pip install opencv-python
"""
    )

from ..logger import get_logger


class LineFormerWorker:
    """封装工作进程的状态和逻辑，替代全局变量"""
    
    def __init__(
        self,
        config_path: str,
        checkpoint_path: str,
        device: str,
        padding_size: int,
        border_color: Tuple[int, int, int]
    ):
        self.config_path = config_path
        self.checkpoint_path = checkpoint_path
        self.device = device
        self.padding_size = padding_size
        self.border_color = border_color
        
        # 延迟导入，避免在主进程中导入
        from ..utils.chartextraction import infer as _infer
        from ..utils.chartextraction import line_utils as _line_utils
        
        self.infer = _infer
        self.line_utils = _line_utils
        
        # 加载模型
        self.infer.load_model(self.config_path, self.checkpoint_path, self.device)
    
    def process_image(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """处理单张图片"""
        res = {
            "image_path": task.get("image_path"),
            "status": "failed",
            "num_lines": 0,
            "line_dataseries": [],
            "visualized_base64": None,
            "json_path": None,
            "vis_path": None,
            "error": None,
        }

        try:
            img_path = task["image_path"]
            return_image = bool(task.get("return_image", False))
            save_json_dir = task.get("save_json_dir")
            save_vis_dir = task.get("save_vis_dir")

            img = cv2.imread(img_path)
            if img is None:
                res["error"] = "Failed to read image"
                return res

            img_padded = cv2.copyMakeBorder(
                img,
                self.padding_size, self.padding_size, 
                self.padding_size, self.padding_size,
                borderType=cv2.BORDER_CONSTANT,
                value=self.border_color
            )

            line_dataseries, _ = self.infer.get_dataseries(
                img_padded, to_clean=False, return_masks=True
            )
            res["line_dataseries"] = line_dataseries
            res["num_lines"] = len(line_dataseries)
            res["status"] = "success"

            stem = Path(img_path).stem

            vis_img = None
            if return_image or save_vis_dir:
                vis_img = self.line_utils.draw_lines(
                    img_padded, self.line_utils.points_to_array(line_dataseries)
                )

            if return_image and vis_img is not None:
                ok, buf = cv2.imencode(".png", vis_img)
                if ok:
                    res["visualized_base64"] = base64.b64encode(buf).decode("utf-8")

            if save_vis_dir and vis_img is not None:
                os.makedirs(save_vis_dir, exist_ok=True)
                vis_path = os.path.join(save_vis_dir, f"{stem}_result.png")
                cv2.imwrite(vis_path, vis_img)
                res["vis_path"] = vis_path

            if save_json_dir:
                os.makedirs(save_json_dir, exist_ok=True)
                json_path = os.path.join(save_json_dir, f"{stem}.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(line_dataseries, f, ensure_ascii=False, indent=2)
                res["json_path"] = json_path

            return res

        except Exception as e:
            res["error"] = str(e)
            return res


# 模块级别的worker实例（每个进程一个）
_worker_instance: Optional[LineFormerWorker] = None


def _init_worker(
    config_path: str,
    checkpoint_path: str,
    device: str,
    padding_size: int,
    border_color: Tuple[int, int, int]
):
    """初始化工作进程中的worker实例"""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = LineFormerWorker(
            config_path, checkpoint_path, device, padding_size, border_color
        )


def _process_image_worker(task: Dict[str, Any]) -> Dict[str, Any]:
    """工作进程的入口函数"""
    global _worker_instance
    if _worker_instance is None:
        raise RuntimeError("Worker not initialized")
    return _worker_instance.process_image(task)


class APILineFormerServing_local():
    """LineFormer本地推理服务，使用多进程池处理图片"""
    
    def __init__(
        self,
        config_path: str = "path/to/your/lineformer_swin_t_config.py",
        checkpoint_path: str = "path/to/your/iter_3000.pth",
        device: str = "cpu",
        padding_size: int = 40,
        border_color: Tuple[int, int, int] = (255, 255, 255),
        num_workers: Optional[int] = 1,
        timeout: int = 1800
    ):
        """
        初始化LineFormer服务
        
        Args:
            lineformer_root: LineFormer项目根目录
            config_path: 模型配置文件路径
            checkpoint_path: 模型权重文件路径
            device: 推理设备 ('cpu' 或 'cuda')
            padding_size: 图片边缘填充大小
            border_color: 填充边框颜色 (B, G, R)
            num_workers: 工作进程数量，默认为 CPU核心数-1
            timeout: 超时时间（秒）
        """
        self.config_path = config_path
        self.checkpoint_path = checkpoint_path
        self.device = device
        self.padding_size = padding_size
        self.border_color = border_color
        self.num_workers = num_workers
        self.timeout = timeout

        self._pool: Optional[Pool] = None
        self.logger = get_logger()

    def start_serving(self) -> None:
        """启动服务，初始化工作进程池"""
        if self._pool is not None:
            return
            
        self.logger.info(
            f"Starting LineFormer local serving with {self.num_workers} workers (device={self.device})"
        )
        
        self._pool = Pool(
            processes=self.num_workers,
            initializer=_init_worker,
            initargs=(
                self.config_path,
                self.checkpoint_path,
                self.device,
                self.padding_size,
                self.border_color,
            ),
        )
        self.logger.success("LineFormer workers initialized.")

    def stop_serving(self) -> None:
        """停止服务，关闭工作进程池"""
        if self._pool is not None:
            try:
                self._pool.close()
                self._pool.join()
            finally:
                self._pool = None
                self.logger.info("LineFormer serving stopped.")

    def extract_from_image_paths(
        self,
        image_paths: List[str],
        *,
        return_image: bool = False,
        save_json_dir: Optional[str] = None,
        save_vis_dir: Optional[str] = None,
        chunksize: int = 4
    ) -> List[Dict[str, Any]]:
        """
        批量处理图片，提取线条数据
        
        Args:
            image_paths: 图片路径列表
            return_image: 是否在结果中返回可视化图片的base64编码
            save_json_dir: 如果指定，将结果保存到此目录的JSON文件中
            save_vis_dir: 如果指定，将可视化图片保存到此目录
            chunksize: 每次分配给工作进程的任务数量
            
        Returns:
            包含处理结果的字典列表
        """
        if not image_paths:
            return []

        if self._pool is None:
            self.start_serving()

        tasks = [{
            "image_path": p,
            "return_image": return_image,
            "save_json_dir": save_json_dir,
            "save_vis_dir": save_vis_dir,
        } for p in image_paths]

        results: List[Dict[str, Any]] = []
        t0 = time.time()
        try:
            # Use ordered iterator to preserve input order in results
            for out in self._pool.imap(_process_image_worker, tasks, chunksize=chunksize):
                results.append(out)
        except Exception as e:
            self.logger.error(f"Multiprocess execution failed: {e}")
            raise
        finally:
            self.logger.info(
                f"Processed {len(image_paths)} images in {time.time() - t0:.2f}s"
            )

        return results