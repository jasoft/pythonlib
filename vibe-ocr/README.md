# Vibe-OCR

An intelligent, decoupled OCR helper library designed for automation tasks. It leverages a remote PaddleOCR server for text recognition and includes a robust local caching system using SQLite to optimize performance and reduce repeated network requests.

## Installation

```bash
pip install vibe-ocr
```

## Features

- **Decoupled Architecture**: Uses a remote OCR server (PaddleOCR) to offload heavy computation.
- **Smart Caching**: Local SQLite database caches OCR results for identical images/regions, significantly speeding up repeated checks.
- **Snapshot Integration**: Built-in support for taking screenshots (via `airtest` by default) or custom snapshot functions.
- **Retry Logic**: Automatic retry without cache if text is not found initially.
- **Debug Friendly**: Options to save debug images with detected regions.

## Usage Guide

### 1. Initialization

Initialize the `OCRHelper`. You can customize the output directory for logs/cache and inject a custom snapshot function (useful for testing or non-Airtest environments).

```python
from vibe_ocr import OCRHelper

# Basic initialization
ocr = OCRHelper(output_dir="output")

# Custom snapshot function (e.g., for testing or different frameworks)
def my_snapshot_func(filename):
    # logic to save screenshot to filename
    pass

ocr = OCRHelper(output_dir="output", snapshot_func=my_snapshot_func)
```

### 2. Finding Text

The most common operation is to capture a screen and find specific text.

```python
# Capture screen and find text "Login"
result = ocr.capture_and_find_text(
    "Login",
    confidence_threshold=0.7,
    occurrence=1,   # 1st occurrence
    use_cache=True  # Use cache if screen hasn't changed
)

if result and result.get("found"):
    print(f"Found 'Login' at: {result['center']}")
    print(f"Bounding Box: {result['bbox']}")
else:
    print("Text not found.")
```

### 3. Finding and Clicking

A convenience method to find text and simulate a touch/click action (requires `airtest` or compatible environment).

```python
# Find "Confirm" and click it if found
clicked = ocr.find_and_click_text(
    "Confirm",
    confidence_threshold=0.6
)
```

### 4. Advanced: Batch OCR & Regions

You can optimize performance by searching only within specific regions.

```python
# Search only in the top-left region [x1, y1, x2, y2]
ocr.capture_and_find_text("Player Name", regions=[0, 0, 200, 100])
```

## Configuration

### Environment Variables

*   `OCR_SERVER_URL`: The URL of the PaddleOCR server. Defaults to `http://localhost:8080/ocr`.

### Constructor Parameters

*   `output_dir`: Directory to store cache (sqlite db) and debug images.
*   `snapshot_func`: Callable to take screenshots. Defaults to `airtest.core.api.snapshot`.
*   `delete_temp_screenshots`: Whether to delete temporary screenshot files after processing (Default: `True`).
*   `resize_image`: Resize large images before sending to OCR server to improve speed (Default: `True`).

## Caching Mechanism

`vibe-ocr` calculates a perceptual hash (dhash) of the screenshot. If a similar image exists in the `sqlite` cache, it retrieves the OCR result locally instead of calling the server. This is critical for high-frequency loops in automation scripts.