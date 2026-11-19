"""Generate the synthetic test fixtures required by the spec.

No real personal data is used: every value is synthetic.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "synthetic"

SURNAMES = ["김", "이", "박", "정", "홍", "최", "조", "윤", "구", "백"]
GIVEN = ["길동", "민준", "서연", "도윤", "하은", "유나", "태현", "지민"]
HEADER = ["USER_ID", "USER_NAME", "PHONE", "RESIDENT_NO", "EMAIL", "ADDR", "BIRTH", "LAT", "LNG"]
MIXED_HEADER = HEADER + ["CARD_NO", "BANK_ACCT", "IMSI", "SEC_ID", "ID_SUFFIX"]


def resident_number(seed: int) -> str:
    """Build a checksum-valid synthetic resident registration number."""
    rng = random.Random(seed)
    year = rng.randint(60, 99)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    seq = "".join(str(rng.randint(0, 9)) for _ in range(6))
    digits = f"{year:02d}{month:02d}{day:02d}{seq}"
    weights = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1, 2, 4)
    total = sum(w * int(d) for w, d in zip(weights, digits))
    check = (10 - total % 10) % 10
    return f"{digits[:6]}-{digits[6:]}-{check}"


def luhn_number(prefix: str, length: int, seed: int) -> str:
    rng = random.Random(seed)
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(length - len(prefix) - 1))
    for check in range(10):
        candidate = body + str(check)
        total = 0
        for index, ch in enumerate(reversed(candidate)):
            digit = int(ch)
            if index % 2 == 1:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        if total % 10 == 0:
            return candidate
    return body + "0"


def base_rows(count: int = 8, seed: int = 1) -> list[list[str]]:
    rng = random.Random(seed)
    rows = []
    for i in range(count):
        rows.append([
            str(1001 + i),
            rng.choice(SURNAMES) + rng.choice(GIVEN),
            f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            resident_number(seed * 100 + i),
            f"user{i + 1}@example.com",
            f"테스트로 {rng.randint(1, 300)}-{rng.randint(1, 50)} apartments {rng.randint(1, 20)}호",
            f"19{rng.randint(70, 99)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}",
            f"{rng.uniform(36.0, 37.6):.6f}",
            f"{rng.uniform(126.0, 127.0):.6f}",
        ])
    return rows


def to_text(header: list[str], rows: list[list[str]], delimiter: str = ",") -> str:
    lines = [delimiter.join(header)]
    lines.extend(delimiter.join(row) for row in rows)
    return "\n".join(lines) + "\n"


def write(name: str, content: str, encoding: str) -> Path:
    path = FIXTURE_DIR / name
    path.write_bytes(content.encode(encoding))
    return path


def mixed_rows(rows: list[list[str]]) -> list[list[str]]:
    out = []
    for index, row in enumerate(rows):
        out.append(row + [
            luhn_number("4", 16, index),
            "1234-5678-901234",
            f"310{index + 7:012d}",
            f"user{index + 1}.sec",
            f"acct{index}.id",
        ])
    return out


def generate() -> list[Path]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    rows = base_rows()
    paths = [
        write("utf8_comma.csv", to_text(HEADER, rows, ","), "utf-8"),
        write("utf8_bom.csv", "\ufeff" + to_text(HEADER, rows, ","), "utf-8-sig"),
        write("cp949.csv", to_text(HEADER, rows, ","), "cp949"),
        write("euckr.csv", to_text(HEADER, rows, ","), "euc-kr"),
        write("utf16.csv", to_text(HEADER, rows, ","), "utf-16"),
        write("semicolon.csv", to_text(HEADER, rows, ";"), "utf-8"),
        write("tab.csv", to_text(HEADER, rows, "\t"), "utf-8"),
        write("pipe.csv", to_text(HEADER, rows, "|"), "utf-8"),
        write("no_header.csv", "\n".join(",".join(row) for row in rows) + "\n", "utf-8"),
        write("code_columns.csv", to_text([f"C{i:03d}" for i in range(1, len(HEADER) + 1)], rows), "utf-8"),
        write("shuffled_columns.csv", "\n".join(
            ",".join([row[3], row[0], row[2], row[1], row[4], row[5], row[6], row[7], row[8]])
            for row in rows
        ) + "\n", "utf-8"),
        write("null_mix.csv", "\n".join(
            [",".join(HEADER)]
            + [",".join([row[0], "", row[2], "NULL", row[4], "", row[6], row[7], row[8]]) for row in rows]
        ) + "\n", "utf-8"),
        write("irregular.csv", "\n".join(
            [",".join(HEADER)] + [",".join(row) for row in rows] + ["1009,홍길동,01011111111", "1010"]
        ) + "\n", "utf-8"),
        write("quoted_newline.csv",
             'USER_ID,USER_NAME,PHONE\n"1001","홍\n길동","010-1234-5678"\n"1002","김민준","010-2345-6789"\n',
             "utf-8"),
        write("leading_zero.csv", "ID,CODE,NAME\n000123,0010000,김길동\n000124,0020000,이민준\n", "utf-8"),
        write("mixed_pii.csv", to_text(MIXED_HEADER, mixed_rows(rows), ","), "utf-8"),
        write("masked_encrypted.csv",
             "USER_ID,USER_NAME,USER_NAME_MASK,USER_NAME_HASH,USER_NAME_ENC,"
             "RESIDENT_NO,RESIDENT_NO_MASK\n"
             + "\n".join(
                 ",".join([
                     row[0],
                     row[1],
                     row[1][0] + "**",
                     "a3f2b9c8d7e6f5a4b3c2d1e0f9a8b7c6",
                     "U2FsdGVkX1+abc123def456",
                     row[3],
                     row[3][:6] + "-*******",
                 ])
                 for row in rows
             ) + "\n",
             "utf-8"),
        write("code_group.csv",
             "GROUP_CODE,CODE,CODE_NAME\n"
             + "\n".join([
                 "GENDER,M,남성", "GENDER,F,여성", "COUNTRY,KR,대한민국", "COUNTRY,US,미국",
                 "BLOOD_TYPE,A,A형", "BLOOD_TYPE,B,B형", "MARITAL,S,미혼",
             ]) + "\n",
             "utf-8"),
    ]
    return paths


def generate_large(rows: int = 130000, path: Path | None = None) -> Path:
    path = path or FIXTURE_DIR / "large_130k.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MIXED_HEADER + ["SN"])
        for index in range(rows):
            writer.writerow(
                base_rows(1, seed=index)[0]
                + [luhn_number("4", 16, index), f"123456789{index % 100:02d}",
                   f"450{index % 100000000000:012d}", f"user{index}.sec", f"acct{index}.id", f"SN{index:06d}"]
            )
    return path


if __name__ == "__main__":
    for generated in generate():
        print("generated", generated)
