# Colored Context Logger

A generic, easy-to-use logging library supporting:
- **Colored console output** (via `coloredlogs`).
- **Context injection**: Add global context (e.g., `session_id`, `config_name`) to all log records automatically.
- **File logging**: Easy attachment of file handlers.
- **Function tracing**: `@log_calls` decorator to log function entry, exit, arguments, and execution time.

## Installation

```bash
pip install colored-context-logger
```

## Usage

### 1. Basic Setup & Context

```python
from vibe_logger import setup_logger, GlobalLogContext, log_calls

# Setup Logger
logger = setup_logger(name="my_app", level="DEBUG")

# Set Global Context (will appear in all logs)
GlobalLogContext.update({"user": "alice", "request_id": "12345"})

logger.info("Processing request") 
# Output: 2025-01-01 12:00:00 INFO my_app ... user=alice request_id=12345 Processing request
```

### 2. File Logging

You can easily attach file handlers to your logger.

```python
from vibe_logger import attach_file_handler

# Method A: Auto-generated filename based on context
# If 'session' is in GlobalLogContext, it uses that for the filename.
GlobalLogContext.update({"session": "main_process"})
log_path = attach_file_handler(logger_name="my_app", log_dir="logs")
print(f"Logging to: {log_path}") 
# Example: logs/main_process_20250101.log

# Method B: Specific filename
attach_file_handler(
    logger_name="my_app",
    log_dir="logs",
    filename="error.log",
    level="ERROR" # Only log errors to this file
)
```

### 3. Function Decorator

```python
@log_calls()
def calculate(x, y):
    return x + y

calculate(1, 2)
# Output:
# ➡️ calculate Start (1, 2)
# ⬅️ calculate End 0.0001s Ret: int
```
