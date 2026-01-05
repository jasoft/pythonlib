import os
import logging
from vibe_logger import setup_logger, GlobalLogContext, attach_file_handler, log_calls

def test_context_injection(caplog):
    """Test that context is correctly injected into logs."""
    GlobalLogContext.clear()
    GlobalLogContext.update({"test_id": "123", "user": "tester"})
    
    logger = setup_logger(
        name="test_ctx",
        log_format="%(levelname)s %(test_id)s %(user)s %(message)s"
    )
    logger.propagate = True  # Enable propagation for caplog capture
    
    with caplog.at_level(logging.INFO, logger="test_ctx"):
        logger.info("Hello World")
    
    # Check that context was injected into the LogRecord
    assert len(caplog.records) > 0
    record = caplog.records[0]
    assert hasattr(record, "test_id")
    assert record.test_id == "123"
    assert record.user == "tester"
    assert record.message == "Hello World"

def test_file_logging(tmp_path):
    """Test file logging attachment."""
    GlobalLogContext.clear()
    log_dir = tmp_path / "logs"
    
    logger = setup_logger(name="test_file")
    log_file = attach_file_handler(
        logger_name="test_file",
        log_dir=str(log_dir),
        filename="test.log",
        log_format="%(message)s"
    )
    
    logger.info("File content check")
    
    assert os.path.exists(log_file)
    with open(log_file, "r") as f:
        content = f.read()
        assert "File content check" in content

def test_decorator(caplog):
    """Test the @log_calls decorator."""
    setup_logger(name="test_dec", level="DEBUG")
    
    @log_calls(level="DEBUG")
    def add(x, y):
        return x + y
    
    # We need to ensure the decorator uses our logger. 
    # The decorator uses logging.getLogger(module_name) by default.
    # Since this test is in a module, we should check the root logger or specific module logger.
    # However, for this test, let's just run it and check caplog.
    
    with caplog.at_level(logging.DEBUG):
        add(1, 2)
        
    # Check for start/end logs
    assert "add Start" in caplog.text
    assert "add End" in caplog.text

if __name__ == "__main__":
    # Manual verification script if run directly
    logger = setup_logger(name="manual_test", level="DEBUG")
    GlobalLogContext.update({"session": "manual_run"})
    
    logger.info("This is an info message (should be green).")
    logger.warning("This is a warning (should be yellow).")
    logger.error("This is an error (should be red).")
    
    path = attach_file_handler(log_dir="manual_logs")
    print(f"Log attached to {path}")
    logger.info("This message goes to file as well.")
