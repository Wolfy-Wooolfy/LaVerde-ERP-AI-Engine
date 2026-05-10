from backend.core.security import verify_credentials


def test_correct_credentials() -> None:
    # The test env sets BASIC_AUTH_USERNAME=testadmin, BASIC_AUTH_PASSWORD=testpass
    assert verify_credentials("testadmin", "testpass") is True


def test_wrong_password() -> None:
    assert verify_credentials("testadmin", "wrongpass") is False


def test_wrong_username() -> None:
    assert verify_credentials("hacker", "testpass") is False


def test_both_wrong() -> None:
    assert verify_credentials("bad", "creds") is False


def test_empty_credentials() -> None:
    assert verify_credentials("", "") is False
