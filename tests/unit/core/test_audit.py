"""Unit tests for audit logger."""

from unittest.mock import patch


def test_log_access_writes_structured_entry() -> None:
    """Verify log_access produces an entry with all required fields."""

    with patch("backend.shared.audit._audit_logger") as mock_logger:
        from backend.shared.audit import log_access

        log_access(
            user="testadmin", action="GET_SUMMARY", resource="/api/v1/summary", ip="127.0.0.1"
        )
        mock_logger.info.assert_called_once()
        call_arg = mock_logger.info.call_args[0][0]
        assert "user=testadmin" in call_arg
        assert "action=GET_SUMMARY" in call_arg
        assert "resource=/api/v1/summary" in call_arg
        assert "ip=127.0.0.1" in call_arg
        assert "[AUDIT]" in call_arg


def test_log_access_default_ip() -> None:
    """Unknown IP defaults to 'unknown'."""
    with patch("backend.shared.audit._audit_logger") as mock_logger:
        from backend.shared.audit import log_access

        log_access(user="u", action="A", resource="/r")
        call_arg = mock_logger.info.call_args[0][0]
        assert "ip=unknown" in call_arg
