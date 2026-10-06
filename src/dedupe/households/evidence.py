"""Relationship evidence between resolved people (clusters). Families are NOT merges: a household is a set of
distinct people linked by evidence such as living together, sharing a policy, or being named on each other's records.

Each evidence type contributes at most once per pair of people (its strongest instance), so ten records sharing one
phone count the same as two. Weights are transparent defaults, editable later by business users.
"""
import duckdb

from dedupe import crypto

WEIGHTS = {
    "policy_member": 5.0,        # insured on the same (floater) policy as another person
    "policy_nominee": 3.0,       # nominee on the same policy
    "same_home": 4.0,            # same PIN + same house/flat number + similar address text
    "same_area_text": 1.0,       # same PIN + similar address text, different/missing flat (neighbours)
    "shared_phone": 4.0,         # same phone, if used by few people (measured ~99% same-household)
    "shared_email": 3.0,
    "named_relative": 3.0,       # a nominee/co-applicant name on one person's record matches the other person (~78%)
    "father_named": 2.0,         # a person's recorded father name matches the other person (~50%: common names)
    "same_surname": 1.0,         # supporting only: counted only when other evidence exists
}
GENERIC_LIMIT = 8     # a phone/email used by more than this many people is generic (agent, call centre): ignored
PIN_BLOCK_LIMIT = 300  # skip address joins for PINs with more records than this
NAME_POP_LIMIT = 50    # a relative name shared by more people than this is too common to link on (its weight would be < w/50 anyway)


def build(con: duckdb.DuckDBPyConnection, std: str, clusters: str, canonical: str) -> int:
    """Creates tables `rc` (records with cluster) and `evidence(ca, cb, ev, w)`. Returns evidence row count."""
    con.execute(f"""CREATE OR REPLACE TABLE rc AS
        SELECT s.*, c.cluster_id, json_extract_string(k.attributes, '$.policy_no') AS policy_no
        FROM {crypto.rp(std)} s
        JOIN {crypto.rp(clusters)} c USING (source_id, source_record_id)
        JOIN {crypto.rp(canonical)} k USING (source_id, source_record_id)""")
    con.execute("CREATE OR REPLACE TEMP TABLE pin_n AS SELECT address_pin, count(*) n FROM rc WHERE address_pin IS NOT NULL GROUP BY 1")
    parts = []
    w = WEIGHTS
    parts.append(f"""SELECT least(a.cluster_id, b.cluster_id) ca, greatest(a.cluster_id, b.cluster_id) cb,
        CASE WHEN 'nominee' IN (a.party_role, b.party_role) THEN 'policy_nominee' ELSE 'policy_member' END ev,
        CASE WHEN 'nominee' IN (a.party_role, b.party_role) THEN {w['policy_nominee']} ELSE {w['policy_member']} END w
        FROM rc a JOIN rc b ON a.policy_no = b.policy_no AND a.cluster_id < b.cluster_id WHERE a.policy_no IS NOT NULL""")
    parts.append(f"""SELECT a.cluster_id, b.cluster_id, 'same_home', {w['same_home']}
        FROM rc a JOIN rc b ON a.address_pin = b.address_pin AND a.address_house = b.address_house AND a.cluster_id < b.cluster_id
        JOIN pin_n p ON p.address_pin = a.address_pin
        WHERE p.n <= {PIN_BLOCK_LIMIT} AND jaro_winkler_similarity(a.address_norm, b.address_norm) >= 0.85""")
    parts.append(f"""SELECT a.cluster_id, b.cluster_id, 'same_area_text', {w['same_area_text']}
        FROM rc a JOIN rc b ON a.address_pin = b.address_pin AND a.cluster_id < b.cluster_id
        JOIN pin_n p ON p.address_pin = a.address_pin
        WHERE p.n <= {PIN_BLOCK_LIMIT} AND jaro_winkler_similarity(a.address_norm, b.address_norm) >= 0.85""")
    for ev, expr in (("shared_phone", "unnest([mobile, mobile_alt])"), ("shared_email", "email_key")):
        parts.append(f"""WITH v AS (SELECT DISTINCT cluster_id, {expr} AS v FROM rc), v2 AS (SELECT * FROM v WHERE v IS NOT NULL),
            n AS (SELECT v, count(DISTINCT cluster_id) n FROM v2 GROUP BY v)
            SELECT a.cluster_id, b.cluster_id, '{ev}', {w[ev]} FROM v2 a JOIN v2 b ON a.v = b.v AND a.cluster_id < b.cluster_id
            JOIN n ON n.v = a.v WHERE n.n <= {GENERIC_LIMIT}""")
    con.execute("""CREATE OR REPLACE TEMP TABLE name_pop AS
        SELECT name_tokens_phon AS nm, count(DISTINCT cluster_id) AS n FROM rc WHERE name_tokens_phon LIKE '% %' GROUP BY 1""")
    con.execute("""CREATE OR REPLACE TEMP TABLE cluster_city AS
        SELECT cluster_id, coalesce(list(DISTINCT city) FILTER (WHERE city IS NOT NULL), []::VARCHAR[]) AS cities FROM rc GROUP BY 1""")
    # A named relative must not contradict the person's known cities (compared across ALL of a person's records).
    same_place = "(len(x.cities) = 0 OR len(y.cities) = 0 OR list_has_any(x.cities, y.cities))"
    father_ok = ("(b.gender IS NULL OR b.gender = 'M') AND "
                 "(a.dob_year IS NULL OR b.dob_year IS NULL OR b.dob_year + 15 <= a.dob_year)")
    for ev, col, extra in (("named_relative", "related_name_phon", "TRUE"), ("father_named", "father_name_phon", father_ok)):
        parts.append(f"""WITH cand AS (
              SELECT DISTINCT a.cluster_id AS ca, b.cluster_id AS cb, a.{col} AS nm,
                     (a.dob_year IS NULL OR b.dob_year IS NULL) AS dob_unknown
              FROM rc a JOIN rc b ON a.{col} = b.name_tokens_phon AND a.cluster_id <> b.cluster_id
              JOIN name_pop np ON np.nm = a.{col}
              JOIN cluster_city x ON x.cluster_id = a.cluster_id JOIN cluster_city y ON y.cluster_id = b.cluster_id
              WHERE np.n <= {NAME_POP_LIMIT} AND a.{col} LIKE '% %' AND {same_place} AND {extra}),
            scored AS (SELECT *, count(*) OVER (PARTITION BY ca, nm) AS k FROM cand)
            SELECT least(ca, cb), greatest(ca, cb), '{ev}', {w[ev]} / k * CASE WHEN dob_unknown AND '{ev}' = 'father_named' THEN 0.5 ELSE 1.0 END FROM scored""")
    union = "\nUNION ALL\n".join(f"({p})" for p in parts)
    con.execute(f"CREATE OR REPLACE TEMP TABLE ev_raw AS SELECT * FROM ({union}) t(ca, cb, ev, w)")
    con.execute("CREATE OR REPLACE TEMP TABLE cluster_surname AS SELECT DISTINCT cluster_id, name_last_phon AS sur FROM rc WHERE name_last_phon IS NOT NULL")
    con.execute(f"""CREATE OR REPLACE TABLE evidence AS
        SELECT ca, cb, ev, max(w) AS w FROM ev_raw WHERE ca <> cb GROUP BY ca, cb, ev
        UNION ALL
        SELECT p.ca, p.cb, 'same_surname', {w['same_surname']} FROM (SELECT DISTINCT ca, cb FROM ev_raw WHERE ca <> cb) p
        WHERE EXISTS (SELECT 1 FROM cluster_surname a JOIN cluster_surname b ON a.sur = b.sur
                      WHERE a.cluster_id = p.ca AND b.cluster_id = p.cb)""")
    return con.execute("SELECT count(*) FROM evidence").fetchone()[0]


def pair_scores(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """[(ca, cb, score, 'ev1+ev2...')] one row per related pair of people."""
    return con.execute("""SELECT ca, cb, sum(w) AS score, string_agg(ev, '+' ORDER BY ev) AS evidence
                          FROM evidence GROUP BY ca, cb""").fetchall()
