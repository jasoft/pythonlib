# -*- encoding=utf8 -*-
"""
Game Actions Module
Encapsulates OCR-based finding and clicking operations, providing a declarative API.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    from airtest.core.api import sleep as airtest_sleep
    from airtest.core.api import touch as airtest_touch
    HAS_AIRTEST = True
except ImportError:
    HAS_AIRTEST = False
    
    def airtest_sleep(s): 
        time.sleep(s)
    
    def airtest_touch(p): 
        logging.getLogger("vibe_ocr.game_actions").warning("Airtest not installed, touch ignored.")

logger = logging.getLogger("vibe_ocr.game_actions")


class GameElement(dict):
    """
    Represents a game element (based on OCR result).
    """

    def __init__(self, data: Dict[str, Any], action_context: "GameActions"):
        super().__init__(data or {})
        self.action_context = action_context
        # Ensure result.get("found") returns True for found elements
        self["found"] = True

    def __bool__(self):
        return self.get("found", False)

    @staticmethod
    def empty(action_context: "GameActions") -> "GameElement":
        return NullGameElement(action_context)

    @property
    def center(self) -> Tuple[int, int]:
        if self.get("center"):
            return tuple(self["center"])
        return (0, 0)

    @property
    def text(self):
        return self.get("text")

    @property
    def confidence(self):
        return self.get("confidence", 0.0)

    def click(self) -> "GameElement":
        """Click the center of this element."""
        if self.center:
            self.action_context.touch(self.center)
            self.action_context.sleep(self.action_context.click_interval)
        else:
            logger.warning("Attempted to click non-existent element")
        return self

    def offset_click(self, x: int = 0, y: int = 0) -> "GameElement":
        """Click with offset."""
        if self.center:
            pos = (self.center[0] + x, self.center[1] + y)
            self.action_context.touch(pos)
            self.action_context.sleep(self.action_context.click_interval)
        return self

    def sleep(self, seconds: float) -> "GameElement":
        self.action_context.sleep(seconds)
        return self

    def __repr__(self):
        return f"GameElement(text='{self.text}', center={self.center})"


class NullGameElement(GameElement):
    """
    Null Object Pattern for GameElement.
    """

    def __init__(self, action_context: "GameActions"):
        super().__init__({}, action_context)
        self["found"] = False

    def __bool__(self):
        return False

    def __repr__(self):
        return "NullGameElement()"

    @property
    def center(self) -> Tuple[int, int]:
        return (0, 0)

    def click(self) -> "GameElement":
        return self

    def offset_click(self, x: int = 0, y: int = 0) -> "GameElement":
        return self

    def sleep(self, seconds: float) -> "GameElement":
        return self


class GameElementCollection(list):
    """
    A collection of GameElements, supporting chainable operations.
    """

    def __init__(self, elements: List[Dict[str, Any]], action_context: "GameActions"):
        super().__init__([GameElement(e, action_context) for e in elements])
        self.action_context = action_context

    def filter(self, predicate: Callable[[GameElement], bool]) -> "GameElementCollection":
        return GameElementCollection([e for e in self if predicate(e)], self.action_context)

    def contains(self, text: str) -> "GameElementCollection":
        return self.filter(lambda e: text in (e.text or ""))

    def equals(self, text: str) -> "GameElementCollection":
        return self.filter(lambda e: e.text == text)

    def min_confidence(self, threshold: float) -> "GameElementCollection":
        return self.filter(lambda e: e.confidence >= threshold)

    def first(self) -> GameElement:
        return self[0] if self else GameElement.empty(self.action_context)

    def last(self) -> GameElement:
        return self[-1] if self else GameElement.empty(self.action_context)

    def get(self, index: int) -> GameElement:
        """
        Get element at index. Returns last element if index out of bounds (legacy behavior).
        """
        if not self:
            return GameElement.empty(self.action_context)

        if index >= len(self):
            return self[-1]

        if 0 <= index < len(self):
            return self[index]

        return GameElement.empty(self.action_context)

    def map(self, func: Callable[[GameElement], Any]) -> List[Any]:
        return [func(e) for e in self]

    def each(self, func: Callable[[GameElement], None]) -> "GameElementCollection":
        for e in self:
            func(e)
        return self

    def click_all(self) -> "GameElementCollection":
        for e in self:
            e.click()
        return self

    def is_empty(self) -> bool:
        return len(self) == 0

    def size(self) -> int:
        return len(self)


class GameActions:
    """
    Encapsulates game finding and interaction logic.
    """

    def __init__(self, ocr_helper, click_interval=1):
        """
        Args:
            ocr_helper: An instance of OCRHelper.
            click_interval: Time to wait after a click (seconds).
        """
        self.ocr_helper = ocr_helper
        self.click_interval = click_interval

    def sleep(self, seconds: float, reason: str = ""):
        if reason:
            logger.info(f"Sleep {seconds}s: {reason}")
        airtest_sleep(seconds)

    def touch(self, pos):
        logger.debug(f"Touch: {pos}")
        airtest_touch(pos)

    def find_all(
        self,
        use_cache: bool = True,
        regions: Optional[List[int]] = None,
    ) -> GameElementCollection:
        """
        Get all text elements on the current screen.
        """
        if self.ocr_helper is None:
            logger.error("OCRHelper not initialized")
            return GameElementCollection([], self)

        # Ensure OCRHelper has capture_and_get_all_texts
        if not hasattr(self.ocr_helper, "capture_and_get_all_texts"):
             logger.error("OCRHelper instance does not support 'capture_and_get_all_texts'")
             return GameElementCollection([], self)

        results = self.ocr_helper.capture_and_get_all_texts(
            use_cache=use_cache,
            regions=regions,
        )

        return GameElementCollection(results, self)

    def find(
        self,
        text: str,
        timeout: float = 1,
        similarity_threshold: float = 0.7,
        occurrence: int = 1,
        use_cache: bool = True,
        regions: Optional[List[int]] = None,
        raise_exception: bool = False,
    ) -> GameElement:
        """
        Find a specific text element.
        """
        start_time = time.time()
        
        first_attempt = True
        while first_attempt or (time.time() - start_time < timeout):
            first_attempt = False
            
            # Use collection filtering
            el = (
                self.find_all(use_cache=use_cache, regions=regions)
                .contains(text)
                .min_confidence(similarity_threshold)
                .get(occurrence - 1)
            )

            if el:
                logger.info(f"Found: '{text}' at {el.center}")
                return el

            if time.time() - start_time >= timeout:
                break
            time.sleep(0.1)

        msg = f"Not found: '{text}'"
        logger.debug(msg)
        if raise_exception:
            raise TimeoutError(msg)
        return GameElement.empty(self)

    def text_exists(
        self,
        texts: Union[str, List[str]],
        similarity_threshold: float = 0.7,
        use_cache: bool = True,
        regions: Optional[List[int]] = None,
    ) -> GameElement:
        """
        Check if any of the provided texts exist.
        """
        if self.ocr_helper is None:
            return GameElement.empty(self)

        texts_to_check = [texts] if isinstance(texts, str) else list(texts)
        if not texts_to_check:
            return GameElement.empty(self)

        collection = self.find_all(use_cache=use_cache, regions=regions).min_confidence(
            similarity_threshold
        )

        for text in texts_to_check:
            el = collection.contains(text).first()
            if el:
                logger.info(f"text_exists Found: '{text}' at {el.center}")
                return el

        return GameElement.empty(self)

    # --- Convenience Methods ---

    def find_text(self, *args, **kwargs) -> GameElement:
        return self.find(*args, **kwargs)

    def find_all_texts(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Backwards compatible list return."""
        if args and isinstance(args[0], str):
            text = args[0]
            kwargs.pop("similarity_threshold", None)
            collection = self.find_all(**kwargs).contains(text)
            return list(collection) # list of GameElement (dicts)

        return list(self.find_all(**kwargs))

    def find_text_and_click(self, text: str, **kwargs) -> GameElement:
        el = self.find(text, **kwargs)
        if el:
            el.click()
        return el
