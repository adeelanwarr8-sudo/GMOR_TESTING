"""Earth Engine connection tests: run without network or real credentials."""
import json

import pytest

import gee_connection as gee

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEsecret\n-----END PRIVATE KEY-----\n"


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("GEE_SERVICE_ACCOUNT_FILE", "GOOGLE_APPLICATION_CREDENTIALS", "GEE_PROJECT", "EE_PROJECT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_ee(monkeypatch):
    """Replace ee.Initialize / ServiceAccountCredentials; record what they received."""
    import ee
    calls = {}
    monkeypatch.setattr(ee, "ServiceAccountCredentials",
                        lambda email, key_data: {"email": email, "info": json.loads(key_data)})
    monkeypatch.setattr(ee, "Initialize", lambda creds=None, project=None: calls.update(creds=creds, project=project))
    return calls


def test_no_credentials_reports_every_source(clean_env):
    status = gee.init_ee(secrets={})
    assert not status.ok and status.method == "none"
    assert len(status.attempts) == 3
    assert "EE_PROJECT" in status.attempts[-1]


def test_ecds_json_secrets_are_accepted(clean_env, fake_ee):
    key = {"type": "service_account", "client_email": "sa@p.iam.gserviceaccount.com",
           "private_key": FAKE_KEY, "project_id": "key-proj"}
    status = gee.init_ee(secrets={"EE_PROJECT": "ee-proj", "EE_SERVICE_ACCOUNT_JSON": json.dumps(key)})
    assert status.ok and "EE_SERVICE_ACCOUNT_JSON" in status.method
    assert fake_ee["project"] == "ee-proj"            # EE_PROJECT wins over project_id in the key
    assert fake_ee["creds"]["email"] == "sa@p.iam.gserviceaccount.com"


def test_ecds_split_key_secrets_convert_escaped_newlines(clean_env, fake_ee):
    status = gee.init_ee(secrets={"EE_PROJECT": "ee-proj", "EE_SERVICE_ACCOUNT": "sa@p.iam.gserviceaccount.com",
                                  "EE_PRIVATE_KEY": FAKE_KEY.replace("\n", "\\n")})
    assert status.ok
    assert "\n" in fake_ee["creds"]["info"]["private_key"] and "\\n" not in fake_ee["creds"]["info"]["private_key"]


def test_invalid_json_gives_a_clear_message(clean_env):
    status = gee.init_ee(secrets={"EE_SERVICE_ACCOUNT_JSON": "{not json"})
    assert not status.ok
    assert "not valid JSON" in status.attempts[0]


def test_own_login_uses_ee_project_from_secrets(clean_env, fake_ee):
    status = gee.init_ee(secrets={"EE_PROJECT": "ee-proj"})
    assert status.ok and status.method == "own login" and fake_ee["project"] == "ee-proj"


def test_failures_never_show_keys_or_tokens(clean_env, monkeypatch):
    import ee

    def boom(*a, **k):
        raise RuntimeError(f"auth failed {FAKE_KEY} access_token=ya29.SECRET see https://oauth2.googleapis.com/x")
    monkeypatch.setattr(ee, "ServiceAccountCredentials", boom)
    key = {"client_email": "sa@p.iam.gserviceaccount.com", "private_key": FAKE_KEY}
    status = gee.init_ee(secrets={"EE_SERVICE_ACCOUNT_JSON": json.dumps(key)})
    text = " ".join(status.attempts)
    assert not status.ok
    assert "MIIEsecret" not in text and "ya29" not in text and "googleapis" not in text
    assert "diagnostic ID" in text


def test_safe_error_redacts():
    msg = gee.safe_error(RuntimeError(f"{FAKE_KEY} Bearer abc.def private_key=xyz http://x.y/z"))
    assert "MIIEsecret" not in msg and "abc.def" not in msg and "xyz" not in msg and "x.y" not in msg


def test_unreadable_key_file(clean_env, monkeypatch, tmp_path):
    bad = tmp_path / "key.json"
    bad.write_text("not json")
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_FILE", str(bad))
    status = gee.init_ee(secrets={})
    assert any("couldn't read" in a for a in status.attempts)
