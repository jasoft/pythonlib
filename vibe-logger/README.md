# Colored Context Logger (vibe-colored-logger)

A generic, easy-to-use logging library designed for modern Python applications. It enhances standard logging with colored console output, automatic context injection, and simplified file handler management.

## Installation

```bash
pip install vibe-colored-logger
```

## Features

- **Colored Console Output**: Integrated with `coloredlogs` for readable, severity-based coloring.
- **Global Context Injection**: Automatically inject context (e.g., `session_id`, `user_id`, `environment`) into every log record. Great for tracking requests across functions.
- **Simplified File Logging**: One-line command to attach file handlers, with support for dynamic filenames based on context.
- **Function Tracing**: `@log_calls` decorator to automatically log function entry, exit, arguments, return values, and execution time.

## Usage Guide

### 1. Basic Setup & Context

Initialize the logger and set global context variables that will appear in all log entries.

```python
from vibe_logger import setup_logger, GlobalLogContext

# 1. Setup Logger (Standard Python logging wrapper)
logger = setup_logger(name="PaymentService", level="DEBUG")

# 2. Set Global Context
# These key-values will be appended to every log message automatically.
GlobalLogContext.update({
    "env": "production",
    "version": "v1.2.0",
    "request_id": "req_8848"
})

logger.info("Service initialized")
# Output: [INFO] ... env=production version=v1.2.0 request_id=req_8848 Service initialized
```

### 2. File Logging

Easily attach file handlers. You can specify a fixed filename or let the logger generate one based on the context (e.g., a specific session ID).

```python
from vibe_logger import attach_file_handler

# Method A: Attach to a specific directory
# Filename will be auto-generated or based on 'session' context if present.
log_path = attach_file_handler(
    logger_name="PaymentService",
    log_dir="logs/payments",
    level="DEBUG"
)
print(f"Logging to: {log_path}")

# Method B: Specify exact filename
attach_file_handler(
    logger_name="PaymentService",
    log_dir="logs",
    filename="errors.log",
    level="ERROR" # Only log errors here
)
```

### 3. Function Tracing (@log_calls)

Use the `@log_calls` decorator to debug function execution without writing boilerplate print statements.

```python
from vibe_logger import log_calls

@log_calls(level="DEBUG")
def process_payment(amount, currency):
    if amount <= 0:
        raise ValueError("Invalid amount")
    return f"Processed {amount} {currency}"

# Calling the function will log:
# ➡️ process_payment Start (100, 'USD')
# ⬅️ process_payment End 0.002s Ret: 'Processed 100 USD'
```

### 4. Integration with Existing Loggers

If you have an existing logging configuration, you can use `GlobalLogContext` to inject data into it, provided you format your logs to include the context fields. `vibe-colored-logger` handles the formatting automatically when using `setup_logger`.