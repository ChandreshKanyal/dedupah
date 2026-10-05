"""Five synthetic source systems. Each renders the same hidden people differently, sparsely and noisily."""
import random
from collections import defaultdict
from datetime import date, timedelta

from . import corrupt as c
from . import reference as ref
from .identifiers import vehicle_reg
from .households import Person

CAP = str.capitalize


class Sources:
    def __init__(self, rng: random.Random, people: list[Person], ref_date: date, noise: float = 1.0):
        self.rng, self.noise, self.ref_date = rng, noise, ref_date
        self.people = people
        self.by_hh: dict[str, list[Person]] = defaultdict(list)
        for p in people:
            self.by_hh[p.household_id].append(p)
        self.head = {h: m[0] for h, m in self.by_hh.items()}
        # Iteration orders are shuffled so IDs and row order carry no information about households.
        self.order = list(people)
        rng.shuffle(self.order)
        self.hh_order = list(self.by_hh.items())
        rng.shuffle(self.hh_order)
        self.truth: list[dict] = []
        self.seen_in: dict[str, set[str]] = defaultdict(set)
        self._n: dict[str, int] = defaultdict(int)

    # ---- helpers -------------------------------------------------------------------------------
    def _id(self, prefix: str, width: int = 8) -> str:
        self._n[prefix] += 1
        return f"{prefix}{self._n[prefix]:0{width}d}"

    def _link(self, source, rid, p: Person, role="primary", related: Person | None = None):
        self.truth.append({"source": source, "source_record_id": rid, "person_id": p.person_id,
                           "household_id": p.household_id, "role": role,
                           "related_person_id": related.person_id if related else ""})
        self.seen_in[p.person_id].add(source)

    def _mobile(self, p: Person, allow_old=True) -> str | None:
        m = p.mobile
        if allow_old and p.mobile_old and self.rng.random() < 0.15:
            m = p.mobile_old
        return m

    def _phone(self, p: Person, mult: float = 1.0) -> str | None:
        m = self._mobile(p)
        return c.render_phone(self.rng, m, self.noise * mult) if m else None

    def _addr(self, p: Person) -> dict:
        if p.prev_addr and self.rng.random() < 0.25:
            return p.prev_addr
        return p.addr

    def _name(self, p, **kw) -> str:
        return c.render_name(self.rng, p, noise=self.noise, **kw)

    def _blank(self, v, pm):
        return None if self.rng.random() < pm else v

    # ---- 1. Bank CRM (CSV) -----------------------------------------------------------------------
    def bank_crm(self) -> list[dict]:
        rng, rows = self.rng, []
        styles = {"full": .4, "fl": .3, "initials": .06, "lastfirst": .1, "fi_last": .04, "honorific": .1}
        for p in self.order:
            if not p.is_adult or rng.random() > (0.68 if p.relation == "head" else 0.5):
                continue
            for _ in range(2 if rng.random() < 0.07 else 1):  # same customer onboarded twice
                rid = self._id("CIF")
                a = c.address_parts(rng, self._addr(p), self.noise)
                inc = p.annual_income
                seg = None if inc is None else ("HNI" if inc > 2500000 else "Affluent" if inc > 900000 else "Mass")
                spouse = next((m for m in self.by_hh[p.household_id] if m.relation == "spouse" and p.relation == "head"), None)
                opened = self.ref_date - timedelta(days=rng.randint(30, 365 * 18))
                rows.append({
                    "cust_id": rid,
                    "full_name": self._name(p, styles=styles, upper_p=0.5),
                    "dob": self._blank(c.render_dob(rng, p.dob, "dmy_slash", 0.1, self.noise), 0.03),
                    "gender": self._blank(rng.choice([p.gender, {"M": "Male", "F": "Female"}[p.gender]]), 0.05),
                    "mobile": self._blank(self._phone(p), 0.04),
                    "alt_mobile": self._blank(p.mobile_old and c.render_phone(rng, p.mobile_old, self.noise), 0.85),
                    "email": self._blank(p.email and c.render_email(rng, p.email, self.noise), 0.30),
                    "addr_line1": a["line1"], "addr_line2": self._blank(a["line2"], 0.2),
                    "locality": a["locality"], "city": a["city"], "state": self._blank(a["state"], 0.1), "pincode": a["pin"],
                    "pan": self._blank(p.pan, 0.22),
                    "aadhaar_last4": self._blank(p.aadhaar and p.aadhaar[-4:], 0.5),
                    "occupation": self._blank(p.occupation, 0.2),
                    "annual_income": self._blank(inc, 0.4),
                    "marital_status": self._blank(p.marital_status, 0.3),
                    "segment": seg,
                    "branch_code": f"BR{rng.randint(100, 480)}",
                    "account_type": rng.choices(ref.BANK_PRODUCTS, weights=[50, 8, 12, 12, 6, 2, 10])[0],
                    "account_open_date": c.render_date(rng, opened, "dmy_slash"),
                    "kyc_status": rng.choices(["Verified", "Pending", "Expired", "Partial"], weights=[70, 10, 12, 8])[0],
                    "nominee_name": self._blank(self._name(spouse, upper_p=0.5) if spouse else None, 0.5),
                    "credit_card": rng.choice(["Y", "N", "N"]),
                    "relationship_manager": self._blank(f"RM{rng.randint(1, 60):03d}", 0.5),
                })
                self._link("bank_crm", rid, p)
        return rows

    # ---- 2. Insurance policy system (Excel): one row per party per policy ----------------------------
    def insurance(self) -> list[dict]:
        rng, rows = self.rng, []
        styles = {"full": .45, "fl": .2, "initials": .05, "lastfirst": .05, "fi_last": .03, "honorific": .22}
        for hid, members in self.hh_order:
            head = members[0]
            adults = [m for m in members if m.is_adult]
            if rng.random() > 0.58 or not adults:
                continue
            for _ in range(1 + (rng.random() < 0.4) + (rng.random() < 0.15)):
                proposer = head if rng.random() < 0.7 else rng.choice(adults)
                types = [t for t in ref.POLICY_TYPES if t != "Health Floater" or len(members) > 1]
                ptype = rng.choices(types, weights=[22, 18, 14 if "Health Floater" in types else 0, 14, 8, 10, 6, 8][:len(types)])[0]
                pno = self._id("POL", 9)
                start = self.ref_date - timedelta(days=rng.randint(15, 365 * 12))
                sa = int(max(100000, (proposer.annual_income or 300000) * rng.choice([5, 8, 10, 15, 20])) // 50000 * 50000)
                common = {
                    "policy_no": pno, "policy_type": ptype, "product_name": rng.choice(ref.POLICY_PRODUCTS[ptype]),
                    "sum_assured": sa, "annual_premium": int(sa * rng.uniform(0.004, 0.03)),
                    "premium_mode": rng.choice(["Annual", "Monthly", "Quarterly", "Half-yearly", "Single"]),
                    "policy_status": rng.choices(["In Force", "Lapsed", "Matured", "Surrendered", "Paid Up"], weights=[70, 14, 6, 4, 6])[0],
                    "start_date": c.render_date(rng, start, "dmy_mon"),
                    "agent_code": self._blank(f"AG{rng.randint(1000, 9999)}", 0.25),
                    "channel": rng.choice(["Agent", "Online", "Bancassurance", "Broker", "Direct"]),
                }

                def party(p: Person, role: str, relation: str, sparse=False) -> dict:
                    rid = self._id("INS", 9)
                    d = dict(common)
                    adult_addr = c.address_oneline(rng, self._addr(p), self.noise)
                    d.update({
                        "row_id": rid, "party_role": role, "party_name": self._name(p, styles=styles, upper_p=0.2),
                        "relation_to_proposer": relation,
                        "dob": self._blank(c.render_dob(rng, p.dob, "dmy_mon", 0.15, self.noise), 0.45 if sparse else 0.02),
                        "gender": self._blank(p.gender, 0.6 if sparse else 0.04),
                        "mobile": self._blank(self._mobile(p) or self.head[p.household_id].mobile, 0.85 if sparse else 0.06),
                        "email": self._blank(p.email, 0.97 if sparse else 0.4),
                        "address": None if sparse else self._blank(adult_addr, 0.08),
                        "pan": None if sparse else self._blank(p.pan, 0.55),
                        "occupation_class": None if sparse else self._blank(rng.choice(["Class 1", "Class 2", "Class 3"]), 0.3),
                        "smoker": None if sparse else self._blank("Y" if p.smoker else "N", 0.3),
                        "pre_existing_conditions": None if sparse else self._blank(p.pre_existing or "None", 0.4),
                        "vehicle_reg_no": None,
                    })
                    if d["mobile"]:
                        d["mobile"] = c.render_phone(rng, d["mobile"], self.noise)
                    if d["email"]:
                        d["email"] = c.render_email(rng, d["email"], self.noise)
                    return d, rid

                prow, prid = party(proposer, "Proposer", "Self")
                if ptype == "Motor":
                    prow["vehicle_reg_no"] = vehicle_reg(rng, proposer.addr["sc"])
                nom = None
                if ptype in ("Term Life", "Endowment", "ULIP", "Health Individual") or rng.random() < 0.2:
                    cands = [m for m in members if m is not proposer]
                    nom = rng.choice(cands) if cands else None
                prow["nominee_name"] = self._name(nom, upper_p=0.3) if nom and rng.random() < 0.9 else None
                rows.append(prow)
                self._link("insurance", prid, proposer, "Proposer")
                if ptype == "Health Floater":
                    insured = [m for m in members if m is not proposer and m.relation in ("spouse", "son", "daughter", "father", "mother")][:4]
                    for m in insured:
                        r, rid = party(m, "Insured", _relation(proposer, m))
                        rows.append(r)
                        self._link("insurance", rid, m, "Insured", proposer)
                elif rng.random() < 0.3:
                    r, rid = party(proposer, "Insured", "Self")
                    rows.append(r)
                    self._link("insurance", rid, proposer, "Insured")
                if nom and rng.random() < 0.85:
                    r, rid = party(nom, "Nominee", _relation(proposer, nom), sparse=True)
                    rows.append(r)
                    self._link("insurance", rid, nom, "Nominee", proposer)
        return rows

    # ---- 3. Loans (relational table export) --------------------------------------------------------
    def loans(self) -> list[dict]:
        rng, rows = self.rng, []
        styles = {"full": .5, "fl": .25, "initials": .05, "lastfirst": .1, "fi_last": .03, "honorific": .07}
        for hid, members in self.hh_order:
            head = members[0]
            if not head.is_adult or rng.random() > 0.27:
                continue
            for _ in range(1 + (rng.random() < 0.2)):
                app = head if rng.random() < 0.75 else (rng.choice([m for m in members if m.is_adult]))
                co = next((m for m in members if m.relation == "spouse" and m is not app), None) if rng.random() < 0.5 else None
                rid = self._id("LN", 8)
                a = self._addr(app)
                inc = app.annual_income or 240000
                amt = int(inc * rng.choice([1, 2, 3, 4, 5, 8]) // 10000 * 10000)
                rows.append({
                    "loan_id": rid,
                    "applicant_name": self._name(app, styles=styles, upper_p=0.4, maiden_p=0.2),
                    "applicant_dob": c.render_dob(rng, app.dob, "iso", 0.08, self.noise),
                    "applicant_gender": self._blank(app.gender, 0.02),
                    "father_name": self._blank(app.father_name, 0.25),
                    "mobile": self._blank(self._phone(app), 0.02),
                    "email": self._blank(app.email and c.render_email(rng, app.email, self.noise), 0.25),
                    "current_address": c.address_oneline(rng, a, self.noise),
                    "permanent_address": self._blank(c.address_oneline(rng, app.addr, self.noise), 0.45),
                    "pan": self._blank(app.pan, 0.04),
                    "aadhaar_no": self._blank(app.aadhaar, 0.62),
                    "employment_type": self._blank(app.employment_type, 0.05),
                    "employer": self._blank(app.employer, 0.2),
                    "monthly_income": self._blank(int(inc / 12 // 500 * 500), 0.1),
                    "loan_type": rng.choices(ref.LOAN_TYPES, weights=[25, 25, 18, 7, 12, 8, 5])[0],
                    "loan_amount": amt,
                    "tenure_months": rng.choice([12, 24, 36, 60, 84, 120, 180, 240]),
                    "interest_rate": round(rng.uniform(7.5, 16.5), 2),
                    "cibil_score": self._blank(rng.randint(560, 900), 0.12),
                    "loan_status": rng.choices(["Active", "Closed", "NPA", "Written Off", "Sanctioned"], weights=[55, 30, 5, 2, 8])[0],
                    "disbursed_date": c.render_date(rng, self.ref_date - timedelta(days=rng.randint(20, 365 * 9)), "iso"),
                    "co_applicant_name": self._name(co, upper_p=0.4) if co else None,
                    "co_applicant_pan": self._blank(co.pan, 0.4) if co else None,
                    "co_applicant_mobile": self._blank(co.mobile, 0.5) if co else None,
                    "branch": f"{app.addr['city']} - {rng.choice(['Main', 'Central', 'East', 'West', 'Retail'])}",
                })
                self._link("loans", rid, app, "Applicant", co)
                if co:
                    self.seen_in[co.person_id].add("loans")
                    self.truth.append({"source": "loans", "source_record_id": rid, "person_id": co.person_id,
                                       "household_id": co.household_id, "role": "Co-applicant",
                                       "related_person_id": app.person_id})
        return rows

    # ---- 4. Marketing leads (Google Sheet style, very sparse) -----------------------------------------
    def leads(self) -> list[dict]:
        rng, rows = self.rng, []
        styles = {"full": .1, "fl": .5, "initials": .02, "lastfirst": .02, "fi_last": .03, "honorific": .03}
        for p in self.order:
            if not p.is_adult or rng.random() > 0.4:
                continue
            for _ in range(1 + (rng.random() < 0.25) + (rng.random() < 0.08)):  # the same person submits several forms
                rid = self._id("LD", 7)
                age_val = p.age + rng.choice([0, 0, 0, 1, -1])
                a = self._addr(p)
                rows.append({
                    "lead_id": rid,
                    "name": self._name(p, styles=styles, upper_p=0.05, first_only_p=0.08),
                    "phone": self._blank(self._phone(p, 1.5), 0.08),
                    "email": self._blank(p.email and c.render_email(rng, p.email, self.noise * 1.5), 0.45),
                    "city": self._blank(rng.choice([a["city"]] + a["aliases"]), 0.2),
                    "age": self._blank(age_val if rng.random() < 0.6 else f"{age_val // 10 * 10}-{age_val // 10 * 10 + 9}", 0.35),
                    "gender": self._blank(p.gender, 0.5),
                    "income_band": self._blank(rng.choice(["<3L", "3-6L", "6-12L", "12-25L", "25L+"]), 0.6),
                    "interested_in": rng.choice(ref.LEAD_PRODUCTS),
                    "campaign": rng.choice(ref.CAMPAIGNS),
                    "lead_source": rng.choice(ref.LEAD_SOURCES),
                    "consent_marketing": rng.choice(["Y", "Y", "N", None]),
                    "created_at": c.render_date(rng, self.ref_date - timedelta(days=rng.randint(1, 900)), "dmy_slash"),
                    "agent_notes": self._blank(rng.choice(["Call back evening", "Wants quote", "Not reachable", "DND", "Existing customer?"]), 0.7),
                })
                self._link("leads", rid, p)
        return rows

    # ---- 5. KYC document extractions (what OCR would produce from ID images) ----------------------------
    def kyc_documents(self) -> list[dict]:
        rng, rows = self.rng, []
        styles = {"full": .6, "fl": .3, "lastfirst": .1}
        for p in self.order:
            if not p.is_adult or not ({"bank_crm", "loans"} & self.seen_in[p.person_id]) or rng.random() > 0.6:
                continue
            avail = [(t, v) for t, v in (("PAN", p.pan), ("AADHAAR", p.aadhaar), ("PASSPORT", p.passport),
                                         ("DRIVING_LICENCE", p.dl), ("VOTER_ID", p.voter)) if v]
            for dtype, num in rng.sample(avail, k=min(len(avail), rng.choice([1, 1, 2, 3]))):
                rid = self._id("DOC", 8)
                conf = round(rng.uniform(0.62, 0.995), 3)
                bad = (1 - conf) * 0.25 * self.noise
                shown = num
                if dtype == "AADHAAR" and rng.random() < 0.8:
                    shown = f"{num[:4]} {num[4:8]} {num[8:]}"
                a = c.address_oneline(rng, self._addr(p), self.noise)
                rows.append({
                    "doc_id": rid, "doc_type": dtype,
                    "doc_number": c.ocr_noise(rng, shown, bad),
                    "name_on_doc": c.ocr_noise(rng, self._name(p, styles=styles, upper_p=0.9, maiden_p=0.2), bad / 2),
                    "dob_on_doc": self._blank(c.render_dob(rng, p.dob, "dmy_slash", 0.03, self.noise), 0.05),
                    "father_name_on_doc": self._blank(p.father_name and c.ocr_noise(rng, p.father_name.upper(), bad / 2), 0.35) if dtype in ("PAN", "PASSPORT", "VOTER_ID") else None,
                    "gender_on_doc": self._blank(p.gender, 0.15),
                    "address_on_doc": self._blank(a, 0.3) if dtype in ("AADHAAR", "DRIVING_LICENCE", "PASSPORT", "VOTER_ID") else None,
                    "ocr_confidence": conf,
                    "extraction_method": rng.choice(["ocr_v1", "ocr_v2", "mrz_parse" if dtype == "PASSPORT" else "ocr_v2"]),
                    "image_file": f"images/{rid}.jpg",
                })
                self._link("kyc_documents", rid, p, dtype)
        return rows


def _relation(proposer: Person, m: Person) -> str:
    return {"spouse": "Spouse", "son": "Son", "daughter": "Daughter", "father": "Father", "mother": "Mother",
            "head": "Self"}.get(m.relation if proposer.relation == "head" else m.relation, "Family Member")
