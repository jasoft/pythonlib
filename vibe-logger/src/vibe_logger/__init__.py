# -*- encoding=utf8 -*-
"""
Colored Context Logger
A generic logging library with color support and context injection.
"""

import logging
import os
import sys
import time
from functools import wraps
from typing import Optional, Dict, Any

# Try to import coloredlogs
try:
    import coloredlogs
except ImportError:
    coloredlogs = None

# Default generic format
DEFAULT_LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(filename)s:%(lineno)d %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class GlobalLogContext:
    """Global container for log context variables."""
    
    context: Dict[str, Any] = {}
    defaults: Dict[str, Any] = {}

    @classmethod
    def update(cls, labels: Dict[str, Any]) -> None:
        """Update context variables."""
        if not labels:
            return
        # Convert values to string to ensure safety in logging
        cls.context.update({k: str(v) for k, v in labels.items() if v is not None})

    @classmethod
    def set_defaults(cls, defaults: Dict[str, Any]) -> None:
        """Set default values for context variables if they are missing."""
        cls.defaults.update(defaults)

    @classmethod
    def clear(cls) -> None:
        """Clear all context variables."""
        cls.context.clear()


class _ContextFilter(logging.Filter):
    """Injects global context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Inject defaults first
        for k, v in GlobalLogContext.defaults.items():
            if not hasattr(record, k):
                setattr(record, k, v)
        
        # Inject current context (overrides defaults if present)
        for k, v in GlobalLogContext.context.items():
            setattr(record, k, v)
            
        return True


class LoggerConfig:
    """Manager for logger configuration."""

    _configured_loggers = set()

    @classmethod
    def configure_logger(
        cls,
        logger_name: Optional[str] = None,
        level: str = "INFO",
        log_format: Optional[str] = None,
        date_format: Optional[str] = None,
        use_color: bool = True,
        extra_styles: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> logging.Logger:
        """
        Configure a logger with colors and context.

        Args:
            logger_name: Name of the logger (default: root)
            level: Logging level (DEBUG, INFO, etc.)
            log_format: Log message format
            date_format: Date format
            use_color: Whether to use colored output (console)
            extra_styles: Custom styles for coloredlogs
        """
        env_level = os.environ.get("LOG_LEVEL", level).upper()

        if logger_name is None:
            logger_name = "root"

        logger = logging.getLogger(logger_name)

        # If already configured, just update level
        if logger_name in cls._configured_loggers:
            logger.setLevel(logging.DEBUG) # Allow all to pass to handlers
            for h in logger.handlers:
                h.setLevel(getattr(logging, env_level, logging.INFO))
            return logger

        logger.setLevel(logging.DEBUG)
        
        # Preserve existing file handlers
        existing_file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        logger.handlers = []
        for h in existing_file_handlers:
            logger.addHandler(h)
        
        logger.propagate = False
        
        # Add context filter to logger so all handlers receive the context
        if not any(isinstance(f, _ContextFilter) for f in logger.filters):
            logger.addFilter(_ContextFilter())

        if log_format is None:
            log_format = DEFAULT_LOG_FORMAT

        if date_format is None:
            date_format = DEFAULT_DATE_FORMAT

        # Console Handler Setup
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setLevel(getattr(logging, env_level, logging.INFO))
        stream_handler.addFilter(_ContextFilter())

        if use_color and coloredlogs:
            try:
                styles = {
                    "debug": {"color": "cyan"},
                    "info": {"color": "green"},
                    "warning": {"color": "yellow"},
                    "error": {"color": "red"},
                    "critical": {"color": "red", "bold": True},
                }
                if extra_styles:
                    styles.update(extra_styles)
                
                formatter = coloredlogs.ColoredFormatter(
                    fmt=log_format,
                    datefmt=date_format,
                    level_styles=styles,
                )
                stream_handler.setFormatter(formatter)
            except Exception:
                # Fallback
                stream_handler.setFormatter(logging.Formatter(log_format, date_format))
        else:
            stream_handler.setFormatter(logging.Formatter(log_format, date_format))

        logger.addHandler(stream_handler)
        cls._configured_loggers.add(logger_name)

        return logger


def setup_logger(
    name: Optional[str] = None,
    level: str = "INFO",
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
    use_color: bool = True,
) -> logging.Logger:
    """Helper to setup a logger."""
    return LoggerConfig.configure_logger(
        logger_name=name,
        level=level,
        log_format=log_format,
        date_format=date_format,
        use_color=use_color,
    )


def attach_file_handler(
    logger_name: Optional[str] = None,
    log_dir: str = "logs",
    filename: Optional[str] = None,
    level: str = "DEBUG",
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
) -> str:
    """
    Attach a file handler to a specific logger or root.
    
    If filename is not provided, it generates one based on current context (e.g. 'log_session_XYZ.log').
    """
    if log_format is None:
        log_format = DEFAULT_LOG_FORMAT
    
    if date_format is None:
        date_format = DEFAULT_DATE_FORMAT

    os.makedirs(log_dir, exist_ok=True)

    if not filename:
        # Generate generic filename
        timestamp = time.strftime("%Y%m%d")
        session = GlobalLogContext.context.get("session", "app")
        safe_session = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(session))
        filename = f"{safe_session}_{timestamp}.log"
    
    file_path = os.path.join(log_dir, filename)

    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    file_handler.addFilter(_ContextFilter())
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    
    # Avoid duplicate handlers
    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(file_path) for h in logger.handlers):
        logger.addHandler(file_handler)
        
    return file_path


def log_calls(level: str = "DEBUG"):
    """Decorator to log function calls, args, and execution time."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            target_logger = logging.getLogger(func.__module__)
            
            # Try to find a 'logger' attribute on the instance if it's a method
            if args and hasattr(args[0], "logger"):
                instance_logger = getattr(args[0], "logger")
                if isinstance(instance_logger, logging.Logger):
                    target_logger = instance_logger

            log_func = getattr(target_logger, level.lower(), target_logger.debug)

            arg_str = ""
            if args:
                arg_str = str(args[:3]) # Truncate for sanity
                if len(args) > 3:
                    arg_str += "..."
            
            kw_str = ""
            if kwargs:
                kw_str = f", kwargs={list(kwargs.keys())}"

            log_func(f"➡️ {func.__name__} Start {arg_str}{kw_str}")
            
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                log_func(f"⬅️ {func.__name__} End {elapsed:.4f}s Ret: {type(result).__name__}")
                return result
            except Exception as e:
                target_logger.error(f"❌ {func.__name__} Error: {type(e).__name__}: {e}")
                raise
        return wrapper
    return decorator
