import pytest
from test_connectors_remote import sftp_server  # noqa: E402,F401  (shared SFTP test server)


@pytest.fixture(autouse=True)
def every_project_may_use_test_variables(monkeypatch):
    """Tests set their own DEDUPE_SRC_* / DEDUPE_DST_* variables; on a real server the operator allows each one per project (envgrants).
    test_env_grants.py sets its own grants to check exactly that."""
    monkeypatch.setenv("DEDUPE_ENV_GRANTS", "DEDUPE_SRC_*=*; DEDUPE_DST_*=*")
