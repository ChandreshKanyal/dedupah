"""Generators for Indian-style identifiers. All values are fake; checksums are valid where they exist."""
import random
import string

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(payload: str) -> int:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return _INV[c]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


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
