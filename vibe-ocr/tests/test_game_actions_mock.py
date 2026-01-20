import sys
import os
import unittest
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from vibe_ocr.game_actions import GameActions, GameElement, GameElementCollection

class TestGameActions(unittest.TestCase):
    def setUp(self):
        self.mock_ocr = MagicMock()
        self.actions = GameActions(self.mock_ocr)
        # Mock touch/sleep to avoid actual calls
        self.actions.touch = MagicMock()
        self.actions.sleep = MagicMock()

    def test_find_all_returns_collection(self):
        # Setup mock return
        self.mock_ocr.capture_and_get_all_texts.return_value = [
            {"text": "Start", "confidence": 0.9, "center": (10, 10), "bbox": []},
            {"text": "Exit", "confidence": 0.95, "center": (20, 20), "bbox": []}
        ]

        collection = self.actions.find_all()
        
        self.assertIsInstance(collection, GameElementCollection)
        self.assertEqual(len(collection), 2)
        self.assertEqual(collection[0].text, "Start")
        self.assertTrue(collection[0]) # Should be truthy

    def test_filtering_chain(self):
        self.mock_ocr.capture_and_get_all_texts.return_value = [
            {"text": "Start Game", "confidence": 0.9},
            {"text": "Start Options", "confidence": 0.5},
            {"text": "Exit", "confidence": 0.95}
        ]

        # Test filtering
        result = self.actions.find_all().contains("Start").min_confidence(0.8)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Start Game")

    def test_click_chain(self):
        self.mock_ocr.capture_and_get_all_texts.return_value = [
             {"text": "Button", "confidence": 0.9, "center": (100, 100)}
        ]
        
        self.actions.find_all().first().click()
        
        self.actions.touch.assert_called_with((100, 100))

    def test_find_timeout(self):
        # Mock capture_and_get_all_texts to return empty first, then result
        # Note: GameActions.find calls find_all repeatedly
        
        # Side effect: first call empty, second call has result
        self.mock_ocr.capture_and_get_all_texts.side_effect = [
            [],
            [{"text": "Target", "confidence": 0.9, "center": (50, 50)}]
        ]
        
        el = self.actions.find("Target", timeout=1)
        
        self.assertTrue(el)
        self.assertEqual(el.text, "Target")
        self.assertEqual(self.mock_ocr.capture_and_get_all_texts.call_count, 2)

if __name__ == "__main__":
    unittest.main()
