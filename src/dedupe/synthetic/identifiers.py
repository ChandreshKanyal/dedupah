"""Generators for Indian-style identifiers. All values are fake; checksums are valid where they exist."""
import random
import string

from dedupe.standardise.verhoeff import verhoeff_check_digit, verhoeff_valid  # noqa: F401


def aadhaar(rng: random.Random) -> str:
    payload = str(rng.randint(2, 9)) + "".join(rng.choices(string.digits, k=10))
    return payload + str(verhoeff_check_digit(payload))


def pan(rng: random.Random, surname: str, holder: str = "P") -> str:
    """AAA + holder type (P=individual) + first letter of surname + 4 digits + check letter."""
    letters = string.ascii_uppercase
    initial = (surname.strip()[:1] or "X").upper()
    if initial not in letters:
        initial = "X"
    return "".join(rng.choices(letters, k=3)) + holder + initial + "".join(rng.choices(string.digits, k=4)) + rng.choice(letters)


def mobile(rng: random.Random) -> str:
    return str(rng.choice([6, 7, 8, 9])) + "".join(rng.choices(string.digits, k=9))


def passport(rng: random.Random) -> str:
    return rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") + "".join(rng.choices(string.digits, k=7))


def driving_licence(rng: random.Random, state_code: str, year: int) -> str:
    return f"{state_code}{rng.randint(1, 30):02d}{year}{rng.randint(0, 9999999):07d}"


def voter_id(rng: random.Random) -> str:
    return "".join(rng.choices(string.ascii_uppercase, k=3)) + "".join(rng.choices(string.digits, k=7))


def vehicle_reg(rng: random.Random, state_code: str) -> str:
    return f"{state_code}{rng.randint(1, 60):02d}{''.join(rng.choices(string.ascii_uppercase, k=2))}{rng.randint(1, 9999):04d}"


class IdFactory:
    """Hands out identifiers that are unique across the whole population."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self._used: set[str] = set()

    def _unique(self, make) -> str:
        while True:
            v = make()
            if v not in self._used:
                self._used.add(v)
                return v

    def aadhaar(self) -> str:
        return self._unique(lambda: aadhaar(self.rng))

    def pan(self, surname: str) -> str:
        return self._unique(lambda: pan(self.rng, surname))

    def mobile(self) -> str:
        return self._unique(lambda: mobile(self.rng))

    def passport(self) -> str:
        return self._unique(lambda: passport(self.rng))

    def dl(self, state_code: str, year: int) -> str:
        return self._unique(lambda: driving_licence(self.rng, state_code, year))

    def voter(self) -> str:
        return self._unique(lambda: voter_id(self.rng))
