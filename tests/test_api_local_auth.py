"""Username and password sign-in, the admin, users, and roles the administrator defines."""
import pytest
from fastapi.testclient import TestClient

from dedupe.api import ApiSettings, accounts, accounts_cli, create_app
from dedupe.api.accounts import AccountError, check_password_policy, hash_password, verify_password
from dedupe.api.roles import PERMISSIONS
from dedupe.api.settings import SettingsError

PW = "correct horse battery"
SECRET = "l" * 40


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch):
    monkeypatch.setattr(accounts, "SCRYPT_N", 2**10)            # the real cost (2**17) makes every test take a second


def make(tmp_path, **kw):
    s = ApiSettings(env="dev", auth_mode="local", local_secret=SECRET, work=tmp_path / "ws", database_url=f"sqlite:///{tmp_path / 'ws' / 'main.db'}",
                    ip_rate_limit_per_min=10**6, rate_limit_per_min=10**6, **kw)
    app = create_app(s)
    sv = app.state.services
    sv.accounts.create_user("admin", "The Admin", PW, ["admin"], "test", must_change=False)
    return TestClient(app), s, sv


def login(c, user="admin", pw=PW):
    return c.post("/auth/login", json={"username": user, "password": pw})


def bearer(c, user="admin", pw=PW):
    r = login(c, user, pw)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_passwords_are_hashed_checked_and_policy_is_enforced():
    h = hash_password(PW)
    assert PW not in h and h.startswith("scrypt$") and h != hash_password(PW)             # salted: the same password never hashes the same way
    assert verify_password(PW, h) and not verify_password(PW + "x", h) and not verify_password(PW, "garbage")
    for bad in ("short", "x" * 129, "alicealicealice", "aaaaaaaaaaaa", "password1234", "Administrator"):
        with pytest.raises(AccountError):
            check_password_policy(bad, "alice")
    check_password_policy(PW, "alice")


def test_local_mode_needs_a_secret_and_production_needs_an_explicit_yes():
    with pytest.raises(SettingsError):
        ApiSettings(env="dev", auth_mode="local").validate()
    ApiSettings(env="dev", auth_mode="local", local_secret=SECRET).validate()
    with pytest.raises(SettingsError, match="DEDUPE_ALLOW_LOCAL_AUTH"):
        ApiSettings(env="production", auth_mode="local", local_secret=SECRET).validate()


def test_the_login_screen_can_ask_which_sign_in_to_offer_and_other_modes_have_no_login(tmp_path):
    c, s, sv = make(tmp_path)
    assert c.get("/auth/config").json() == {"mode": "local", "password_login": True}
    dev = TestClient(create_app(ApiSettings(env="dev", auth_mode="dev", dev_secret="d" * 40, work=tmp_path / "w2", database_url=f"sqlite:///{tmp_path / 'm2.db'}")))
    assert dev.get("/auth/config").json()["password_login"] is False
    assert dev.post("/auth/login", json={"username": "admin", "password": PW}).status_code == 404


def test_the_admin_signs_in_and_holds_every_permission(tmp_path):
    c, s, sv = make(tmp_path)
    r = login(c)
    assert r.status_code == 200 and r.json()["must_change_password"] is False and r.json()["expires_in"] == s.max_token_lifetime_s
    me = c.get("/me", headers={"Authorization": "Bearer " + r.json()["token"]}).json()
    assert me["sub"] == "admin" and me["username"] == "The Admin" and me["roles"] == ["admin"] and set(me["permissions"]) == set(PERMISSIONS)
    assert c.get("/me").status_code == 401                                                # no anonymous access
    ev = sv.seclog.events(limit=50, event_name="login")
    assert ev and ev[-1]["actor"] == "admin" and PW not in str(ev)                        # sign-ins are logged, passwords never


def test_wrong_passwords_look_the_same_for_every_case_and_lock_the_account(tmp_path):
    c, s, sv = make(tmp_path)
    sv.accounts.create_user("dora", "", PW, ["viewer"], "admin", must_change=False)
    sv.accounts.update("dora", disabled=True)
    sv.accounts.create_user("lena", "", PW, ["viewer"], "admin", must_change=False)
    bodies = set()
    for user, pw in (("nobody", PW), ("lena", "wrong-password-1"), ("dora", PW), ("Not A User!", PW)):
        r = login(c, user, pw)
        assert r.status_code == 401
        bodies.add(r.json()["error"]["message"])
    assert bodies == {"Incorrect username or password."}                                   # unknown, wrong password, disabled, malformed: one answer
    for _ in range(4):
        assert login(c, "lena", "wrong-password-2").status_code == 401
    assert login(c, "lena", PW).status_code == 401                                         # five failures in a row: locked, even with the right password
    assert any(e["detail"].get("why") == "locked" for e in sv.seclog.events(limit=100, event_name="auth_failed"))
    sv.accounts.set_password("lena", PW + "2", must_change=False)                          # an administrator reset unlocks
    assert login(c, "lena", PW + "2").status_code == 200


def test_sign_in_attempts_are_rate_limited_per_address(tmp_path):
    c, s, sv = make(tmp_path)
    codes = [login(c, "nobody", "x" * 14).status_code for _ in range(14)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]


def test_a_new_user_must_change_the_temporary_password_before_anything_else(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    r = c.post("/users", json={"username": "asha", "display_name": "Asha", "password": "temporary-pass-1", "roles": ["steward"]}, headers=admin)
    assert r.status_code == 201 and r.json()["must_change_password"] is True and "temporary-pass" not in str(r.json())
    first = login(c, "asha", "temporary-pass-1").json()
    assert first["must_change_password"] is True
    h = {"Authorization": "Bearer " + first["token"]}
    assert c.get("/me", headers=h).status_code == 200
    assert c.get("/home", headers=h).status_code == 403 and c.get("/projects", headers=h).status_code == 403          # nothing else until changed
    assert c.post("/auth/password", json={"current_password": "wrong-password-9", "new_password": "a brand new phrase 1"}, headers=h).status_code == 403
    assert c.post("/auth/password", json={"current_password": "temporary-pass-1", "new_password": "temporary-pass-1"}, headers=h).status_code == 422
    assert c.post("/auth/password", json={"current_password": "temporary-pass-1", "new_password": "short"}, headers=h).status_code == 422
    done = c.post("/auth/password", json={"current_password": "temporary-pass-1", "new_password": "a brand new phrase 1"}, headers=h)
    assert done.status_code == 200 and done.json()["must_change_password"] is False
    assert c.get("/home", headers=h).status_code == 401                                   # the old session ended with the old password
    assert c.get("/home", headers={"Authorization": "Bearer " + done.json()["token"]}).status_code == 200
    assert login(c, "asha", "temporary-pass-1").status_code == 401 and login(c, "asha", "a brand new phrase 1").status_code == 200


def test_disabling_a_user_or_editing_a_role_takes_effect_on_the_next_request(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    sv.accounts.create_user("ravi", "", PW, ["viewer"], "admin", must_change=False)
    ravi = bearer(c, "ravi")
    assert c.get("/projects", headers=ravi).status_code == 200 and c.get("/security/events", headers=ravi).status_code == 403
    assert c.put("/roles/viewer", json={"description": "", "permissions": ["logs.read"]}, headers=admin).status_code == 409        # built-in: fixed
    assert c.post("/roles", json={"name": "log-reader", "description": "reads logs", "permissions": ["logs.read", "runs.read"]}, headers=admin).status_code == 201
    assert c.patch("/users/ravi", json={"roles": ["log-reader"]}, headers=admin).status_code == 200
    assert c.get("/security/events", headers=ravi).status_code == 200 and c.get("/users", headers=ravi).status_code == 403   # same token, new rights
    assert c.put("/roles/log-reader", json={"description": "", "permissions": ["runs.read"]}, headers=admin).status_code == 200
    assert c.get("/security/events", headers=ravi).status_code == 403                                                              # a role edit applies at once
    assert c.patch("/users/ravi", json={"disabled": True}, headers=admin).status_code == 200
    assert c.get("/me", headers=ravi).status_code == 401 and login(c, "ravi").status_code == 401                                   # disabled: gone at once
    assert c.patch("/users/ravi", json={"disabled": False}, headers=admin).status_code == 200
    assert c.get("/me", headers=ravi).status_code == 401                                                                           # but the old session stays dead
    assert login(c, "ravi").status_code == 200


def test_admin_defined_roles_validate_and_cannot_be_misused(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    good = {"name": "queue-viewer", "description": "reads the queue", "permissions": ["queue.read", "person.read"]}
    assert c.post("/roles", json=good, headers=admin).status_code == 201
    assert c.post("/roles", json=good, headers=admin).status_code == 409
    assert c.post("/roles", json={**good, "name": "steward"}, headers=admin).status_code == 409                # a built-in name
    assert c.post("/roles", json={**good, "name": "Bad Name"}, headers=admin).status_code == 422
    assert c.post("/roles", json={**good, "name": "x1", "permissions": ["fly.rocket"]}, headers=admin).status_code == 422
    assert c.post("/roles", json={**good, "name": "x2", "permissions": []}, headers=admin).status_code == 422
    listing = c.get("/roles", headers=admin).json()
    assert {r["name"] for r in listing["roles"]} >= {"admin", "viewer", "queue-viewer"} and len(listing["permissions"]) == len(PERMISSIONS)
    assert next(r for r in listing["roles"] if r["name"] == "admin")["builtin"] is True
    sv.accounts.create_user("tess", "", PW, ["queue-viewer"], "admin", must_change=False)
    assert c.delete("/roles/queue-viewer", headers=admin).status_code == 409                                    # still assigned
    assert c.delete("/roles/admin", headers=admin).status_code == 409 and c.delete("/roles/nope", headers=admin).status_code == 404
    sv.workspace.create("demo", "Demo", "", "admin")
    assert c.post("/projects/demo/access", json={"subject": "tess", "role": "queue-viewer"}, headers=admin).status_code == 201     # grants accept custom roles
    assert c.patch("/users/tess", json={"roles": ["viewer"]}, headers=admin).status_code == 200
    assert c.delete("/roles/queue-viewer", headers=admin).status_code == 409                                    # a project grant still names it
    assert c.delete("/projects/demo/access/tess/queue-viewer", headers=admin).status_code == 204
    assert c.delete("/roles/queue-viewer", headers=admin).status_code == 204


def test_nobody_can_hand_out_more_than_they_hold(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    assert c.post("/roles", json={"name": "people-manager", "description": "", "permissions": ["users.manage", "queue.read"]}, headers=admin).status_code == 201
    sv.accounts.create_user("hrmgr", "", PW, ["people-manager"], "admin", must_change=False)
    hr = bearer(c, "hrmgr")
    assert c.get("/users", headers=hr).status_code == 200 and c.get("/roles", headers=hr).status_code == 200
    assert c.post("/users", json={"username": "newbie", "password": "temporary-pass-1", "roles": ["admin"]}, headers=hr).status_code == 403       # more than they hold
    assert c.post("/users", json={"username": "newbie", "password": "temporary-pass-1", "roles": ["steward"]}, headers=hr).status_code == 403    # steward holds more
    assert c.post("/users", json={"username": "newbie", "password": "temporary-pass-1", "roles": ["people-manager"]}, headers=hr).status_code == 201
    assert c.post("/users/admin/password", json={"password": "hijack-the-admin-1"}, headers=hr).status_code == 403       # cannot touch a stronger user
    assert c.patch("/users/admin", json={"display_name": "x"}, headers=hr).status_code == 403
    assert c.post("/roles", json={"name": "sneaky", "description": "", "permissions": ["queue.read"]}, headers=hr).status_code == 403            # lacks roles.manage
    assert login(c, "admin").status_code == 200                                                                          # the admin's password is untouched


def test_an_administrator_cannot_lock_everyone_out_or_change_themselves(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    assert c.patch("/users/admin", json={"roles": ["viewer"]}, headers=admin).status_code == 409                     # not their own roles
    assert c.patch("/users/admin", json={"disabled": True}, headers=admin).status_code == 409
    assert c.post("/users/admin/password", json={"password": "another long phrase 1"}, headers=admin).status_code == 409   # use /auth/password
    assert c.patch("/users/admin", json={"display_name": "Chief"}, headers=admin).status_code == 200
    assert c.post("/users", json={"username": "second", "password": "temporary-pass-1", "roles": ["admin"]}, headers=admin).status_code == 201
    sv.accounts.set_password("second", PW, must_change=False)
    second = bearer(c, "second")
    assert c.patch("/users/admin", json={"disabled": True}, headers=second).status_code == 200                       # fine while another admin remains
    assert c.patch("/users/second", json={"roles": ["viewer"]}, headers=second).status_code == 409                   # themselves
    sv.accounts.update("admin", disabled=False)
    assert c.post("/roles", json={"name": "usermgr", "description": "", "permissions": ["users.manage"]}, headers=bearer(c)).status_code == 201


def test_project_access_for_custom_roles_and_the_projects_all_permission(tmp_path):
    c, s, sv = make(tmp_path)
    admin = bearer(c)
    sv.workspace.create("demo", "Demo", "", "admin")
    assert c.post("/roles", json={"name": "browser", "description": "", "permissions": ["runs.read", "config.read"]}, headers=admin).status_code == 201
    assert c.post("/roles", json={"name": "browser-all", "description": "", "permissions": ["runs.read", "projects.all"]}, headers=admin).status_code == 201
    for who, role in (("inside", "browser"), ("outside", "browser"), ("everywhere", "browser-all")):
        sv.accounts.create_user(who, "", PW, [role], "admin", must_change=False)
    assert c.post("/projects/demo/access", json={"subject": "inside", "role": "browser"}, headers=admin).status_code == 201
    assert c.get("/projects/demo", headers=bearer(c, "inside")).status_code == 200
    assert c.get("/projects/demo", headers=bearer(c, "outside")).status_code == 403
    assert c.get("/projects/demo", headers=bearer(c, "everywhere")).status_code == 200                                # the permission, not a role name, decides


def test_the_first_administrator_is_created_from_the_command_line(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("DEDUPE_WORK_DIR", str(tmp_path / "w"))
    monkeypatch.delenv("DEDUPE_DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert accounts_cli.main(["create-admin", "--username", "root-admin", "--name", "Root"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert accounts_cli.main(["create-admin", "--username", "root-admin"]) == 1                                    # already exists
    monkeypatch.setattr("sys.stdin", io.StringIO("weak\n"))
    assert accounts_cli.main(["create-admin", "--username", "other-admin"]) == 1                                   # the policy applies here too
    s = ApiSettings(env="dev", auth_mode="local", local_secret=SECRET, work=tmp_path / "w", database_url=f"sqlite:///{tmp_path / 'w' / 'api.db'}")
    c = TestClient(create_app(s))
    r = login(c, "root-admin")
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert PW not in capsys.readouterr().out
