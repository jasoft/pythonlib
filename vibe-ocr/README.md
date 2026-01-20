# Vibe-OCR

An intelligent, decoupled OCR helper library designed for automation tasks. It leverages a remote PaddleOCR server for text recognition and includes a robust local caching system using SQLite to optimize performance and reduce repeated network requests.

## Installation

```bash
pip install vibe-ocr
```

## Features

- **Decoupled Architecture**: Uses a remote OCR server (PaddleOCR) to offload heavy computation.
- **Smart Caching**: Local SQLite database caches OCR results for identical images/regions, significantly speeding up repeated checks.
- **Declarative API**: High-level `GameActions` API for chaining operations (filter, map, click).
- **Snapshot Integration**: Built-in support for taking screenshots (via `airtest` by default) or custom snapshot functions.
- **Retry Logic**: Automatic retry without cache if text is not found initially.

## Usage Guide

### 1. Initialization

Initialize the `OCRHelper`.

```python
from vibe_ocr import OCRHelper

# Basic initialization
ocr = OCRHelper(output_dir="output")
```

### 2. High-Level Declarative API (Recommended)

The `GameActions` class provides a powerful, fluent interface for finding and interacting with game elements. This is the preferred way to write automation scripts.

```python
from vibe_ocr import OCRHelper, GameActions

ocr = OCRHelper(output_dir="output")
actions = GameActions(ocr)

# Find all texts, filter for "Item", and click the first one
actions.find_all() \
       .contains("Item") \
       .min_confidence(0.8) \
       .first() \
       .click()

# Find specific text with timeout (retries automatically)
actions.find("Start Game", timeout=5).click()

# Check if text exists
if actions.text_exists("Game Over"):
    print("Game ended")

# Batch operations
actions.find_all() \
       .filter(lambda e: "Coin" in e.text) \
       .click_all()
```

### 3. Low-Level API: Finding Text

You can also use the `OCRHelper` directly for simple tasks.

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
else:
    print("Text not found.")
```

### 4. Low-Level API: Finding and Clicking

A convenience method to find text and simulate a touch/click action (requires `airtest` installed).

```python
# Find "Confirm" and click it if found
clicked = ocr.find_and_click_text(
    "Confirm",
    confidence_threshold=0.6
)
```

## Configuration

### 1. PaddleX OCR Server (Required)

This library requires a running PaddleOCR server (PaddleX 3.0+). You can easily deploy it using Docker:

```bash
docker run -d --name paddlex \
  --shm-size=8g \
  --network=host \
  ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlex/paddlex:paddlex3.3.11-paddlepaddle3.2.0-cpu \
  sh -lc "paddlex --install serving && paddlex --serve --pipeline OCR"
```

*   **Port**: The server listens on `8080` by default.
*   **Endpoint**: `http://localhost:8080/ocr`

### 2. Environment Variables

*   `OCR_SERVER_URL`: The URL of the PaddleOCR server. Defaults to `http://localhost:8080/ocr`.

### Dependencies

*   **Airtest** (Optional but Recommended): The `click()` methods and default snapshot function rely on `airtest`. Ensure it is installed (`pip install airtest`) if you plan to use these features.

### Constructor Parameters

*   `output_dir`: Directory to store cache (sqlite db) and debug images.
*   `snapshot_func`: Callable to take screenshots. Defaults to `airtest.core.api.snapshot`.
*   `delete_temp_screenshots`: Whether to delete temporary screenshot files after processing (Default: `True`).
*   `resize_image`: Resize large images before sending to OCR server to improve speed (Default: `True`).

## Caching Mechanism

`vibe-ocr` calculates a perceptual hash (dhash) of the screenshot. If a similar image exists in the `sqlite` cache, it retrieves the OCR result locally instead of calling the server. This is critical for high-frequency loops in automation scripts.
