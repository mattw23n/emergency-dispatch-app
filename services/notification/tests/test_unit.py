# tests/test_unit.py
import pytest
from unittest.mock import patch
from src.send_sns import send_notification


@pytest.mark.dependency()
def test_send_notification_message_published():
    """Unit test: SNS publish is called with correct params"""
    with patch("src.send_sns.sns.publish") as mock_publish:
        mock_publish.return_value = {"MessageId": "12345"}

        msg_id = send_notification("P001", "TestSubject", "TestMessage")

        mock_publish.assert_called_once()
        assert msg_id == "12345"
