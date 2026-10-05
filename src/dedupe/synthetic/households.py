"""Builds the hidden ground-truth population: households of related people, with deliberate hard cases."""
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import reference as ref
from .identifiers import IdFactory


@dataclass
class Person:
    person_id: str
    household_id: str
    relation: str  # head | spouse | son | daughter | father | mother
    first: str
    middle: str | None
    last: str
    maiden_last: str | None
    gender: str
    dob: date
    community: str
    marital_status: str
    education: str | None
    occupation: str | None
    employment_type: str | None
    employer: str | None
    annual_income: int | None
    mobile: str | None
    mobile_old: str | None
    email: str | None
    addr: dict
    prev_addr: dict | None
    pan: str | None
    aadhaar: str | None
    passport: str | None
    dl: str | None
    voter: str | None
    father_name: str | None
    smoker: bool
    pre_existing: str | None
    traps: list = field(default_factory=list)

    @property
    def is_adult(self) -> bool:
        return self.age >= 18

    age: int = 0  # set at build time relative to the reference date


def age_on(dob: date, ref_date: date) -> int:
    return ref_date.year - dob.year - ((ref_date.month, ref_date.day) < (dob.month, dob.day))


def random_dob(rng: random.Random, age: int, ref_date: date) -> date:
    start = ref_date - timedelta(days=int((age + 1) * 365.25))
    return start + timedelta(days=rng.randint(1, 365))


def make_address(rng: random.Random, city: dict) -> dict:
    return {
        "house": f"{rng.choice(ref.HOUSE_PREFIX)} {rng.randint(1, 450)}" + (f"/{rng.randint(1, 20)}" if rng.random() < 0.3 else ""),
        "society": rng.choice(ref.SOCIETY),
        "street": rng.choice(ref.STREET_WORDS),
        "landmark": rng.choice(ref.LANDMARKS),
        "locality": rng.choice(city["loc"]),
        "city": city["city"],
        "state": city["state"],
        "sc": city["sc"],
        "aliases": city["aliases"],
        "pin": city["pin"] + "".join(rng.choices("0123456789", k=3)),
    }


def make_email(rng: random.Random, first: str, last: str, dob: date) -> str:
    f, l = first.lower().replace("'", ""), last.lower().replace("'", "")
    local = rng.choice([f"{f}.{l}", f"{f}{l}", f"{f[0]}{l}", f"{f}_{l}", f"{f}.{l}{dob.year % 100}",
                        f"{f}{rng.randint(1, 999)}", f"{l}{f[0]}{dob.year}", f"{f}.{l[0]}{rng.randint(1, 99)}"])
    return f"{local}@{rng.choices(ref.FREE_MAIL, weights=[60, 12, 5, 8, 3, 6, 3, ][:len(ref.FREE_MAIL)])[0]}"


def _pick_community(rng, city) -> str:
    return rng.choices(list(city["mix"]), weights=list(city["mix"].values()))[0]


class PopulationBuilder:
    def __init__(self, rng: random.Random, ref_date: date):
        self.rng = rng
        self.ref_date = ref_date
        self.ids = IdFactory(rng)
        self._pid = 0
        self._hid = 0
        self.heads_by_city: dict[str, list[Person]] = {}

    def _next_pid(self) -> str:
        self._pid += 1
        return f"P{self._pid:06d}"

    def _person(self, hid, relation, first, last, gender, age, community, city, addr, middle=None,
                maiden_last=None, marital="Single", dob=None) -> Person:
        rng = self.rng
        dob = dob or random_dob(rng, age, self.ref_date)
        age = age_on(dob, self.ref_date)
        adult = age >= 18
        occ = emp_type = employer = None
        income = None
        edu = None
        if adult:
            if age >= 62 and rng.random() < 0.6:
                occ = "Retired"
            elif gender == "F" and rng.random() < 0.4:
                occ = "Homemaker"
            else:
                occ = rng.choices(ref.OCCUPATIONS, weights=[14, 7, 8, 5, 8, 7, 8, 5, 4, 4, 0, 0, 4, 8])[0][0]
            row = next(o for o in ref.OCCUPATIONS if o[0] == occ)
            emp_type, employer = row[1], rng.choice(row[2])
            lo, hi = row[3]
            income = int(rng.uniform(lo, hi) // 1000 * 1000)
            edu = rng.choices(ref.EDUCATION, weights=[5, 12, 35, 22, 15, 8, 3])[0]
        elif age >= 5:
            edu = "Student"
        mobile = None
        if adult and rng.random() < 0.96:
            mobile = self.ids.mobile()
        mobile_old = self.ids.mobile() if (adult and mobile and rng.random() < 0.08) else None
        p = Person(
            person_id=self._next_pid(), household_id=hid, relation=relation, first=first, middle=middle, last=last,
            maiden_last=maiden_last or last, gender=gender, dob=dob, community=community, marital_status=marital,
            education=edu, occupation=occ, employment_type=emp_type, employer=employer, annual_income=income,
            mobile=mobile, mobile_old=mobile_old,
            email=make_email(rng, first, last, dob) if adult and rng.random() < 0.85 else None,
            addr=addr, prev_addr=None,
            pan=self.ids.pan(maiden_last or last) if rng.random() < (0.9 if adult else 0.04) else None,
            aadhaar=self.ids.aadhaar() if rng.random() < (0.97 if adult else 0.7) else None,
            passport=self.ids.passport() if adult and rng.random() < 0.25 else None,
            dl=self.ids.dl(city["sc"], dob.year + 18) if adult and rng.random() < (0.5 if gender == "M" else 0.2) else None,
            voter=self.ids.voter() if adult and rng.random() < 0.7 else None,
            father_name=None,
            smoker=adult and gender == "M" and rng.random() < 0.22,
            pre_existing=rng.choice(["Diabetes", "Hypertension", "Asthma", "Thyroid", "Cardiac"]) if age >= 35 and rng.random() < 0.18 else None,
        )
        p.age = age
        return p

    def build_household(self) -> list[Person]:
        rng = self.rng
        self._hid += 1
        hid = f"H{self._hid:05d}"
        city = rng.choice(ref.CITIES)
        community = _pick_community(rng, city)
        traps: dict[str, list[str]] = {}
        pool = self.heads_by_city.get(city["city"], [])

        # --- head ---
        gender = "M" if rng.random() < 0.74 else "F"
        age = rng.randint(24, 68)
        names = ref.COMMUNITIES[community]
        first = rng.choice(names["male" if gender == "M" else "female"])
        last = rng.choice(names["surnames"])
        namesake = False
        if pool and rng.random() < 0.05:  # unrelated person with exactly the same name, same city
            twin_of = rng.choice(pool)
            first, last, community, gender = twin_of.first, twin_of.last, twin_of.community, twin_of.gender
            namesake = True
        names = ref.COMMUNITIES[community]
        middle = rng.choice(names["middle"]) if rng.random() < 0.45 else None
        addr = make_address(rng, city)
        has_spouse = age >= 26 and rng.random() < 0.82
        head = self._person(hid, "head", first, last, gender, age, community, city, addr, middle=middle,
                            marital="Married" if has_spouse else rng.choice(["Single", "Single", "Divorced", "Widowed"]))
        head.father_name = f"{rng.choice(names['male'])} {last}"
        if namesake:
            head.traps.append("namesake")
        members = [head]
        self.heads_by_city.setdefault(city["city"], []).append(head)
        if rng.random() < 0.12 and age >= 30:  # moved house; old systems still have the old address
            old_city = city if rng.random() < 0.6 else rng.choice(ref.CITIES)
            head.prev_addr = make_address(rng, old_city)

        # --- spouse ---
        spouse = None
        if has_spouse:
            sg = "F" if gender == "M" else "M"
            sfirst = rng.choice(names["female" if sg == "F" else "male"])
            maiden = rng.choice(names["surnames"])
            keep = sg == "F" and rng.random() < 0.20
            slast = maiden if (sg == "M" or keep) else head.last
            spouse = self._person(hid, "spouse", sfirst, slast, sg, max(21, age - rng.randint(-4, 7)), community, city,
                                  addr, middle=rng.choice(names["middle"]) if rng.random() < 0.3 else None,
                                  maiden_last=maiden, marital="Married")
            spouse.father_name = f"{rng.choice(names['male'])} {maiden}"
            if slast != maiden:
                spouse.traps.append("maiden_name")
            members.append(spouse)

        # --- children ---
        kids = []
        if has_spouse:
            for i in range(rng.choices([0, 1, 2, 3], weights=[15, 35, 35, 15])[0]):
                cage = age - rng.randint(22, 36) - i * rng.randint(1, 4)
                if cage < 0:
                    continue
                cg = rng.choice(["M", "F"])
                cfirst = rng.choice(names["male" if cg == "M" else "female"])
                if i == 0 and cg == "M" and gender == "M" and rng.random() < 0.06:
                    cfirst = head.first  # Junior carries the same name as Senior
                c = self._person(hid, "son" if cg == "M" else "daughter", cfirst, head.last, cg, cage, community, city,
                                 addr, middle=rng.choice(names["middle"]) if rng.random() < 0.3 else None,
                                 marital="Married" if cage > 27 and rng.random() < 0.3 else "Single")
                if cfirst == head.first:
                    c.traps.append("junior_senior")
                    head.traps.append("junior_senior")
                kids.append(c)
            if kids and rng.random() < 0.07:  # twins: same DOB, near-identical names
                k = kids[0]
                tg = rng.choice(["M", "F"])
                pool_names = names["male" if tg == "M" else "female"]
                same_initial = [n for n in pool_names if n[0] == k.first[0] and n != k.first]
                tfirst = rng.choice(same_initial or pool_names)
                t = self._person(hid, "son" if tg == "M" else "daughter", tfirst, head.last, tg, k.age, community, city,
                                 addr, dob=k.dob, marital=k.marital_status)
                k.traps.append("twin")
                t.traps.append("twin")
                kids.append(t)
        members += kids

        # --- elderly parents living with the head ---
        if age >= 33 and rng.random() < 0.28:
            for rel, pg in (("father", "M"), ("mother", "F")):
                if rng.random() < (0.7 if rel == "father" else 0.85):
                    plast = head.last if (gender == "M" or pg == "M") else rng.choice(names["surnames"])
                    par = self._person(hid, rel, rng.choice(names["male" if pg == "M" else "female"]), plast, pg,
                                       age + rng.randint(23, 32), community, city, addr,
                                       marital="Married" if rel == "father" else rng.choice(["Married", "Widowed"]))
                    members.append(par)
        # head's father_name should match a parent present in the household
        fathers = [m for m in members if m.relation == "father"]
        if fathers:
            head.father_name = f"{fathers[0].first} {fathers[0].last}"
        for m in members:
            if m.relation in ("son", "daughter"):
                m.father_name = f"{head.first} {head.last}" if gender == "M" else (f"{spouse.first} {spouse.last}" if spouse else None)

        # --- shared contact details (the main source of false merges) ---
        for m in members[1:]:
            r = rng.random()
            if m.age < 18:
                m.mobile = head.mobile if r < 0.9 else None
                if m.mobile:
                    m.traps.append("shared_mobile")
            elif m.mobile and r < 0.14:
                m.mobile = head.mobile
                m.traps.append("shared_mobile")
            if m.email and m.age >= 18 and rng.random() < 0.10 and head.email:
                m.email = head.email
                m.traps.append("shared_email")
        if spouse and rng.random() < 0.1:  # landline-style shared family number alongside own mobile
            spouse.mobile_old = head.mobile
        return members

    def build(self, n_households: int) -> list[Person]:
        out: list[Person] = []
        for _ in range(n_households):
            out.extend(self.build_household())
        return out
