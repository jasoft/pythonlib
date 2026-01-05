import sys
import os
import unittest
from typing import Dict, Any, cast
from unittest.mock import MagicMock, patch
import shutil

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from vibe_ocr import OCRHelper

class TestOCRHelper(unittest.TestCase):
    def setUp(self):
        self.output_dir = "test_output"
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)
        self.ocr = OCRHelper(output_dir=self.output_dir)

    def tearDown(self):
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)

    @patch("requests.post")
    @patch("cv2.imread")
    @patch("cv2.resize")
    @patch("cv2.imwrite")
    def test_find_text_in_image(self, mock_imwrite, mock_resize, mock_imread, mock_post):
        # Mock OpenCV
        mock_img = MagicMock()
        mock_img.shape = (100, 100, 3)
        mock_imread.return_value = mock_img
        mock_resize.return_value = mock_img

        # Mock OCR Server Response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {
                        "prunedResult": {
                            "rec_texts": ["Hello", "World"],
                            "rec_scores": [0.99, 0.98],
                            "dt_polys": [
                                [[10, 10], [50, 10], [50, 30], [10, 30]],
                                [[60, 10], [100, 10], [100, 30], [60, 30]]
                            ]
                        }
                    }
                ]
            }
        }
        mock_post.return_value = mock_response

        # Create a dummy file to avoid file not found error in _resize_image_for_ocr or other checks
        # But wait, OCRHelper uses cv2.imread(image_path), if we mock it, we don't need real file existence
        # EXCEPT explicit os.path.exists checks if any.
        # _resize_image_for_ocr calls cv2.imread(image_path).
        # _predict_with_timing calls open(processed_image_path, "rb"). This needs a real file if processed_image_path is real.
        
        # Let's bypass the file reading in _predict_with_timing by mocking open or just creating a dummy file.
        dummy_image_path = "test_image.png"
        with open(dummy_image_path, "wb") as f:
            f.write(b"dummy image data")

        try:
            result = cast(Dict[str, Any], self.ocr.find_text_in_image(dummy_image_path, "Hello", use_cache=False))
            
            self.assertIsInstance(result, dict)
            self.assertTrue(result["found"])
            self.assertEqual(result["text"], "Hello")
        finally:
            if os.path.exists(dummy_image_path):
                os.remove(dummy_image_path)

if __name__ == "__main__":
    unittest.main()
