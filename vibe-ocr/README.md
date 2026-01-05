# Vibe-OCR

A decoupled OCR helper library using PaddleOCR (via remote server) and SQLite caching.

## Installation

```bash
pip install vibe-ocr
```

## Usage

```python
from vibe_ocr import OCRHelper

ocr = OCRHelper()
result = ocr.find_text_in_image("screenshot.png", "Target Text")
print(result)
```

## Configuration

Set `OCR_SERVER_URL` environment variable to point to your PaddleOCR server. Default is `http://localhost:8080/ocr`.
