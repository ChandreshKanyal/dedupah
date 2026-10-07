"""Every kind of source the platform can read, described once: the screens build their forms from this, and the settings check uses it as its
allow-list. Adding a source type = a connector class + an entry here.

A setting is {name, label, kind, required, help, default, choices, placeholder, advanced, show_if}. kind is one of: text, number, select,
env (the NAME of an environment variable starting with DEDUPE_SRC_ that holds a secret: never the secret itself), textarea, path.
`tested` says how the connector was checked in this project: "live" (against a real server or real files in the test suite), "simulated"
(against a stand-in that speaks the same protocol), or "unverified" (built from the vendor's documentation; not yet run against the service).
"""
from .records import available

GROUPS = ["Upload", "Files on the server", "Databases", "Cloud data warehouses", "Cloud storage and file transfer", "Applications and APIs"]


def S(name, label, kind="text", required=False, help="", default=None, choices=None, placeholder=None, advanced=False, show_if=None):
    return {"name": name, "label": label, "kind": kind, "required": required, "help": help, "default": default, "choices": choices,
            "placeholder": placeholder, "advanced": advanced, "show_if": show_if}


ENV = "Name of an environment variable on the server (starting with DEDUPE_SRC_) that holds it. The value itself is never typed here."

# ---- reusable groups of settings --------------------------------------------------------------------------------------------------
PATH = S("path", "File path", "path", True, "A file inside a folder the administrator allowed for sources (DEDUPE_DATA_ROOTS).", placeholder="/data/exports/customers.csv")
ENCODING = S("encoding", "Text encoding", "select", help="How the text is stored. UTF-8 unless the file came from an old Windows program.",
             default="utf-8-sig", choices=["utf-8-sig", "utf-8", "cp1252", "latin-1", "utf-16"], advanced=True)
DELIM = S("delimiter", "Delimiter", "select", help="The character between values.", choices=[",", ";", "|", "\t"], advanced=True)
SHEET = S("sheet", "Sheet name", help="Leave empty for the first sheet.", placeholder="Customers")
RECORDS_PATH = S("records_path", "Where the list of records is", help="Dotted path to the list inside the document, e.g. data.customers. Empty when the file is the list itself.")
RECORD_TAG = S("record_tag", "Record element", required=True, help="The XML element that holds one record.", placeholder="Customer")
WIDTHS = S("widths", "Column layout", "textarea", True, "Each column as name:width in characters, in order, separated by commas.", placeholder="cust_id:10, name:40, dob:8, pan:10")
SKIP = S("skip_rows", "Lines to skip at the top", "number", help="Header or title lines before the data.", default="0")
FORMAT = S("format", "File format", "select", True, "How the file is written.", "csv", ["csv", "tsv", "xlsx", "xls", "json", "jsonl", "xml", "parquet", "avro", "fixed_width"])
FORMAT_OPTS = [S("sheet", "Sheet name", help="Excel only. Empty for the first sheet.", show_if={"format": ["xlsx", "xls"]}),
               S("delimiter", "Delimiter", "select", help="CSV only.", choices=[",", ";", "|", "\t"], show_if={"format": ["csv"]}),
               S("records_path", "Where the list of records is", help="JSON only: dotted path to the list, e.g. data.customers.", show_if={"format": ["json"]}),
               S("record_tag", "Record element", help="XML only: the element that holds one record.", show_if={"format": ["xml"]}),
               S("widths", "Column layout", "textarea", help="Fixed width only: name:width, ...", show_if={"format": ["fixed_width"]}),
               S("encoding", "Text encoding", "select", help="Text formats only.", default="utf-8-sig", choices=["utf-8-sig", "utf-8", "cp1252", "latin-1", "utf-16"], advanced=True)]


def TABLE_OR_QUERY(schema_label="Schema", schema_help="Leave empty for the default schema."):
    return [S("schema", schema_label, help=schema_help, placeholder="public"),
            S("table", "Table or view", help="Read the whole table or view. Give this OR a query.", placeholder="customers"),
            S("query", "Or a SELECT query", "textarea", help="One read-only SELECT (or WITH ... SELECT). Use it to pick columns or filter rows.",
              placeholder="SELECT cust_id, name, dob, mobile, pan FROM customers WHERE status = 'ACTIVE'")]


def DB(port, extra=(), user_label="User name"):
    return [S("host", "Host", required=True, help="Server name or IP address. The administrator may limit hosts (DEDUPE_SOURCE_HOSTS).", placeholder="db.internal.example.com"),
            S("port", "Port", "number", default=str(port) if port else None),
            S("database", "Database", required=True, placeholder="core"),
            S("username", user_label, required=True, help="Use a read-only account: this platform only reads.", placeholder="dedupe_reader"),
            S("password_env", "Password (environment variable)", "env", True, ENV, placeholder="DEDUPE_SRC_CORE_PASSWORD"),
            *extra,
            S("url_env", "Complete connection URL instead (environment variable)", "env", help="Advanced: a full SQLAlchemy URL in an environment "
              "variable. When set, it is used instead of the fields above.", advanced=True)]


SSL_PG = S("ssl_mode", "Encryption (sslmode)", "select", help="verify-full checks the server's certificate and name (recommended; the default in production).",
           default="prefer", choices=["disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
SSL_CA = S("ssl_ca_env", "CA certificate file (environment variable)", "env", help="Path of the certificate authority file, e.g. the Amazon RDS global bundle. " + ENV, advanced=True)

C = []          # the catalogue, in screen order


def add(type, title, group, logo, summary, settings, *, module=None, extra=None, docs=None, tested="unverified", aliases=(), note=None):
    C.append({"type": type, "title": title, "group": group, "logo": logo, "summary": summary, "settings": settings, "module": module, "extra": extra,
              "docs": docs, "tested": tested, "aliases": list(aliases), "note": note})


# ---- upload and files -------------------------------------------------------------------------------------------------------------
add("upload", "Upload a file", "Upload", "upload", "CSV, Excel, JSON or Parquet from your computer. Checked, converted and stored encrypted.",
    [S("upload_id", "Uploaded file", required=True)], tested="live")
add("csv", "CSV", "Files on the server", "csv", "Comma-separated text (also ; or | separated).", [PATH, DELIM, ENCODING], tested="live",
    docs="https://arrow.apache.org/docs/python/csv.html")
add("tsv", "TSV", "Files on the server", "tsv", "Tab-separated text.", [PATH, ENCODING], tested="live")
add("xlsx", "Excel workbook (.xlsx)", "Files on the server", "xlsx", "Excel 2007 or later. One sheet per connection.", [PATH, SHEET], tested="live",
    aliases=["excel"], docs="https://openpyxl.readthedocs.io/")
add("xls", "Old Excel workbook (.xls)", "Files on the server", "xls", "Excel 97-2003 files, still common in older systems.", [PATH, SHEET],
    module="xlrd", extra="files", tested="live", docs="https://xlrd.readthedocs.io/")
add("json", "JSON", "Files on the server", "json", "A list of records, or a document with the list inside.", [PATH, RECORDS_PATH, ENCODING], tested="live")
add("jsonl", "JSON Lines", "Files on the server", "jsonl", "One JSON record per line (also called NDJSON). Best for large exports.", [PATH, ENCODING], tested="live")
add("xml", "XML", "Files on the server", "xml", "One element per record. Parsed safely (no external entities).", [PATH, RECORD_TAG],
    module="defusedxml", extra="files", tested="live")
add("fixed_width", "Fixed-width text", "Files on the server", "fixedwidth", "Mainframe and core-banking exports with columns at fixed positions.",
    [PATH, WIDTHS, SKIP, ENCODING], tested="live")
add("parquet", "Parquet", "Files on the server", "parquet", "Columnar files from data lakes and Spark.", [PATH], tested="live",
    docs="https://arrow.apache.org/docs/python/parquet.html")
add("avro", "Avro", "Files on the server", "avro", "Avro container files from Kafka and Hadoop pipelines.", [PATH], module="fastavro", extra="files", tested="live",
    docs="https://fastavro.readthedocs.io/")
add("orc", "ORC", "Files on the server", "orc", "ORC files from Hive. Readable when the server runs Linux or macOS.", [PATH], module="pyarrow.orc", extra="files",
    tested="unverified", note="pyarrow has no ORC reader on Windows.")
add("folder", "Folder of files", "Files on the server", "folder", "Every file matching a pattern (daily drops, one file per branch) as one source.",
    [S("path", "Folder", "path", True, "A folder inside an allowed data folder.", placeholder="/data/drops/crm"),
     S("pattern", "Files to read", required=True, help="A pattern such as *.csv or 2026-*/customers.csv.", default="*.csv"), FORMAT, *FORMAT_OPTS], tested="live")

# ---- databases ---------------------------------------------------------------------------------------------------------------------
add("postgres", "PostgreSQL", "Databases", "postgresql", "PostgreSQL 10 or later, including Google Cloud SQL and Azure Database for PostgreSQL.",
    [*DB(5432, [SSL_PG, SSL_CA]), *TABLE_OR_QUERY()], module="psycopg2", extra="postgres", tested="live",
    docs="https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg2")
add("mysql", "MySQL", "Databases", "mysql", "MySQL 5.7 or 8, including Google Cloud SQL for MySQL.", [*DB(3306, [SSL_CA]), *TABLE_OR_QUERY("Schema", "Not used: MySQL calls the database the schema.")],
    module="pymysql", extra="mysql", docs="https://docs.sqlalchemy.org/en/20/dialects/mysql.html#module-sqlalchemy.dialects.mysql.pymysql")
add("mariadb", "MariaDB", "Databases", "mariadb", "MariaDB 10.3 or later.", [*DB(3306, [SSL_CA]), *TABLE_OR_QUERY("Schema", "Not used.")],
    module="pymysql", extra="mysql", docs="https://docs.sqlalchemy.org/en/20/dialects/mysql.html#mariadb-support")
SQLSERVER = [S("odbc_driver", "Driver", "select", help="Microsoft's ODBC Driver 18 (recommended; installed on the server) or the pymssql library.",
               default="ODBC Driver 18 for SQL Server", choices=["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "pymssql"]),
             S("trust_server_certificate", "Trust the server certificate without checking", "select", help="Only for test servers with a self-signed certificate.",
               default="no", choices=["no", "yes"], advanced=True)]
add("sqlserver", "Microsoft SQL Server", "Databases", "sqlserver", "SQL Server 2016 or later. Connections are always encrypted.",
    [*DB(1433, SQLSERVER), *TABLE_OR_QUERY("Schema", "Usually dbo.")], module="pyodbc", extra="sqlserver",
    docs="https://learn.microsoft.com/sql/connect/python/pyodbc/python-sql-driver-pyodbc")
add("azure_sql", "Azure SQL Database", "Databases", "azuresql", "Azure SQL Database and Managed Instance (host: yourserver.database.windows.net).",
    [*DB(1433, SQLSERVER), *TABLE_OR_QUERY("Schema", "Usually dbo.")], module="pyodbc", extra="sqlserver",
    docs="https://learn.microsoft.com/azure/azure-sql/database/connect-query-python")
add("oracle", "Oracle Database", "Databases", "oracle", "Oracle 12c or later (thin mode: no Oracle Client needed). Common under core banking systems.",
    [*DB(1521, [S("service_name", "Service name", help="The database service, e.g. ORCLPDB1. If you only have a SID, put it in Database instead.", placeholder="ORCLPDB1")]),
     *TABLE_OR_QUERY("Schema (owner)", "The owner of the table, e.g. FINACLE.")], module="oracledb", extra="oracle",
    docs="https://docs.sqlalchemy.org/en/20/dialects/oracle.html#module-sqlalchemy.dialects.oracle.oracledb")
add("db2", "IBM Db2", "Databases", "db2", "Db2 for Linux, UNIX and Windows (LUW).", [*DB(50000), *TABLE_OR_QUERY()], module="ibm_db_sa", extra="db2",
    docs="https://github.com/ibmdb/python-ibmdbsa")
add("rds", "Amazon RDS / Aurora", "Databases", "aws", "A database on Amazon RDS or Aurora: PostgreSQL, MySQL, MariaDB, SQL Server or Oracle.",
    [S("engine", "Engine", "select", True, "The database engine of the RDS instance or Aurora cluster.", "postgres", ["postgres", "mysql", "mariadb", "sqlserver", "oracle"]),
     *DB(None, [SSL_PG, SSL_CA, S("service_name", "Oracle service name", show_if={"engine": ["oracle"]})]), *TABLE_OR_QUERY()],
    aliases=["aurora"], docs="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html",
    note="Use the RDS certificate bundle (SSL CA) with verify-full. IAM database authentication is not supported yet: use a database user.")
add("sqlite", "SQLite file", "Databases", "sqlite", "A SQLite database file, opened read-only.", [PATH, *TABLE_OR_QUERY()[1:]], tested="live")
add("duckdb", "DuckDB file", "Databases", "duckdb", "A DuckDB database file, opened read-only.", [PATH, *TABLE_OR_QUERY("Schema", "Usually main.")], tested="live")
add("sql", "Other database (connection URL)", "Databases", "sql", "Any database SQLAlchemy supports, from a complete URL in an environment variable.",
    [S("url_env", "Connection URL (environment variable)", "env", True, ENV, placeholder="DEDUPE_SRC_WAREHOUSE_URL"), *TABLE_OR_QUERY()], tested="live",
    docs="https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls")

# ---- cloud data warehouses ---------------------------------------------------------------------------------------------------------
add("snowflake", "Snowflake", "Cloud data warehouses", "snowflake", "Snowflake tables and views.",
    [S("account", "Account identifier", required=True, help="organisation-account, e.g. myorg-myaccount.", placeholder="myorg-myaccount"),
     S("username", "User name", required=True), S("password_env", "Password (environment variable)", "env", True, ENV),
     S("warehouse", "Warehouse", placeholder="COMPUTE_WH"), S("role", "Role", placeholder="DEDUPE_READER"), S("database", "Database", required=True),
     *TABLE_OR_QUERY("Schema", "The schema of the table."), S("url_env", "Complete connection URL instead (environment variable)", "env", advanced=True)],
    module="snowflake.sqlalchemy", extra="snowflake", docs="https://docs.snowflake.com/en/developer-guide/python-connector/sqlalchemy")
add("redshift", "Amazon Redshift", "Cloud data warehouses", "aws", "Redshift provisioned clusters and Redshift Serverless.",
    [*DB(5439, [S("ssl_mode", "Certificate check", "select", help="verify-full checks the server name too.", default="verify-ca", choices=["verify-ca", "verify-full"])]),
     *TABLE_OR_QUERY()], module="sqlalchemy_redshift", extra="redshift", docs="https://github.com/sqlalchemy-redshift/sqlalchemy-redshift")
add("bigquery", "Google BigQuery", "Cloud data warehouses", "bigquery", "BigQuery tables and views.",
    [S("project", "Project id", required=True, placeholder="my-gcp-project"), S("dataset", "Dataset", required=True, placeholder="crm"),
     S("credentials_env", "Service account key file (environment variable)", "env", help="Path of a service account JSON key with BigQuery read access. "
       "Leave empty to use the server's own Google identity. " + ENV),
     S("table", "Table or view", placeholder="customers"), S("query", "Or a SELECT query", "textarea", placeholder="SELECT * FROM crm.customers"), S("url_env", "Complete connection URL instead (environment variable)", "env", help="Advanced: a full SQLAlchemy URL in an environment variable. When set, it is used instead of the fields above.", advanced=True)],
    module="sqlalchemy_bigquery", extra="bigquery", docs="https://googleapis.dev/python/sqlalchemy-bigquery/latest/README.html")
add("databricks", "Databricks SQL", "Cloud data warehouses", "databricks", "Tables in a Databricks SQL warehouse (Unity Catalog).",
    [S("host", "Server host name", required=True, placeholder="adb-1234567890123456.7.azuredatabricks.net"),
     S("http_path", "HTTP path", required=True, help="From the SQL warehouse's connection details.", placeholder="/sql/1.0/warehouses/abc123"),
     S("token_env", "Access token (environment variable)", "env", True, ENV), S("catalog", "Catalog", placeholder="main"),
     S("schema", "Schema", placeholder="crm"), S("table", "Table or view"), S("query", "Or a SELECT query", "textarea"), S("url_env", "Complete connection URL instead (environment variable)", "env", help="Advanced: a full SQLAlchemy URL in an environment variable. When set, it is used instead of the fields above.", advanced=True)],
    module="databricks.sqlalchemy", extra="databricks", docs="https://docs.databricks.com/en/dev-tools/sqlalchemy.html")

# ---- cloud storage and file transfer ------------------------------------------------------------------------------------------------
OBJECT_FORMAT = S("format", "File format", "select", True, "How the files are written.", "csv", ["csv", "tsv", "parquet", "json", "jsonl", "xlsx"])
OBJECT_PATH = S("path", "File or pattern", required=True, help="The object key, or a pattern for several files with the same columns.", placeholder="exports/customers/*.parquet")
add("sftp", "SFTP", "Cloud storage and file transfer", "sftp", "A file on an SFTP server, the usual way banks exchange files.",
    [S("host", "Host", required=True, placeholder="sftp.partner.example.com"), S("port", "Port", "number", default="22"),
     S("username", "User name", required=True),
     S("password_env", "Password (environment variable)", "env", help="Give this or a private key. " + ENV),
     S("private_key_env", "Private key (environment variable)", "env", help="The key text (OpenSSH or PEM, no passphrase). " + ENV),
     S("host_key", "Server host key", "textarea", True, "The server's public key as it appears in known_hosts, e.g. ssh-ed25519 AAAA.... "
       "Ask the server's administrator (or run ssh-keyscan). An unknown or changed key is refused.", placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA..."),
     S("path", "Remote file", required=True, placeholder="/outbound/customers_20261007.csv"), FORMAT, *FORMAT_OPTS],
    module="paramiko", extra="sftp", tested="live", docs="https://docs.paramiko.org/en/stable/api/client.html")
add("s3", "Amazon S3", "Cloud storage and file transfer", "aws", "Files in an S3 bucket, or in an S3-compatible store (MinIO, Ceph, Wasabi, Cloudflare R2).",
    [S("bucket", "Bucket", required=True, placeholder="bank-data-exports"), OBJECT_PATH, OBJECT_FORMAT,
     S("region", "Region", placeholder="ap-south-1"),
     S("auth", "Sign in with", "select", help="Access keys, or the server's own AWS identity (instance role or profile).", default="keys", choices=["keys", "credential_chain"]),
     S("access_key_env", "Access key id (environment variable)", "env", help=ENV, show_if={"auth": ["keys"]}),
     S("secret_key_env", "Secret access key (environment variable)", "env", help=ENV, show_if={"auth": ["keys"]}),
     S("endpoint", "Endpoint (S3-compatible stores only)", help="host or host:port of MinIO / Ceph / Wasabi. Empty for Amazon S3.", advanced=True),
     S("url_style", "Address style", "select", default="path", choices=["path", "vhost"], advanced=True),
     S("sheet", "Sheet name", help="Excel only.", show_if={"format": ["xlsx"]}),
     S("delimiter", "Delimiter", "select", choices=[",", ";", "|", "\t"], show_if={"format": ["csv"]})],
    tested="simulated", docs="https://duckdb.org/docs/extensions/httpfs/s3api")
add("gcs", "Google Cloud Storage", "Cloud storage and file transfer", "gcs", "Files in a Cloud Storage bucket (HMAC keys).",
    [S("bucket", "Bucket", required=True), OBJECT_PATH, OBJECT_FORMAT,
     S("hmac_key_env", "HMAC access id (environment variable)", "env", True, ENV), S("hmac_secret_env", "HMAC secret (environment variable)", "env", True, ENV),
     S("sheet", "Sheet name", show_if={"format": ["xlsx"]})], docs="https://duckdb.org/docs/guides/network_cloud_storage/gcs_import")
add("azure_blob", "Azure Blob Storage / ADLS", "Cloud storage and file transfer", "azure", "Files in an Azure storage container (also Data Lake Storage Gen2).",
    [S("account", "Storage account", required=True, placeholder="bankexports"), S("container", "Container", required=True, placeholder="customers"), OBJECT_PATH, OBJECT_FORMAT,
     S("auth", "Sign in with", "select", default="connection_string", choices=["connection_string", "credential_chain"],
       help="A connection string, or the server's managed identity / Azure CLI login."),
     S("connection_string_env", "Connection string (environment variable)", "env", help=ENV, show_if={"auth": ["connection_string"]}),
     S("sheet", "Sheet name", show_if={"format": ["xlsx"]})], docs="https://duckdb.org/docs/extensions/azure")

# ---- applications and APIs ----------------------------------------------------------------------------------------------------------
add("salesforce", "Salesforce", "Applications and APIs", "salesforce", "Records from Salesforce (Sales, Service or Financial Services Cloud) with a SOQL query.",
    [S("instance", "My Domain", required=True, help="Your org's My Domain host.", placeholder="acme.my.salesforce.com"),
     S("client_id", "Connected app consumer key", help="A connected app with the OAuth client credentials flow enabled and a run-as user with read access."),
     S("client_secret_env", "Consumer secret (environment variable)", "env", help=ENV),
     S("access_token_env", "Or an access token (environment variable)", "env", help="Instead of the connected app. " + ENV, advanced=True),
     S("query", "SOQL query", "textarea", True, "One SELECT ... FROM ... statement.", "SELECT Id, FirstName, LastName, Email, MobilePhone, Birthdate, MailingStreet, "
       "MailingCity, MailingPostalCode FROM Contact"),
     S("api_version", "API version", default="v62.0", advanced=True)],
    tested="simulated", docs="https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm")
add("hubspot", "HubSpot", "Applications and APIs", "hubspot", "Contacts, companies or other CRM objects from HubSpot.",
    [S("token_env", "Private app access token (environment variable)", "env", True, "A private app with crm.objects.contacts.read (or the object's read scope). " + ENV),
     S("object", "Object", "select", True, default="contacts", choices=["contacts", "companies", "deals", "tickets"]),
     S("properties", "Properties to read", "textarea", help="Internal property names, separated by commas.", default="firstname, lastname, email, phone, mobilephone, date_of_birth, address, city, zip")],
    tested="simulated", docs="https://developers.hubspot.com/docs/api/crm/contacts")
add("dynamics365", "Microsoft Dynamics 365", "Applications and APIs", "dynamics", "Contacts, accounts or any Dataverse table from Dynamics 365.",
    [S("org_url", "Environment URL", required=True, placeholder="https://yourorg.crm8.dynamics.com"),
     S("tenant_id", "Tenant id", required=True, help="Microsoft Entra tenant (directory) id."),
     S("client_id", "Application (client) id", required=True, help="An app registration added as an application user with read access."),
     S("client_secret_env", "Client secret (environment variable)", "env", True, ENV),
     S("entity", "Table (entity set name)", required=True, default="contacts"),
     S("select", "Columns", "textarea", help="Logical names, separated by commas. Empty reads every column.",
       default="contactid, fullname, firstname, lastname, birthdate, emailaddress1, mobilephone, address1_line1, address1_city, address1_postalcode")],
    tested="simulated", docs="https://learn.microsoft.com/power-apps/developer/data-platform/webapi/query-data-web-api")
add("rest_api", "REST API", "Applications and APIs", "restapi", "Any JSON API that returns a list of records, with paging and sign-in.",
    [S("url", "Address", required=True, help="https only.", placeholder="https://api.example.com/v1/customers"),
     S("auth", "Sign in with", "select", default="none", choices=["none", "bearer", "basic", "api_key", "oauth2"]),
     S("token_env", "Bearer token (environment variable)", "env", help=ENV, show_if={"auth": ["bearer"]}),
     S("username", "User name", show_if={"auth": ["basic"]}),
     S("password_env", "Password (environment variable)", "env", help=ENV, show_if={"auth": ["basic"]}),
     S("api_key_header", "Header name", default="X-API-Key", show_if={"auth": ["api_key"]}),
     S("api_key_env", "API key (environment variable)", "env", help=ENV, show_if={"auth": ["api_key"]}),
     S("token_url", "Token address", help="OAuth 2.0 client credentials token endpoint.", show_if={"auth": ["oauth2"]}),
     S("client_id", "Client id", show_if={"auth": ["oauth2"]}),
     S("client_secret_env", "Client secret (environment variable)", "env", help=ENV, show_if={"auth": ["oauth2"]}),
     S("scope", "Scope", show_if={"auth": ["oauth2"]}),
     S("records_path", "Where the list of records is", help="Dotted path in the answer, e.g. data or results. Empty when the answer is the list."),
     S("pagination", "Paging", "select", default="none", choices=["none", "next_link", "page", "offset", "cursor"],
       help="How the API gives the next page: a link in the answer, a page number, an offset, or a cursor."),
     S("next_link_path", "Next link in the answer", default="next", show_if={"pagination": ["next_link"]}),
     S("page_param", "Page parameter", default="page", show_if={"pagination": ["page"]}),
     S("page_start", "First page number", "number", default="1", show_if={"pagination": ["page"]}),
     S("offset_param", "Offset parameter", default="offset", show_if={"pagination": ["offset"]}),
     S("limit_param", "Page size parameter", default="limit", show_if={"pagination": ["offset", "page"]}),
     S("page_size", "Page size", "number", default="100", show_if={"pagination": ["offset", "page"]}),
     S("cursor_param", "Cursor parameter", default="cursor", show_if={"pagination": ["cursor"]}),
     S("cursor_path", "Cursor in the answer", default="next_cursor", show_if={"pagination": ["cursor"]}),
     S("max_pages", "Stop after this many pages", "number", default="1000", advanced=True)],
    tested="simulated")
add("mongodb", "MongoDB", "Applications and APIs", "mongodb", "A MongoDB collection (also Amazon DocumentDB and Azure Cosmos DB for MongoDB).",
    [S("uri_env", "Connection string (environment variable)", "env", help="mongodb:// or mongodb+srv:// string. Or fill in host and user below. " + ENV),
     S("host", "Host"), S("port", "Port", "number", default="27017"), S("username", "User name"),
     S("password_env", "Password (environment variable)", "env", help=ENV), S("auth_source", "Authentication database", default="admin", advanced=True),
     S("tls", "Encrypted connection (TLS)", "select", default="yes", choices=["yes", "no"], advanced=True),
     S("database", "Database", required=True), S("collection", "Collection", required=True, placeholder="customers"),
     S("filter", "Filter (JSON)", "textarea", help="Which documents to read, as a find() filter.", placeholder='{"status": "active"}'),
     S("projection", "Fields (JSON)", "textarea", help="Which fields to read, e.g. {\"name\": 1, \"dob\": 1}.", advanced=True)],
    module="pymongo", extra="mongodb", tested="simulated", docs="https://pymongo.readthedocs.io/en/stable/api/pymongo/collection.html#pymongo.collection.Collection.find")
add("gsheet", "Google Sheets", "Applications and APIs", "googlesheets", "A sheet shared by link, or read with an access token.",
    [S("spreadsheet_id", "Spreadsheet id", required=True, help="The long id in the sheet's address."), S("gid", "Tab id (gid)", default="0"),
     S("token_env", "Access token (environment variable)", "env", help="Only for private sheets. " + ENV)], aliases=["google_sheets"])

BY_TYPE = {c["type"]: c for c in C} | {a: c for c in C for a in c["aliases"]}


def allowed_settings() -> dict[str, set[str]]:
    out = {}
    for t, c in BY_TYPE.items():
        names = {s["name"] for s in c["settings"]} | {"type"}
        if c["type"] in ("postgres", "mysql", "mariadb", "rds", "aurora", "sqlserver", "azure_sql", "oracle", "db2", "redshift", "snowflake", "bigquery", "databricks"):    # every DatabaseConnector type
            names |= {"url_env"}
        out[t] = names
    return out


def public(c: dict) -> dict:
    """What the screens get for one connector: everything plus whether its driver is installed on this server."""
    return {**{k: v for k, v in c.items() if k not in ("module", "extra")}, "installed": available(c["module"]),
            "install": f'pip install "dedupe[{c["extra"]}]"' if c["extra"] else None}
