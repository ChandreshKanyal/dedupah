"""Network connectors against real servers or protocol-level stand-ins started inside the test:
- PostgreSQL: a real PostgreSQL 16 (pgserver), read through the form settings, read-only transaction enforced;
- Amazon S3: moto's S3 server (the real S3 HTTP API), read by DuckDB's httpfs exactly as against AWS (endpoint, keys, globs);
- SFTP: a paramiko SSH server, with host-key checking (a wrong key is refused);
- REST APIs, Salesforce, HubSpot, Dynamics 365: a local HTTP server answering like each documented API (paging, OAuth client credentials);
- MongoDB: mongomock (pymongo's API in memory; not a real server).
"""
import json
import os
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow as pa
import pytest

from dedupe.connectors import create
from dedupe.connectors.guard import ConnectorConfigError
from dedupe.dburl import driver_url


def read_all(conf: dict) -> pa.Table:
    c = create(conf)
    c.test_connection()
    return pa.concat_tables(list(c.read_batches(2)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("DEDUPE_SOURCES_ALLOW_HTTP", "1")
    monkeypatch.setenv("DEDUPE_DATA_ROOTS", str(tmp_path))
    monkeypatch.setenv("DEDUPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.delenv("DEDUPE_SOURCE_HOSTS", raising=False)


# ---- PostgreSQL --------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    import sqlalchemy as sa
    eng = sa.create_engine(driver_url(srv.get_uri()))          # SQLAlchemy 2.1 defaults to psycopg 3; we ship psycopg2
    with eng.begin() as c:
        c.execute(sa.text("CREATE SCHEMA crm; CREATE TABLE crm.customers (cust_id text, full_name text, dob date, mobile text, pan text); "
                          "CREATE SEQUENCE crm.s"))
        c.execute(sa.text("INSERT INTO crm.customers VALUES ('C1', 'Rahul Sharma', '1985-05-12', '9811122222', 'ABCPS1234K'), "
                          "('C2', 'Anita Rao', '1990-01-31', NULL, NULL), ('C3', 'Priya Nair', NULL, '7012345678', 'BBBPS1111C')"))
    u = urllib.parse.urlsplit(srv.get_uri())
    if u.port is None:                                                        # on Linux pgserver listens on a local socket only; the form needs host + port
        eng.dispose()
        pytest.skip("the throwaway PostgreSQL listens on a local socket only (Linux); runs on Windows")
    yield {"host": u.hostname, "port": str(u.port)}
    eng.dispose()


def test_postgres_from_form_settings_read_only(postgres, test_mode, monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_PG_PW", "not-checked-by-the-test-server")
    base = {"type": "postgres", **postgres, "database": "postgres", "username": "postgres", "password_env": "DEDUPE_SRC_PG_PW", "ssl_mode": "disable"}
    t = read_all({**base, "schema": "crm", "table": "customers"})
    assert t.column_names == ["cust_id", "full_name", "dob", "mobile", "pan"] and t.num_rows == 3
    assert t.to_pylist()[0]["dob"] == "1985-05-12"
    q = read_all({**base, "query": "SELECT cust_id, full_name FROM crm.customers WHERE pan IS NOT NULL ORDER BY cust_id"})
    assert [r["cust_id"] for r in q.to_pylist()] == ["C1", "C3"]
    with pytest.raises(Exception, match="read-only"):           # even a SELECT that would change something is stopped by the read-only transaction
        read_all({**base, "query": "SELECT nextval('crm.s') AS n"})
    rds = read_all({**base, "type": "rds", "engine": "postgres", "schema": "crm", "table": "customers"})
    assert rds.num_rows == 3


# ---- Amazon S3 (moto server) -------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def s3():
    moto_server = pytest.importorskip("moto.server")
    import boto3
    port = free_port()
    srv = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    srv.start()
    cli = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{port}", aws_access_key_id="AKIDTEST", aws_secret_access_key="secret-test", region_name="us-east-1")
    cli.create_bucket(Bucket="bank-exports")
    cli.put_object(Bucket="bank-exports", Key="crm/2026-10-01.csv", Body=b"id,name,mobile\nC1,Rahul Sharma,9811122222\n")
    cli.put_object(Bucket="bank-exports", Key="crm/2026-10-02.csv", Body=b"id,name,mobile\nC2,Anita Rao,9822233333\nC3,Priya Nair,\n")
    cli.put_object(Bucket="bank-exports", Key="crm/people.jsonl", Body=b'{"id": "C1", "name": {"first": "Rahul"}}\n{"id": "C2", "name": {"first": "Anita"}}\n')
    yield port
    srv.stop()


def test_s3_reads_one_object_a_pattern_and_json_with_keys_from_the_environment(s3, test_mode, monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_S3_KEY", "AKIDTEST")
    monkeypatch.setenv("DEDUPE_SRC_S3_SECRET", "secret-test")
    base = {"type": "s3", "bucket": "bank-exports", "region": "us-east-1", "endpoint": f"127.0.0.1:{s3}", "use_ssl": "no", "url_style": "path",
            "access_key_env": "DEDUPE_SRC_S3_KEY", "secret_key_env": "DEDUPE_SRC_S3_SECRET"}
    one = read_all({**base, "path": "crm/2026-10-01.csv", "format": "csv"})
    assert one.to_pylist() == [{"id": "C1", "name": "Rahul Sharma", "mobile": "9811122222"}]
    many = read_all({**base, "path": "crm/*.csv", "format": "csv"})
    assert sorted(r["id"] for r in many.to_pylist()) == ["C1", "C2", "C3"]
    js = read_all({**base, "path": "crm/people.jsonl", "format": "jsonl"})
    assert js.column_names == ["id", "name.first"] and js.num_rows == 2
    # (moto does not check request signatures, so a wrong secret cannot be tested here; real S3 answers 403 SignatureDoesNotMatch)
    for bad in ({"path": "../etc/passwd"}, {"path": "/abs.csv"}, {"bucket": "Bad_Bucket"}, {"endpoint": "169.254.169.254"}):
        with pytest.raises(ConnectorConfigError):
            create({**base, "path": "x.csv", **bad}).columns()


def test_plain_http_storage_and_apis_are_refused_outside_tests(monkeypatch):
    monkeypatch.delenv("DEDUPE_SOURCES_ALLOW_HTTP", raising=False)
    monkeypatch.setenv("DEDUPE_SRC_K", "k")
    with pytest.raises(ConnectorConfigError, match="TLS"):
        create({"type": "s3", "bucket": "b-1", "path": "x.csv", "endpoint": "minio.bank.in:9000", "use_ssl": "no",
                "access_key_env": "DEDUPE_SRC_K", "secret_key_env": "DEDUPE_SRC_K"}).columns()
    with pytest.raises(ConnectorConfigError, match="https"):
        create({"type": "rest_api", "url": "http://api.bank.in/customers"})
    with pytest.raises(ConnectorConfigError, match="salesforce"):
        create({"type": "salesforce", "instance": "evil.example.com", "query": "SELECT Id FROM Contact"})


# ---- SFTP (paramiko server) ------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sftp_server(tmp_path_factory):
    paramiko = pytest.importorskip("paramiko")
    root = tmp_path_factory.mktemp("sftp_root")
    (root / "outbound").mkdir()
    (root / "outbound" / "customers.csv").write_text("id,name,mobile\nC1,Rahul Sharma,9811122222\nC2,Anita Rao,\n", encoding="utf-8")
    host_key = paramiko.RSAKey.generate(2048)

    class Server(paramiko.ServerInterface):
        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("bank", "pw-123") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

    class Files(paramiko.SFTPServerInterface):
        def _p(self, path):
            p = (root / path.lstrip("/")).resolve()
            assert root.resolve() in p.parents or p == root.resolve()
            return p

        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(os.stat(self._p(path)))
            except FileNotFoundError:
                return paramiko.SFTP_NO_SUCH_FILE

        lstat = stat

        def open(self, path, flags, attr):
            try:
                f = open(self._p(path), "rb")  # noqa: SIM115
            except FileNotFoundError:
                return paramiko.SFTP_NO_SUCH_FILE
            h = paramiko.SFTPHandle(flags)
            h.readfile = f
            return h

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    port = sock.getsockname()[1]
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            t.start_server(server=Server())

    threading.Thread(target=serve, daemon=True).start()
    yield {"port": str(port), "host_key": f"{host_key.get_name()} {host_key.get_base64()}"}
    stop.set()
    sock.close()


def test_sftp_downloads_reads_and_deletes_and_checks_the_host_key(sftp_server, test_mode, monkeypatch, tmp_path):
    paramiko = pytest.importorskip("paramiko")
    monkeypatch.setenv("DEDUPE_SRC_SFTP_PW", "pw-123")
    base = {"type": "sftp", "host": "127.0.0.1", "username": "bank", "password_env": "DEDUPE_SRC_SFTP_PW", "path": "/outbound/customers.csv", "format": "csv",
            **sftp_server}
    c = create(base)
    t = pa.concat_tables(list(c.read_batches(10)))
    assert t.to_pylist() == [{"id": "C1", "name": "Rahul Sharma", "mobile": "9811122222"}, {"id": "C2", "name": "Anita Rao", "mobile": ""}]   # CSV: empty text
    assert not list((tmp_path / "work" / "incoming").glob("*")), "the downloaded copy must be deleted after reading"
    other = paramiko.RSAKey.generate(2048)
    with pytest.raises(paramiko.SSHException):                     # a server presenting another key is refused (no trust on first use)
        create({**base, "host_key": f"{other.get_name()} {other.get_base64()}"}).columns()
    monkeypatch.setenv("DEDUPE_SRC_SFTP_PW", "wrong")
    with pytest.raises(paramiko.AuthenticationException):
        create(base).columns()
    with pytest.raises(ConnectorConfigError):
        create({**base, "path": "/outbound/../../etc/passwd"})


# ---- REST APIs, Salesforce, HubSpot, Dynamics 365 -------------------------------------------------------------------------------------
PEOPLE = [{"id": f"P{i}", "name": f"Person {i}", "contact": {"mobile": f"98111{i:05d}"}} for i in range(1, 8)]


class Api(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, doc, code=200):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
        Api.seen.append(("POST", self.path, form))
        if form.get("grant_type") == "client_credentials" and form.get("client_secret") == "cs-1":
            return self._send({"access_token": "tok-1", "token_type": "Bearer"})
        return self._send({"error": "invalid_client"}, 401)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        Api.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") not in ("Bearer tok-1", "Bearer static-tok"):
            return self._send({"error": "unauthorized"}, 401)
        if u.path == "/next":
            start = int(q.get("from", 0))
            return self._send({"items": PEOPLE[start:start + 3], **({"links": {"next": f"/next?from={start + 3}"}} if start + 3 < len(PEOPLE) else {})})
        if u.path == "/page":
            p = int(q["page"])
            return self._send({"data": PEOPLE[(p - 1) * 3:p * 3]})
        if u.path == "/offset":
            o, n = int(q["offset"]), int(q["limit"])
            return self._send(PEOPLE[o:o + n])
        if u.path == "/cursor":
            c = int(q.get("cursor", 0))
            return self._send({"results": PEOPLE[c:c + 4], "next_cursor": (c + 4) if c + 4 < len(PEOPLE) else None})
        if u.path == "/away":
            return self._send({"items": PEOPLE[:1], "links": {"next": "https://evil.example.com/steal"}})
        if u.path.startswith("/services/data/v62.0/query"):
            if "page2" in u.path:                                       # nextRecordsUrl carries its own locator, no query
                return self._send({"done": True, "records": [{"attributes": {"type": "Contact"}, "Id": "003B", "LastName": "Rao", "Account": {"attributes": {}, "Name": "X"}}]})
            assert q["q"].startswith("SELECT")
            return self._send({"done": False, "nextRecordsUrl": "/services/data/v62.0/query/page2", "records": [{"attributes": {"type": "Contact"}, "Id": "003A", "LastName": "Sharma", "Account": None}]})
        if u.path == "/crm/v3/objects/contacts":
            assert q["properties"] == "firstname,email"
            if q.get("after") == "2":
                return self._send({"results": [{"id": "3", "properties": {"firstname": "Priya", "email": "p@x.in"}}]})
            return self._send({"results": [{"id": "1", "properties": {"firstname": "Rahul", "email": "r@x.in"}}, {"id": "2", "properties": {"firstname": "Anita"}}],
                               "paging": {"next": {"after": "2"}}})
        if u.path == "/api/data/v9.2/contacts":
            assert self.headers.get("Prefer") == "odata.maxpagesize=5000"
            if q.get("page") == "2":
                return self._send({"value": [{"@odata.etag": "W/2", "contactid": "c2", "fullname": "Anita Rao"}]})
            base = f"http://{self.headers['Host']}"
            return self._send({"@odata.context": "x", "value": [{"@odata.etag": "W/1", "contactid": "c1", "fullname": "Rahul Sharma"}],
                               "@odata.nextLink": base + "/api/data/v9.2/contacts?page=2"})
        return self._send({"error": "not found"}, 404)


@pytest.fixture(scope="module")
def api_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_rest_api_paging_styles_and_sign_in(api_server, test_mode, monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_TOK", "static-tok")
    monkeypatch.setenv("DEDUPE_SRC_CS", "cs-1")
    bearer = {"type": "rest_api", "auth": "bearer", "token_env": "DEDUPE_SRC_TOK"}
    styles = [{"url": api_server + "/next", "records_path": "items", "pagination": "next_link", "next_link_path": "links.next"},
              {"url": api_server + "/page", "records_path": "data", "pagination": "page", "page_param": "page"},
              {"url": api_server + "/offset", "pagination": "offset", "page_size": "3"},
              {"url": api_server + "/cursor", "records_path": "results", "pagination": "cursor", "cursor_param": "cursor", "cursor_path": "next_cursor"}]
    for s in styles:
        t = read_all({**bearer, **s})
        assert t.column_names == ["id", "name", "contact.mobile"], s
        assert [r["id"] for r in t.to_pylist()] == [p["id"] for p in PEOPLE], s
    oauth = read_all({"type": "rest_api", "url": api_server + "/offset", "pagination": "offset", "auth": "oauth2", "token_url": api_server + "/token",
                      "client_id": "app", "client_secret_env": "DEDUPE_SRC_CS"})
    assert oauth.num_rows == len(PEOPLE)
    with pytest.raises(ConnectorConfigError, match="another host"):
        read_all({**bearer, "url": api_server + "/away", "records_path": "items", "pagination": "next_link", "next_link_path": "links.next"})
    monkeypatch.setenv("DEDUPE_SRC_TOK", "wrong")
    with pytest.raises(ConnectorConfigError, match="refused the credentials"):
        read_all({**bearer, **styles[0]})
    assert all("static-tok" not in str(x) or x[0] == "GET" for x in Api.seen)


def test_salesforce_hubspot_and_dynamics_follow_their_documented_flows(api_server, test_mode, monkeypatch):
    monkeypatch.setenv("DEDUPE_SRC_SF", "cs-1")
    monkeypatch.setenv("DEDUPE_SRC_HS", "static-tok")
    sf = read_all({"type": "salesforce", "instance": api_server, "client_id": "app", "client_secret_env": "DEDUPE_SRC_SF",
                   "query": "SELECT Id, LastName, Account.Name FROM Contact"})
    assert sf.column_names == ["Id", "LastName", "Account"] or sf.column_names[:2] == ["Id", "LastName"]
    assert [r["Id"] for r in sf.to_pylist()] == ["003A", "003B"] and not any(c.startswith("attributes") or ".attributes" in c for c in sf.column_names)
    assert ("POST", "/services/oauth2/token", {"grant_type": "client_credentials", "client_id": "app", "client_secret": "cs-1"}) in Api.seen
    with pytest.raises(ConnectorConfigError, match="SOQL"):
        create({"type": "salesforce", "instance": api_server, "query": "DELETE FROM Contact"})
    hs = read_all({"type": "hubspot", "token_env": "DEDUPE_SRC_HS", "object": "contacts", "properties": "firstname, email", "base_url": api_server})
    assert hs.to_pylist() == [{"id": "1", "firstname": "Rahul", "email": "r@x.in"}, {"id": "2", "firstname": "Anita", "email": None},
                              {"id": "3", "firstname": "Priya", "email": "p@x.in"}]
    dy = read_all({"type": "dynamics365", "org_url": api_server, "tenant_id": "contoso.onmicrosoft.com", "client_id": "app", "client_secret_env": "DEDUPE_SRC_SF",
                   "entity": "contacts", "select": "contactid, fullname", "token_url": api_server + "/tenant/oauth2/v2.0/token"})
    assert dy.to_pylist() == [{"contactid": "c1", "fullname": "Rahul Sharma"}, {"contactid": "c2", "fullname": "Anita Rao"}]
    token_call = [x for x in Api.seen if x[0] == "POST" and x[1].startswith("/tenant")][-1]
    assert token_call[2]["scope"] == api_server + "/.default"


# ---- MongoDB (mongomock) --------------------------------------------------------------------------------------------------------------
def test_mongodb_flattens_documents_applies_the_filter_and_refuses_code(monkeypatch, test_mode):
    mongomock = pytest.importorskip("mongomock")
    pymongo = pytest.importorskip("pymongo")
    client = mongomock.MongoClient()
    client.bank.customers.insert_many([{"name": {"first": "Rahul", "last": "Sharma"}, "phones": ["9811122222", "7012345678"], "status": "active"},
                                       {"name": {"first": "Anita"}, "status": "closed"}])
    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **k: client)
    monkeypatch.setenv("DEDUPE_SRC_MONGO", "mongodb://localhost:27017")
    t = read_all({"type": "mongodb", "uri_env": "DEDUPE_SRC_MONGO", "database": "bank", "collection": "customers", "filter": '{"status": "active"}'})
    row = t.to_pylist()[0]
    assert t.num_rows == 1 and row["name.first"] == "Rahul" and row["phones"] == "9811122222 | 7012345678" and len(row["_id"]) == 24
    for bad in ('{"$where": "sleep(1000)"}', '{"a": {"$function": {}}}', "[1]", "not json"):
        with pytest.raises(ConnectorConfigError):
            create({"type": "mongodb", "uri_env": "DEDUPE_SRC_MONGO", "database": "bank", "collection": "customers", "filter": bad})


def test_uploads_take_json_lines_and_parquet(tmp_path):
    import pyarrow.parquet as pq
    from sqlalchemy import create_engine

    from dedupe.workspace.uploads import Uploads
    up = Uploads(create_engine(f"sqlite:///{tmp_path / 'u.db'}"), tmp_path / "uploads")
    raw = up.incoming()
    raw.write_text('{"id": "C1", "name": {"first": "Rahul"}}\n{"id": "C2", "name": {"first": "Anita"}}\n', encoding="utf-8")
    r = up.store(raw, "people.jsonl", "tester")
    assert r["kind"] == "jsonl" and r["rows"] == 2 and r["columns"] == ["id", "name.first"]
    raw = up.incoming()
    pq.write_table(pa.table({"id": ["C1"], "score": [42]}), raw)
    r = up.store(raw, "scores.parquet", "tester")
    assert r["kind"] == "parquet" and r["columns"] == ["id", "score"] and Path(up.path(r["upload_id"])).is_file()
