"""Login seam — the placeholder gate must be simple but never sloppy:
hashed comparison only, overridable sources, correct fallback order."""
import hashlib
import json

from src.app import auth


def test_demo_fallback_accepts_demo_credentials():
    assert auth.verify_credentials("analyst", "riskdemo")


def test_wrong_password_rejected():
    assert not auth.verify_credentials("analyst", "wrong")
    assert not auth.verify_credentials("nobody", "riskdemo")


def test_empty_credentials_rejected():
    assert not auth.verify_credentials("", "")
    assert not auth.verify_credentials(None, None)
    assert not auth.verify_credentials("analyst", "")


def test_users_json_overrides_demo(tmp_path, monkeypatch):
    users = {"ana": hashlib.sha256(b"s3cret").hexdigest()}
    (tmp_path / "users.json").write_text(json.dumps(users))
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
    assert auth.verify_credentials("ana", "s3cret")
    assert not auth.verify_credentials("ana", "riskdemo")
    # demo user must be disabled once a real user file exists
    assert not auth.verify_credentials("analyst", "riskdemo")


def test_env_var_source(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)  # no users.json
    monkeypatch.setenv("NIRE_USER", "josem")
    monkeypatch.setenv("NIRE_PASSWORD", "hunter2")
    assert auth.verify_credentials("josem", "hunter2")
    assert not auth.verify_credentials("analyst", "riskdemo")


def test_no_plaintext_passwords_in_store():
    users = auth.load_users()
    for pw_hash in users.values():
        assert len(pw_hash) == 64 and int(pw_hash, 16) >= 0  # sha256 hex
