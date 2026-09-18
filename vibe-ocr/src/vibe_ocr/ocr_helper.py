"""
OCR Helper Class - 基于PaddleOCR的文字识别和定位工具类
提供统一的接口供外部调用，输入图像文件和要查找的文字，返回文字所在的图片区域
支持区域分割功能，可以指定只识别屏幕的特定区域，大大提升识别速度
"""

import base64
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import cv2
import imagehash
import requests
from dotenv import load_dotenv
from PIL import Image

# 加载环境变量
load_dotenv()

# 缓存条目默认有效期：24 小时（秒）
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60

# 两次过期清理之间的最小间隔（秒），避免每次查找都扫一遍缓存表
CACHE_CLEANUP_INTERVAL_SECONDS = 60


class OCRHelper:
    """OCR辅助工具类，封装PaddleOCR功能"""

    def __init__(
        self,
        output_dir="output",
        resize_image=True,
        max_width=960,
        delete_temp_screenshots=True,
        max_cache_size=200,
        hash_type="dhash",  # 可选: "phash", "dhash", "ahash", "whash"
        hash_threshold=10,  # hash 汉明距离阈值
        correction_map: Optional[Dict[str, str]] = None,
        snapshot_func: Optional[Callable[..., Any]] = None,
        cache_ttl_seconds: Optional[int] = None,
    ):
        """
        初始化OCR Helper

        Args:
            output_dir (str): 输出目录路径
            resize_image (bool): 是否自动缩小图片以提升速度
            max_width (int): 图片最大宽度，默认960（建议在640-960之间）
            delete_temp_screenshots (bool): 是否删除临时截图文件，默认为True
            max_cache_size (int): 最大缓存条目数，默认200
            hash_type (str): 哈希算法类型，默认"dhash"（差分哈希，最快）
            hash_threshold (int): 哈希汉明距离阈值，默认10
            correction_map (dict): OCR 纠正映射，例如 {"装各": "装备"}
            snapshot_func (callable): 自定义截图函数，接受 filename 参数
            cache_ttl_seconds (int, optional): 缓存条目有效期（秒）。为 None 时读取
                环境变量 ``OCR_CACHE_TTL_SECONDS``，该变量也未设置时使用
                ``DEFAULT_CACHE_TTL_SECONDS``（24 小时）。设为 0 或负数表示永不过期。
        """
        self.output_dir = output_dir
        self.resize_image = resize_image
        self.max_width = max_width
        self.delete_temp_screenshots = delete_temp_screenshots
        self.max_cache_size = max_cache_size
        self.hash_type = hash_type
        self.hash_threshold = hash_threshold
        self.correction_map = correction_map or {}
        self.snapshot_func = snapshot_func

        # 缓存条目有效期（秒）：解析顺序为显式参数 -> 环境变量 -> 默认 24 小时
        if cache_ttl_seconds is None:
            cache_ttl_seconds = int(
                os.getenv("OCR_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
            )
        self.cache_ttl_seconds = cache_ttl_seconds
        # 上次执行过期清理的时间戳，用于清理节流
        self._last_cache_cleanup = 0.0

        self.ocr_url = os.getenv("OCR_SERVER_URL", "http://localhost:8080/ocr")

        # 创建输出目录
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        # 创建缓存目录和临时目录
        self.cache_dir = os.path.join(self.output_dir, "cache")
        self.temp_dir = os.path.join(self.output_dir, "temp")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        # 配置日志
        self.logger = logging.getLogger(__name__)

        # 初始化缓存（仅保留兼容属性，不再保存图片路径）
        # 旧版格式: [(image_path, json_file_path), ...]
        self.ocr_cache = []

        # 缓存相似度阈值（95%以上认为是同一张图）
        self.cache_similarity_threshold = 0.95

        # 初始化 SQLite 缓存数据库
        self.cache_db_path = os.path.join(self.cache_dir, "cache.db")
        self._init_cache_db()

        # 仅使用 SQLite 存储缓存，避免落盘图片文件

    def _init_cache_db(self):
        """
        初始化缓存数据库，创建必要的表
        """
        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.cursor()
                # 删除旧缓存表，避免继续落盘图片缓存
                cursor.execute("DROP TABLE IF EXISTS cache_entries")
                # 创建缓存表（仅保存哈希与 JSON 数据，不落盘图片）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ocr_cache (
                        image_hash TEXT PRIMARY KEY,
                        phash TEXT,
                        dhash TEXT,
                        ahash TEXT,
                        whash TEXT,
                        regions TEXT,  -- JSON 存储区域信息
                        hit_count INTEGER DEFAULT 0,
                        last_access_time REAL,
                        created_time REAL,
                        image_size INTEGER,  -- 图片字节大小
                        json_data TEXT NOT NULL  -- OCR 结果 JSON
                    )
                """)
                # 创建索引以加速查询
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ocr_cache_phash ON ocr_cache(phash)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ocr_cache_dhash ON ocr_cache(dhash)")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ocr_cache_last_access ON ocr_cache(last_access_time)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ocr_cache_image_hash ON ocr_cache(image_hash)"
                )
                conn.commit()
            self.logger.debug(f"✅ 缓存数据库初始化成功: {self.cache_db_path}")
        except Exception as e:
            self.logger.error(f"❌ 初始化缓存数据库失败: {e}")
            raise

    def _compute_image_hash(
        self,
        image_path: Optional[str] = None,
        image: Optional[Any] = None,
        hash_type: Optional[str] = None,
    ) -> Optional[str]:
        """
        计算图像的感知哈希值

        Args:
            image_path: 图像路径
            image: OpenCV 图像对象
            hash_type: 哈希类型 ("phash", "dhash", "ahash", "whash")

        Returns:
            哈希值的十六进制字符串，失败返回None
        """
        if hash_type is None:
            hash_type = self.hash_type

        try:
            pil_img = self._to_pil_image(image_path=image_path, image=image)
            if pil_img is None:
                return None
            if hash_type == "phash":
                hash_obj = imagehash.phash(pil_img)
            elif hash_type == "dhash":
                hash_obj = imagehash.dhash(pil_img)
            elif hash_type == "ahash":
                hash_obj = imagehash.average_hash(pil_img)
            elif hash_type == "whash":
                hash_obj = imagehash.whash(pil_img)
            else:
                self.logger.warning(f"未知的哈希类型: {hash_type}，使用默认 dhash")
                hash_obj = imagehash.dhash(pil_img)
            return str(hash_obj)
        except Exception as e:
            self.logger.debug(f"计算图像哈希失败: {e}")
            return None

    def _compute_all_hashes(
        self, image_path: Optional[str] = None, image: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        计算图像的所有哈希值

        Args:
            image_path: 图像路径
            image: OpenCV 图像对象

        Returns:
            包含所有哈希值的字典
        """
        hashes = {}
        try:
            pil_img = self._to_pil_image(image_path=image_path, image=image)
            if pil_img is None:
                return hashes
            hashes["phash"] = str(imagehash.phash(pil_img))
            hashes["dhash"] = str(imagehash.dhash(pil_img))
            hashes["ahash"] = str(imagehash.average_hash(pil_img))
            hashes["whash"] = str(imagehash.whash(pil_img))
        except Exception as e:
            self.logger.debug(f"计算图像哈希失败: {e}")
        return hashes

    def _to_pil_image(
        self, image_path: Optional[str] = None, image: Optional[Any] = None
    ) -> Optional[Image.Image]:
        """
        将输入转换为 PIL Image
        """
        try:
            if image is not None:
                if len(image.shape) == 2:
                    return Image.fromarray(image)
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb)
            if image_path:
                with Image.open(image_path) as img:
                    return img.copy()
        except Exception as e:
            self.logger.debug(f"转换 PIL 图片失败: {e}")
        return None

    def _get_image_bytes(
        self, image_path: Optional[str] = None, image: Optional[Any] = None
    ) -> Optional[bytes]:
        """
        获取图像字节数据，用于计算精确哈希
        """
        try:
            if image is not None:
                success, buffer = cv2.imencode(".png", image)
                if not success:
                    return None
                return buffer.tobytes()
            if image_path:
                with open(image_path, "rb") as f:
                    return f.read()
        except Exception as e:
            self.logger.debug(f"读取图像字节失败: {e}")
        return None

    def _compute_image_md5(
        self, image_path: Optional[str] = None, image: Optional[Any] = None
    ) -> Optional[str]:
        """
        计算图像字节的 MD5 哈希
        """
        try:
            image_bytes = self._get_image_bytes(image_path=image_path, image=image)
            if image_bytes is None:
                return None
            import hashlib

            return hashlib.md5(image_bytes).hexdigest()
        except Exception as e:
            self.logger.debug(f"计算 MD5 失败: {e}")
            return None

    def _calculate_hamming_distance(self, hash1: str, hash2: str) -> int:
        """
        计算两个哈希值之间的汉明距离

        Args:
            hash1: 第一个哈希值
            hash2: 第二个哈希值

        Returns:
            汉明距离（不同位的数量）
        """
        try:
            # 将十六进制转换为二进制字符串
            h1 = int(hash1, 16)
            h2 = int(hash2, 16)
            # 异或后计算1的个数
            return bin(h1 ^ h2).count("1")
        except Exception:
            return 999  # 返回一个大值表示无法比较

    def _get_cache_key(self, image_path: str, regions: Optional[List[int]] = None) -> str:
        """
        生成缓存键，包含图像路径和区域信息

        Args:
            image_path: 图像路径
            regions: 区域列表

        Returns:
            唯一的缓存键
        """
        if regions:
            regions_str = "_".join(map(str, sorted(regions)))
            return f"{image_path}_{regions_str}"
        return image_path

    def _find_similar_in_cache(
        self,
        image_path: Optional[str] = None,
        image: Optional[Any] = None,
        regions: Optional[List[int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        在缓存中查找相似的图像

        Args:
            image_path: 图像路径
            image: OpenCV 图像对象
            regions: 区域列表（如果有的话）

        Returns:
            OCR 结果字典，没找到返回None
        """
        try:
            # 先淘汰过期条目：避免复用过期的（可能是错误的）识别结论
            self._clean_expired_cache()

            image_bytes = self._get_image_bytes(image_path=image_path, image=image)
            if image_bytes is None:
                return None

            # 计算当前图像的哈希值
            current_hashes = self._compute_all_hashes(image_path=image_path, image=image)
            if not current_hashes.get(self.hash_type):
                return None

            image_hash = self._compute_image_md5(image_path=image_path, image=image)
            if not image_hash:
                return None

            regions_json = json.dumps(sorted(regions)) if regions else None

            # 连接数据库
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.cursor()

                # 首先检查是否有完全相同的图像（通过文件哈希）
                if regions_json is None:
                    cursor.execute(
                        "SELECT json_data FROM ocr_cache WHERE image_hash = ?",
                        (image_hash,),
                    )
                else:
                    cursor.execute(
                        "SELECT json_data FROM ocr_cache WHERE image_hash = ? AND regions = ?",
                        (image_hash, regions_json),
                    )
                result = cursor.fetchone()
                if result and result[0]:
                    self.logger.debug("?? 缓存命中（完全相同）")
                    # 更新访问信息
                    cursor.execute(
                        "UPDATE ocr_cache SET hit_count = hit_count + 1, last_access_time = ? WHERE image_hash = ?",
                        (time.time(), image_hash),
                    )
                    conn.commit()
                    return json.loads(result[0])

                # 查找相似的图像（基于感知哈希）
                primary_hash = current_hashes[self.hash_type]

                if regions_json is None:
                    cursor.execute(
                        f"""
                        SELECT image_hash, json_data, {self.hash_type}
                        FROM ocr_cache
                        WHERE {self.hash_type} IS NOT NULL
                        ORDER BY last_access_time DESC
                        LIMIT 100
                    """
                    )
                else:
                    cursor.execute(
                        f"""
                        SELECT image_hash, json_data, {self.hash_type}
                        FROM ocr_cache
                        WHERE {self.hash_type} IS NOT NULL AND regions = ?
                        ORDER BY last_access_time DESC
                        LIMIT 100
                    """,
                        (regions_json,),
                    )

                candidates = cursor.fetchall()
                best_match = None
                best_distance = 999

                for cached_image_hash, json_data, cached_hash in candidates:
                    if not cached_hash:
                        continue

                    distance = self._calculate_hamming_distance(primary_hash, cached_hash)
                    if distance < best_distance and distance <= self.hash_threshold:
                        best_distance = distance
                        best_match = (cached_image_hash, json_data, distance)

                if best_match:
                    cached_image_hash, json_data, distance = best_match
                    self.logger.debug(f"?? 缓存命中（哈希相似，距离={distance}）")

                    # 更新访问信息
                    cursor.execute(
                        "UPDATE ocr_cache SET hit_count = hit_count + 1, last_access_time = ? WHERE image_hash = ?",
                        (time.time(), cached_image_hash),
                    )
                    conn.commit()

                    if json_data:
                        return json.loads(json_data)

            return None
        except Exception as e:
            self.logger.error(f"查找缓存失败: {e}")
            return None

    def _clean_expired_cache(self, force: bool = False) -> int:
        """清理创建时间超过 ``cache_ttl_seconds`` 的缓存条目。

        感知哈希缓存会把某次 OCR 的识别结果长期固化：只要后续画面与缓存图像
        足够相似（汉明距离 <= ``hash_threshold``），就直接复用旧结果而不再重新
        识别。一旦某次识别出错，错误结论会被反复命中；而命中会刷新
        ``last_access_time``，使该条目在 LRU 淘汰中始终排在"最热"位置，
        最终永不过期（实测曾有一条把文字识别少一个字的条目存活 86 天、
        被命中上千次）。因此这里按 ``created_time`` 强制淘汰，
        保证任何一次识别结论最多存活一个 TTL 周期。

        Args:
            force: 为 True 时忽略清理节流，立即执行清理。

        Returns:
            本次删除的条目数量；缓存未启用（TTL 非正数）或清理失败时返回 0。
        """
        if self.cache_ttl_seconds is None or self.cache_ttl_seconds <= 0:
            return 0

        now = time.time()
        if not force and now - self._last_cache_cleanup < CACHE_CLEANUP_INTERVAL_SECONDS:
            return 0
        self._last_cache_cleanup = now

        try:
            expire_time = now - self.cache_ttl_seconds
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM ocr_cache WHERE created_time < ?",
                    (expire_time,),
                )
                deleted = cursor.rowcount
                conn.commit()

            if deleted > 0:
                self.logger.info(
                    f"🧹 清理了 {deleted} 个过期缓存条目"
                    f"（TTL={self.cache_ttl_seconds}秒）"
                )
            return deleted
        except Exception as e:
            self.logger.error(f"清理过期缓存失败: {e}")
            return 0

    def _evict_cache(self):
        """
        淘汰最久未访问的缓存条目，保持缓存大小在限制内
        """
        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.cursor()

                # 获取当前缓存条目数
                cursor.execute("SELECT COUNT(*) FROM ocr_cache")
                count = cursor.fetchone()[0]

                if count > self.max_cache_size:
                    # 计算需要删除的条目数
                    to_delete = count - self.max_cache_size + 10  # 多删除一些，避免频繁操作

                    # 删除最久未访问的条目
                    cursor.execute(
                        """
                        DELETE FROM ocr_cache
                        WHERE image_hash IN (
                            SELECT image_hash FROM ocr_cache
                            ORDER BY last_access_time ASC
                            LIMIT ?
                        )
                    """,
                        (to_delete,),
                    )

                    conn.commit()
                    self.logger.debug(f"?? 淘汰了 {to_delete} 个缓存条目")
        except Exception as e:
            self.logger.error(f"淘汰缓存失败: {e}")

    def _save_to_cache_db(
        self,
        image_path: Optional[str] = None,
        ocr_result: Optional[Dict[str, Any]] = None,
        regions: Optional[List[int]] = None,
        image: Optional[Any] = None,
    ):
        """
        保存缓存条目到数据库

        Args:
            image_path: 图像路径
            ocr_result: OCR 结果字典
            regions: 区域列表
            image: OpenCV 图像对象
        """
        try:
            if ocr_result is None:
                return

            image_bytes = self._get_image_bytes(image_path=image_path, image=image)
            if image_bytes is None:
                return

            # 计算所有哈希值
            hashes = self._compute_all_hashes(image_path=image_path, image=image)

            # 计算文件哈希
            image_hash = self._compute_image_md5(image_path=image_path, image=image)
            if not image_hash:
                return

            # 获取文件大小
            image_size = len(image_bytes)

            # 准备区域信息
            regions_json = json.dumps(sorted(regions)) if regions else None

            json_data = json.dumps(ocr_result, ensure_ascii=False)

            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.cursor()

                # 插入或更新记录
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO ocr_cache
                    (image_hash, phash, dhash, ahash, whash, regions,
                     hit_count, last_access_time, created_time, image_size, json_data)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                    (
                        image_hash,
                        hashes.get("phash"),
                        hashes.get("dhash"),
                        hashes.get("ahash"),
                        hashes.get("whash"),
                        regions_json,
                        time.time(),
                        time.time(),
                        image_size,
                        json_data,
                    ),
                )

                conn.commit()

            # 检查是否需要淘汰
            self._evict_cache()

        except Exception as e:
            self.logger.error(f"保存缓存到数据库失败: {e}")

    def _merge_regions(self, regions: List[int]) -> Tuple[int, int, int, int]:
        """
        合并多个区域为一个连续的矩形区域

        Args:
            regions: 要合并的区域列表（1-9）
                    1 2 3
                    4 5 6
                    7 8 9

        Returns:
            合并后的边界 (min_row, max_row, min_col, max_col)，都是0-based索引
        """
        if not regions:
            return (0, 2, 0, 2)  # 整个图像

        # 将区域ID转换为行列索引
        rows = []
        cols = []
        for region_id in regions:
            if not 1 <= region_id <= 9:
                self.logger.warning(f"无效的区域ID: {region_id}，跳过")
                continue
            row = (region_id - 1) // 3
            col = (region_id - 1) % 3
            rows.append(row)
            cols.append(col)

        if not rows:
            return (0, 2, 0, 2)

        # 计算包含所有区域的最小矩形
        min_row = min(rows)
        max_row = max(rows)
        min_col = min(cols)
        max_col = max(cols)

        return (min_row, max_row, min_col, max_col)

    def _get_region_bounds(
        self, image_shape: Tuple[int, int], regions: Optional[List[int]] = None
    ) -> Tuple[int, int, int, int]:
        """
        将图像分成3x3网格，返回合并后的区域边界

        Args:
            image_shape: 图像形状 (height, width)
            regions: 要提取的区域列表，使用数字1-9表示（从左到右，从上到下）
                    1 2 3
                    4 5 6
                    7 8 9
                    如果为None，返回整个图像
                    多个区域会被合并成一个连续的矩形

        Returns:
            区域边界 (x, y, w, h)
        """
        height, width = image_shape

        if regions is None:
            # 返回整个图像
            return (0, 0, width, height)

        # 合并区域
        min_row, max_row, min_col, max_col = self._merge_regions(regions)

        # 计算每个格子的大小
        cell_height = height // 3
        cell_width = width // 3

        # 计算合并后的边界
        x = min_col * cell_width
        y = min_row * cell_height
        w = (max_col - min_col + 1) * cell_width
        h = (max_row - min_row + 1) * cell_height

        # 处理边界情况，确保覆盖到图像边缘
        if max_col == 2:  # 包含最右列
            w = width - x
        if max_row == 2:  # 包含最下行
            h = height - y

        return (x, y, w, h)

    def _extract_region(
        self,
        image: Any,
        regions: Optional[List[int]] = None,
        debug_save_path: Optional[str] = None,
    ) -> Tuple[Any, Tuple[int, int]]:
        """
        从图像中提取指定的区域（合并后的单个区域）

        Args:
            image: OpenCV图像对象
            regions: 要提取的区域列表（1-9），会被合并成一个连续的矩形
            debug_save_path: 调试用，保存区域截图的路径

        Returns:
            (region_image, (offset_x, offset_y))
        """
        if image is None:
            return None, (0, 0)

        height, width = image.shape[:2]
        x, y, w, h = self._get_region_bounds((height, width), regions)

        region_img = image[y : y + h, x : x + w]

        # 调试：保存区域截图
        if debug_save_path:
            cv2.imwrite(debug_save_path, region_img)
            self.logger.debug(f"🔍 调试：区域截图已保存到 {debug_save_path}")
            self.logger.debug(f"   区域范围: x={x}, y={y}, w={w}, h={h}")
            self.logger.debug(f"   原图尺寸: {width}x{height}")

        return region_img, (x, y)

    def _get_region_description(self, regions: Optional[List[int]]) -> str:
        """
        获取区域的描述文字

        Args:
            regions: 区域列表

        Returns:
            区域描述，如 "区域[1,2,3]（上部）"
        """
        if not regions:
            return "全屏"

        # 合并区域
        min_row, max_row, min_col, max_col = self._merge_regions(regions)

        # 生成描述
        parts = []

        # 行描述
        if min_row == max_row:
            row_names = ["上部", "中部", "下部"]
            parts.append(row_names[min_row])
        elif min_row == 0 and max_row == 2:
            parts.append("全高")
        else:
            parts.append(f"第{min_row + 1}-{max_row + 1}行")

        # 列描述
        if min_col == max_col:
            col_names = ["左侧", "中间", "右侧"]
            parts.append(col_names[min_col])
        elif min_col == 0 and max_col == 2:
            parts.append("全宽")
        else:
            parts.append(f"第{min_col + 1}-{max_col + 1}列")

        region_str = ",".join(map(str, sorted(regions)))
        return f"区域[{region_str}]（{' '.join(parts)}）"

    def _empty_result(self) -> Dict[str, Any]:
        """返回空的查找结果"""
        return {
            "found": False,
            "center": None,
            "text": None,
            "confidence": None,
            "bbox": None,
            "total_matches": 0,
            "selected_index": 0,
        }

    def _adjust_coordinates_to_full_image(
        self, bbox: List[List[int]], offset: Tuple[int, int]
    ) -> List[List[int]]:
        """
        将区域内的坐标调整为原图中的坐标

        Args:
            bbox: 区域内的边界框坐标 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            offset: 区域在原图中的偏移量 (offset_x, offset_y)

        Returns:
            调整后的边界框坐标
        """
        offset_x, offset_y = offset
        adjusted_bbox = []
        for point in bbox:
            adjusted_bbox.append([point[0] + offset_x, point[1] + offset_y])
        return adjusted_bbox

    def _load_existing_cache(self):
        """
        加载缓存目录中已有的缓存文件
        """
        try:
            if not os.path.exists(self.cache_dir):
                return

            # 查找所有缓存文件对
            cache_files = os.listdir(self.cache_dir)
            cache_pairs = {}

            # 将图片和 JSON 文件配对
            for filename in cache_files:
                if filename.startswith("cache_") and filename.endswith(".png"):
                    # 提取缓存 ID
                    cache_id = filename.replace("cache_", "").replace(".png", "")
                    json_filename = f"cache_{cache_id}_res.json"

                    image_path = os.path.join(self.cache_dir, filename)
                    json_path = os.path.join(self.cache_dir, json_filename)

                    # 检查对应的 JSON 文件是否存在
                    if os.path.exists(json_path):
                        cache_pairs[cache_id] = (image_path, json_path)

            # 按 ID 排序并加载到缓存列表
            for cache_id in sorted(cache_pairs.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                self.ocr_cache.append(cache_pairs[cache_id])

            if self.ocr_cache:
                self.logger.debug(f"💾 加载了 {len(self.ocr_cache)} 个缓存文件")
        except Exception as e:
            self.logger.error(f"加载缓存失败: {e}")

    def _find_similar_cached_image(self, current_image_path, regions: Optional[List[int]] = None):
        """
        查找缓存中是否有相似的图片（使用新的哈希索引系统）

        Args:
            current_image_path (str): 当前图片路径
            regions (List[int], optional): 区域列表

        Returns:
            dict: 缓存的 OCR 结果，如果没有找到则返回 None
        """
        try:
            return self._find_similar_in_cache(image_path=current_image_path, regions=regions)
        except Exception as e:
            self.logger.error(f"查找相似缓存图片失败: {e}")
            return None

    def _save_to_cache(
        self,
        image_path: str,
        ocr_result: Dict[str, Any],
        regions: Optional[List[int]] = None,
    ):
        """
        保存 OCR 结果到缓存（仅写入 SQLite）

        Args:
            image_path (str): 图片路径
            ocr_result (dict): OCR 结果
            regions (List[int], optional): 区域列表
        """
        try:
            if not ocr_result:
                return
            self._save_to_cache_db(image_path=image_path, ocr_result=ocr_result, regions=regions)
        except Exception as e:
            self.logger.error(f"保存缓存失败: {e}")

    def _resize_image_for_ocr(self, image_path):
        """

        调整图片大小以加速 OCR 识别



        Args:

            image_path (str): 原始图片路径



        Returns:

            Tuple[str, float]: (调整后的图片路径, 缩放比例)

        """

        if not self.resize_image:
            return image_path, 1.0

        try:
            img = cv2.imread(image_path)

            if img is None:
                return image_path, 1.0

            height, width = img.shape[:2]

            # 如果图片宽度大于最大宽度，进行缩放

            if width > self.max_width:
                scale = self.max_width / width

                new_width = self.max_width

                new_height = int(height * scale)

                # 缩小图片

                resized_img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)

                # 保存到临时文件

                temp_path = image_path.replace(".png", "_resized.png")

                cv2.imwrite(temp_path, resized_img)

                self.logger.debug(
                    f"🔧 图片已缩小: {width}x{height} -> {new_width}x{new_height} (scale={scale:.2f})"
                )

                return temp_path, scale

            return image_path, 1.0

        except Exception as e:
            self.logger.warning(f"图片缩放失败: {e}，使用原图")

            return image_path, 1.0

    def _predict_with_timing(self, image_path):
        """
        执行 OCR 识别并记录耗时 (Remote PaddleX 3.0)

        Args:
            image_path (str): 图像文件路径

        Returns:
            OCR 识别结果 (dict: {rec_texts: [], rec_scores: [], dt_polys: []})
        """
        # 预处理：缩小图片
        processed_image_path, scale = self._resize_image_for_ocr(image_path)

        start_time = time.time()
        result = None

        try:
            # 1. 转换为 Base64
            with open(processed_image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            # 2. 构建符合 PaddleX 3.0 规范的 Payload
            payload = {
                "file": image_data,
                "fileType": 1,
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useTextlineOrientation": False,
            }

            # 3. 发送请求 (默认端口 8080)
            response = requests.post(self.ocr_url, json=payload, timeout=60)
            response.raise_for_status()
            json_resp = response.json()

            if json_resp.get("errorCode") == 0:
                # PaddleX 3.0 的结果嵌套在 result.ocrResults[0].prunedResult 中
                ocr_results = json_resp.get("result", {}).get("ocrResults", [])
                if ocr_results:
                    pruned = ocr_results[0].get("prunedResult", {})

                    dt_polys = pruned.get("dt_polys", [])

                    # 如果进行了缩放，需要还原坐标
                    if scale != 1.0 and dt_polys:
                        restored_polys = []
                        for poly in dt_polys:
                            # poly 是 [[x1, y1], [x2, y2], ...]
                            restored_poly = []
                            for point in poly:
                                restored_poly.append([int(point[0] / scale), int(point[1] / scale)])
                            restored_polys.append(restored_poly)
                        dt_polys = restored_polys

                    rec_texts = pruned.get("rec_texts", [])
                    if self.correction_map:
                        corrected_texts = []
                        for t in rec_texts:
                            corrected_texts.append(self.correction_map.get(t, t))
                        rec_texts = corrected_texts

                    # 转换格式为 OCRHelper 所需的格式
                    result = {
                        "rec_texts": rec_texts,
                        "rec_scores": pruned.get("rec_scores", []),
                        "dt_polys": dt_polys,
                    }

                else:
                    self.logger.warning("OCR Server returned empty ocrResults")
            else:
                self.logger.error(f"OCR Server Error: {json_resp.get('errorMsg')}")

        except Exception as e:
            self.logger.error(f"OCR Request Failed: {e}")

        elapsed_time = time.time() - start_time

        filename = os.path.basename(image_path)
        self.logger.debug(f"⏱️ OCR识别耗时: {elapsed_time:.3f}秒 (文件: {filename})")

        # 清理临时文件
        if processed_image_path != image_path and os.path.exists(processed_image_path):
            try:
                os.remove(processed_image_path)
            except Exception:
                pass

        return result

    def _get_or_create_ocr_result(
        self, image_path, use_cache=True, regions: Optional[List[int]] = None
    ):
        """
        获取或创建 OCR 识别结果（带缓存）

        Args:
            image_path (str): 图像文件路径
            use_cache (bool): 是否使用缓存，默认为 True
            regions (List[int], optional): 区域列表

        Returns:
            dict: OCR 结果
        """
        # 如果启用缓存，检查缓存中是否有相似图片
        if use_cache:
            cached_result = self._find_similar_cached_image(image_path, regions)
            if cached_result:
                return cached_result

        # 缓存未命中或禁用缓存，执行 OCR 识别
        result = self._predict_with_timing(image_path)

        if result:
            # 如果启用缓存，同时保存到缓存
            if use_cache:
                self._save_to_cache(image_path, result, regions)

            return result

        return None

    def find_text_in_image(
        self,
        image_path,
        target_text,
        confidence_threshold=0.5,
        occurrence=1,
        use_cache=True,
        regions: Optional[List[int]] = None,
        debug_save_path: Optional[str] = None,
        return_all=False,
    ):
        """
        在指定图像中查找目标文字的位置

        Args:
            image_path (str): 图像文件路径
            target_text (str): 要查找的目标文字
            confidence_threshold (float): 置信度阈值 (0-1)
            occurrence (int): 指定点击第几个出现的文字 (1-based)，默认为1
            use_cache (bool): 是否使用缓存，默认为 True
            regions (List[int], optional): 要搜索的区域列表（1-9）
            debug_save_path (str, optional): 调试用
            return_all (bool): 是否返回所有匹配项的列表

        Returns:
            dict | list: 如果 return_all=False, 返回查找结果字典;
                         如果 return_all=True, 返回包含所有匹配字典的列表
        """
        try:
            # 如果指定了区域，使用区域搜索
            if regions is not None:
                return self._find_text_in_regions(
                    image_path,
                    target_text,
                    confidence_threshold,
                    occurrence,
                    regions,
                    debug_save_path,
                    use_cache,
                    return_all=return_all,
                )

            # 获取或创建 OCR 结果
            ocr_data = self._get_or_create_ocr_result(
                image_path, use_cache=use_cache, regions=regions
            )

            if not ocr_data:
                return [] if return_all else self._empty_result()

            # 从 OCR 结果中查找目标文字
            return self._find_text_in_json(
                ocr_data, target_text, confidence_threshold, occurrence, return_all=return_all
            )

        except Exception as e:
            self.logger.error(f"图像OCR识别出错: {e}")
            return [] if return_all else self._empty_result()

    def capture_and_find_all_texts(
        self,
        target_text,
        confidence_threshold=0.5,
        use_cache=True,
        regions: Optional[List[int]] = None,
    ):
        """
        截图并查找所有匹配的目标文字

        Args:
            target_text (str): 要查找的目标文字
            confidence_threshold (float): 置信度阈值 (0-1)
            use_cache (bool): 是否使用缓存，默认为 True
            regions (List[int], optional): 要搜索的区域列表

        Returns:
            list: 包含所有匹配项信息的列表
        """
        if not self.snapshot_func:
            raise RuntimeError("snapshot_func is not set. Cannot capture screenshot.")

        # 内部复用 capture_and_find_text 的部分逻辑（截图与重试）
        # 但传递 return_all=True

        # 为了简洁，这里直接调用 find_text_in_image 逻辑
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        screenshot_path = os.path.join(self.temp_dir, f"all_texts_{timestamp}_{unique_id}.png")

        try:
            self.snapshot_func(filename=screenshot_path)
            results = self.find_text_in_image(
                screenshot_path,
                target_text,
                confidence_threshold,
                use_cache=use_cache,
                regions=regions,
                return_all=True,
            )
            return results
        finally:
            if self.delete_temp_screenshots and os.path.exists(screenshot_path):
                try:
                    os.remove(screenshot_path)
                except Exception:
                    pass

    def _find_text_in_regions(
        self,
        image_path: str,
        target_text: str,
        confidence_threshold: float,
        occurrence: int,
        regions: List[int],
        debug_save_path: Optional[str] = None,
        use_cache: bool = True,
        return_all: bool = False,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        在指定区域中查找文字（内部方法）
        """
        try:
            # 读取图像
            image = cv2.imread(image_path)
            if image is None:
                self.logger.error(f"无法读取图像: {image_path}")
                return [] if return_all else self._empty_result()

            # 提取合并后的区域
            region_img, offset = self._extract_region(image, regions, debug_save_path)
            if region_img is None:
                self.logger.warning("未能提取区域")
                return [] if return_all else self._empty_result()

            # 显示区域信息
            region_desc = self._get_region_description(regions)
            self.logger.debug(f"🔍 在{region_desc}搜索文字: '{target_text}'")

            # 初始化结果
            result = None
            cache_used = False
            elapsed_time = 0

            # 只有在使用缓存时才尝试从缓存读取
            if use_cache:
                cached_result = self._find_similar_in_cache(image=region_img, regions=regions)
                if cached_result:
                    self.logger.debug(f"?? 区域缓存命中: {region_desc}")
                    result = [cached_result]
                    cache_used = True

            # 如果没有命中缓存或不使用缓存，进行OCR识别
            if result is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = str(uuid.uuid4())[:8]
                temp_region_path = os.path.join(
                    self.temp_dir, f"region_{timestamp}_{unique_id}.png"
                )
                cv2.imwrite(temp_region_path, region_img)

                # 对区域进行OCR识别 (Remote)
                ocr_dict = self._predict_with_timing(temp_region_path)

                if temp_region_path and os.path.exists(temp_region_path):
                    try:
                        os.remove(temp_region_path)
                    except Exception:
                        pass

                if ocr_dict:
                    result = [ocr_dict]  # 包装成列表以兼容后续逻辑

                    # 保存OCR结果到缓存（仅在使用缓存时）
                    if use_cache:
                        self._save_to_cache_db(
                            image=region_img, ocr_result=ocr_dict, regions=regions
                        )
                else:
                    result = []

            if not result or len(result) == 0:
                return [] if return_all else self._empty_result()

            # 收集所有匹配结果
            all_matches = []
            for res in result:
                res_cache_used = cache_used
                res_elapsed_time = elapsed_time

                # 安全地获取字段，兼容 dict 和 object
                if isinstance(res, dict):
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    dt_polys = res.get("dt_polys", [])
                else:
                    rec_texts = getattr(res, "rec_texts", [])
                    rec_scores = getattr(res, "rec_scores", [])
                    dt_polys = getattr(res, "dt_polys", [])

                # 查找匹配的文字
                for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                    if score >= confidence_threshold and target_text in text:
                        if i < len(dt_polys):
                            poly = dt_polys[i]

                            # 调整坐标到原图
                            adjusted_poly = self._adjust_coordinates_to_full_image(poly, offset)

                            # 计算中心点
                            x_coords = [point[0] for point in adjusted_poly]
                            y_coords = [point[1] for point in adjusted_poly]
                            center_x = int(sum(x_coords) / len(x_coords))
                            center_y = int(sum(y_coords) / len(y_coords))

                            all_matches.append(
                                {
                                    "center": (center_x, center_y),
                                    "text": text,
                                    "confidence": score,
                                    "bbox": adjusted_poly,
                                    "index": len(all_matches) + 1,
                                    "cache_used": res_cache_used,
                                    "elapsed_time": res_elapsed_time,
                                }
                            )

            if return_all:
                return all_matches

            # 处理匹配结果
            total_matches = len(all_matches)
            if total_matches == 0:
                return self._empty_result()

            # 选择指定的匹配项
            if occurrence > total_matches:
                selected_match = all_matches[-1]
                selected_index = total_matches
            else:
                selected_match = all_matches[occurrence - 1]
                selected_index = occurrence

            return {
                "found": True,
                "center": selected_match["center"],
                "text": selected_match["text"],
                "confidence": selected_match["confidence"],
                "bbox": selected_match["bbox"],
                "total_matches": total_matches,
                "selected_index": selected_index,
            }

        except Exception as e:
            self.logger.error(f"区域搜索出错: {e}")
            return [] if return_all else self._empty_result()

    def _find_text_in_json(
        self, json_file_path, target_text, confidence_threshold=0.5, occurrence=1, return_all=False
    ):
        """
        从OCR结果中查找目标文字
        """
        try:
            # 读取JSON文件或直接使用结果字典
            if isinstance(json_file_path, dict):
                data = json_file_path
            else:
                with open(json_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

            # 获取识别的文字列表和对应的坐标框
            rec_texts = data.get("rec_texts", [])
            rec_scores = data.get("rec_scores", [])
            dt_polys = data.get("dt_polys", [])  # 检测框坐标

            # 收集所有匹配的文字
            matches = []
            for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                # 检查置信度和文字匹配（识别出的文字包含目标文字）
                if score >= confidence_threshold and target_text in text:
                    # 获取对应的坐标框
                    if i < len(dt_polys):
                        poly = dt_polys[i]

                        # 计算中心点坐标
                        x_coords = [point[0] for point in poly]
                        y_coords = [point[1] for point in poly]
                        center_x = int(sum(x_coords) / len(x_coords))
                        center_y = int(sum(y_coords) / len(y_coords))

                        matches.append(
                            {
                                "center": (center_x, center_y),
                                "text": text,
                                "confidence": score,
                                "bbox": poly,
                                "index": len(matches) + 1,
                            }
                        )

            if return_all:
                return matches

            total_matches = len(matches)
            if total_matches == 0:
                return self._empty_result()

            # 选择指定的匹配项
            if occurrence > total_matches:
                selected_match = matches[-1]
                selected_index = total_matches
            else:
                selected_match = matches[occurrence - 1]
                selected_index = occurrence

            return {
                "found": True,
                "center": selected_match["center"],
                "text": selected_match["text"],
                "confidence": selected_match["confidence"],
                "bbox": selected_match["bbox"],
                "total_matches": total_matches,
                "selected_index": selected_index,
            }

        except Exception as e:
            self.logger.error(f"处理OCR数据时出错: {e}")
            return [] if return_all else self._empty_result()

    def capture_and_find_text(
        self,
        target_text,
        confidence_threshold=0.5,
        occurrence=1,
        use_cache=True,
        regions: Optional[List[int]] = None,
        debug_save_path: Optional[str] = None,
        screenshot_path: Optional[str] = None,
    ):
        """
        截图并查找指定文字，如果缓存未找到会自动重试
        """
        if screenshot_path:
            final_path = screenshot_path
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            final_path = os.path.join(self.temp_dir, f"capture_{timestamp}_{unique_id}.png")

        try:
            if not screenshot_path:
                if not self.snapshot_func:
                    raise RuntimeError("snapshot_func is not set. Cannot capture screenshot.")
                self.snapshot_func(filename=final_path)
            
            # 第一次尝试
            result = self.find_text_in_image(
                final_path,
                target_text,
                confidence_threshold,
                occurrence,
                use_cache=use_cache,
                regions=regions,
                debug_save_path=debug_save_path,
                return_all=False,
            )
            
            # 重试逻辑：如果启用了缓存但没找到，尝试禁用缓存重新识别
            if use_cache and (not result or not result.get("found")):
                self.logger.debug(f"缓存未找到文字 '{target_text}'，尝试禁用缓存重新识别...")
                
                # 重新截图（如果不是提供的固定路径）
                if not screenshot_path and self.snapshot_func:
                    self.snapshot_func(filename=final_path)
                
                # 手动调用 predict 和 cache 保存逻辑
                # 因为 find_text_in_image(use_cache=False) 不会保存缓存
                # 我们希望刷新缓存
                
                # 1. 强制重新识别
                ocr_data = self._get_or_create_ocr_result(
                    final_path, 
                    use_cache=False, 
                    regions=None # 即使指定了区域，也建议全屏识别并缓存，以便后续复用
                )
                
                if ocr_data:
                    # 2. 手动保存到缓存
                    self._save_to_cache(final_path, ocr_data, None)
                    
                    # 3. 从新数据中查找
                    if regions:
                        # 如果指定了区域，需要重新从全屏数据中提取区域逻辑
                        # 但 _find_text_in_regions 逻辑比较复杂，涉及重新切图
                        # 这里简单起见，如果指定了regions，我们再次调用 find_text_in_image(use_cache=True)
                        # 因为现在缓存已经有了新数据
                         result = self.find_text_in_image(
                            final_path,
                            target_text,
                            confidence_threshold,
                            occurrence,
                            use_cache=True, # 现在可以用缓存了
                            regions=regions,
                            debug_save_path=debug_save_path,
                            return_all=False,
                        )
                    else:
                        result = self._find_text_in_json(
                            ocr_data, 
                            target_text, 
                            confidence_threshold, 
                            occurrence, 
                            return_all=False
                        )
                else:
                    result = self._empty_result()
            
            return result
        finally:
            if not screenshot_path and self.delete_temp_screenshots and os.path.exists(final_path):
                try:
                    os.remove(final_path)
                except Exception:
                    pass

    def find_and_click_text(
        self,
        target_text,
        confidence_threshold=0.5,
        occurrence=1,
        use_cache=True,
        regions: Optional[List[int]] = None,
    ):
        """
        查找并点击文字 (依赖 airtest)
        """
        result = self.capture_and_find_text(
            target_text,
            confidence_threshold=confidence_threshold,
            occurrence=occurrence,
            use_cache=use_cache,
            regions=regions,
        )
        
        if result and result.get("found"):
            center = result.get("center")
            if center:
                try:
                    from airtest.core.api import touch
                    touch(center)
                    self.logger.info(f"点击了文字 '{target_text}' at {center}")
                    return True
                except ImportError:
                    self.logger.error("未安装 airtest，无法执行点击操作")
                    return False
                except Exception as e:
                    self.logger.error(f"点击文字 '{target_text}' 失败: {e}")
                    return False
        
        return False

    def capture_and_get_all_texts(
        self,
        use_cache=True,
        regions: Optional[List[int]] = None,
    ):
        """
        截图并获取所有识别到的文字信息

        Args:
            use_cache (bool): 是否使用缓存
            regions (List[int], optional): 要获取的区域列表

        Returns:
            list: 包含所有文字信息的列表
        """
        if not self.snapshot_func:
            raise RuntimeError("snapshot_func is not set. Cannot capture screenshot.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        screenshot_path = os.path.join(self.temp_dir, f"get_all_{timestamp}_{unique_id}.png")

        try:
            self.snapshot_func(filename=screenshot_path)
            # 使用现有逻辑获取所有文本
            if regions:
                # 借用 _find_text_in_regions 逻辑，传递空字符串匹配所有
                return self._find_text_in_regions(
                    screenshot_path,
                    target_text="",
                    confidence_threshold=0.0,
                    occurrence=1,
                    regions=regions,
                    use_cache=use_cache,
                    return_all=True,
                )
            else:
                # 获取全屏 OCR 结果
                ocr_data = self._get_or_create_ocr_result(
                    screenshot_path, use_cache=use_cache, regions=None
                )
                if not ocr_data:
                    return []
                # 从数据中提取所有项
                return self._find_text_in_json(
                    ocr_data, target_text="", confidence_threshold=0.0, return_all=True
                )
        finally:
            if self.delete_temp_screenshots and os.path.exists(screenshot_path):
                try:
                    os.remove(screenshot_path)
                except Exception:
                    pass

    def find_all_matching_texts(self, image_path, target_text, confidence_threshold=0.5):
        """
        查找图像中所有匹配的文字

        Args:
            image_path (str): 图像文件路径
            target_text (str): 要查找的目标文字
            confidence_threshold (float): 置信度阈值 (0-1)

        Returns:
            list: 包含所有匹配文字信息的列表，每个元素包含center, text, confidence, bbox
        """
        try:
            # OCR 识别
            result = self._predict_with_timing(image_path)

            if not result:
                self.logger.warning(f"OCR识别结果为空: {image_path}")
                return []

            # 保存识别结果到JSON (可选，保持兼容性)
            json_filename = os.path.basename(image_path).replace(".png", "_res.json")
            json_file = os.path.join(self.output_dir, json_filename)

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # 直接从内存结果处理，或者读取刚才保存的文件
            # 为了复用逻辑，这里复用 _find_all_matching_texts_in_json
            return self._find_all_matching_texts_in_json(
                json_file, target_text, confidence_threshold
            )

        except Exception as e:
            self.logger.error(f"查找所有匹配文字时出错: {e}")
            return []

    def _find_all_matching_texts_in_json(
        self, json_file_path, target_text, confidence_threshold=0.5
    ):
        """
        从JSON文件中查找所有匹配的文字

        Args:
            json_file_path (str): JSON文件路径
            target_text (str): 要查找的目标文字
            confidence_threshold (float): 置信度阈值 (0-1)

        Returns:
            list: 所有匹配的文字信息列表
        """
        try:
            with open(json_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            rec_texts = data.get("rec_texts", [])
            rec_scores = data.get("rec_scores", [])
            dt_polys = data.get("dt_polys", [])

            matches = []
            for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                # 检查置信度和文字匹配（识别出的文字包含目标文字）
                if score >= confidence_threshold and target_text in text:
                    if i < len(dt_polys):
                        poly = dt_polys[i]
                        x_coords = [point[0] for point in poly]
                        y_coords = [point[1] for point in poly]
                        center_x = int(sum(x_coords) / len(x_coords))
                        center_y = int(sum(y_coords) / len(y_coords))

                        matches.append(
                            {
                                "center": (center_x, center_y),
                                "text": text,
                                "confidence": score,
                                "bbox": poly,
                                "index": len(matches) + 1,
                            }
                        )

            return matches

        except Exception as e:
            self.logger.error(f"处理JSON文件时出错: {e}")
            return []

    def get_all_texts_from_image(self, image_path):
        """
        获取图像中所有识别到的文字信息

        Args:
            image_path (str): 图像文件路径

        Returns:
            list: 包含所有文字信息的列表，每个元素为字典包含text, confidence, center, bbox
        """
        try:
            # OCR 识别
            result = self._predict_with_timing(image_path)

            if not result:
                self.logger.warning(f"OCR识别结果为空: {image_path}")
                return []

            # 保存识别结果到JSON
            json_filename = os.path.basename(image_path).replace(".png", "_res.json")
            json_file = os.path.join(self.output_dir, json_filename)

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # 从JSON文件读取所有文字
            return self._get_all_texts_from_json(json_file)

        except Exception as e:
            self.logger.error(f"获取图像文字信息出错: {e}")
            return []

    def _get_all_texts_from_json(self, json_file_path):
        """
        从JSON文件中获取所有文字信息

        Args:
            json_file_path (str): JSON文件路径

        Returns:
            list: 所有文字信息列表
        """
        try:
            with open(json_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            rec_texts = data.get("rec_texts", [])
            rec_scores = data.get("rec_scores", [])
            dt_polys = data.get("dt_polys", [])

            texts_info = []

            for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                if i < len(dt_polys):
                    poly = dt_polys[i]
                    x_coords = [point[0] for point in poly]
                    y_coords = [point[1] for point in poly]
                    center_x = int(sum(x_coords) / len(x_coords))
                    center_y = int(sum(y_coords) / len(y_coords))

                    texts_info.append(
                        {
                            "text": text,
                            "confidence": score,
                            "center": (center_x, center_y),
                            "bbox": poly,
                        }
                    )

            return texts_info

        except Exception as e:
            self.logger.error(f"读取JSON文件出错: {e}")
            return []