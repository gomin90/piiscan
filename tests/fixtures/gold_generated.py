"""Generated gold cells: 30 positive and 30 negative controls per policy type.

`gold_cells.CASES` holds the hand-written edge cases. This module adds the volume
required by the accuracy spec (>=30 positives and >=30 negatives for every type),
generated deterministically from a seeded index so the numbers are reproducible.

A `NOT_PERSONAL` value here is genuinely not that type: wrong length, bad checksum,
system-generated identifier, or a shape that only *looks* like the type. Where such a
near-miss still fires another detector, the harness records it as a false positive for
that detector - which is the signal we want.
"""
from __future__ import annotations

PERSONAL = "PERSONAL"
NOT_PERSONAL = "NOT_PERSONAL"

RESIDENT_WEIGHTS = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1, 2, 4)  # the weights in app.utils.validators
SURNAMES = ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "한")
GIVEN = ("길동", "철수", "영순", "민준", "서연", "도준", "지우", "재현", "소유", "하은")


def resident_checksum(first12: str) -> int:
    return (10 - sum(int(d) * w for d, w in zip(first12, RESIDENT_WEIGHTS)) % 10) % 10


def luhn_valid(digits: str) -> bool:
    """Same convention as app.utils.validators.luhn_valid."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def luhn_complete(prefix: str) -> str:
    for check in range(10):
        if luhn_valid(prefix + str(check)):
            return prefix + str(check)
    return prefix + "0"


def cases() -> list[tuple[str, str, str, tuple[str, ...]]]:
    out: list[tuple[str, str, str, tuple[str, ...]]] = []

    def add(column: str, key: str, positives: list[str], negatives: list[str],
            positive_status: str = PERSONAL) -> None:
        assert len(positives) >= 30 and len(negatives) >= 30
        out.extend((column, value, positive_status, (key,)) for value in positives)
        out.extend((column, value, NOT_PERSONAL, ()) for value in negatives)

    # --- RESIDENT_ID: YYMMDD + 6 digits + check digit, valid checksum ------------
    pos, neg = [], []
    for i in range(30):
        head = f"{60 + i % 40:02d}{(i % 12) + 1:02d}{(i % 28) + 1:02d}"
        tail = f"{(i * 7919) % 1000000:06d}"
        pos.append(f"{head}{tail}{resident_checksum(head + tail)}")
        neg.append(f"{head}{tail}{(resident_checksum(head + tail) + 1) % 10}")
    add("RESIDENT_NO", "RESIDENT_ID", pos, neg)

    # --- PASSPORT -----------------------------------------------------------------
    pos = [f"{('E', 'D')[i % 2]}{10000000 + i * 137:08d}" for i in range(30)]
    # Non-Korean letters + a short digit run: passport-shaped but not a KR passport.
    neg = [f"{('A', 'B', 'C', 'F', 'G', 'H', 'J', 'K')[i % 8]}{100000 + i * 131:06d}" for i in range(30)]
    add("PASSPORT_NO", "PASSPORT", pos, neg)

    # --- DRIVER_LICENSE: issuance year + serial + branch code ---------------------
    pos = [f"{1980 + i % 40:04d}{(i * 911) % 10000:04d}-{(i % 90) + 10:02d}" for i in range(30)]
    neg = [f"{2180 + i % 40:04d}{(i * 911) % 10000:04d}-{(i % 90) + 10:02d}" for i in range(30)]
    add("DL_NO", "DRIVER_LICENSE", pos, neg)

    # --- FOREIGNER_ID ---------------------------------------------------------------
    pos = [f"{10 + i:02d}{chr(65 + i % 26)}{1000000 + i * 137:07d}" for i in range(30)]
    neg = [f"{10 + i:02d}{10000000 + i * 137:08d}" for i in range(30)]  # no letter
    add("FOREIGNER_ID", "FOREIGNER_ID", pos, neg)

    # --- SOCIAL_SECURITY -------------------------------------------------------------
    pos = [f"{100 + i:03d}-{(i % 90) + 10:02d}-{1000 + i * 11:04d}" for i in range(30)]
    neg = [f"{100 + i:03d}-{1000 + i * 11:04d}-{(i % 90) + 10:02d}" for i in range(30)]
    add("SSN", "SOCIAL_SECURITY", pos, neg)

    # --- INSURANCE_NUMBER -----------------------------------------------------------
    pos = [f"{(i % 90) + 10:02d}-{100000 + i * 311:06d}-{1000 + i * 7:04d}" for i in range(30)]
    neg = [f"{100 + i:03d}-{1000 + i * 7:04d}-{100000 + i * 311:06d}" for i in range(30)]
    add("NHI_NO", "INSURANCE_NUMBER", pos, neg)

    # --- BIOMETRIC: fingerprint template blobs -------------------------------------
    pos = ["iVBOR" + "Q" * 12 + f"{i:04d}" for i in range(30)]
    neg = [f"LOGROW{(i * 7919) % 10**16:016d}" for i in range(30)]
    add("FP_TEMPLATE", "BIOMETRIC", pos, neg)

    # --- CARD_NUMBER: valid BIN + Luhn ----------------------------------------------
    pos, neg = [], []
    for i in range(30):
        prefix = f"{4 if i % 2 else 5}{1234 + i:04d}{(i * 7919) % 10000:04d}{(i * 31) % 10000:04d}"
        pos.append(luhn_complete(prefix))
        bad = f"{4 + i % 5}{(i * 7919) % 1000000:06d}{(i * 31) % 1000000:06d}"
        neg.append(bad if not luhn_valid(bad) else bad + "1")
    add("CARD_NO", "CARD_NUMBER", pos, neg)

    # --- BANK_ACCOUNT ---------------------------------------------------------------
    pos = [f"{1000 + i:04d}-{10000000 + i * 311:08d}-{(i % 90) + 10:02d}" for i in range(30)]
    neg = [f"{1000 + i:04d}-0000-{(i * 7919) % 100:02d}" for i in range(30)]
    add("ACCOUNT_NO", "BANK_ACCOUNT", pos, neg)

    # --- LOCATION: lat,lon in one cell ---------------------------------------------
    pos = [f"{37 + (i % 90) / 100:.6f},{126 + (i % 90) / 100:.6f}" for i in range(30)]
    neg = [f"{91 + (i % 40) / 100:.6f},{181 + (i % 40) / 100:.6f}" for i in range(30)]
    add("GPS_LOC", "LOCATION", pos, neg)

    # --- LATITUDE / LONGITUDE (separate columns) ----------------------------------
    pos = [f"{(i % 180) - 90:.6f}" for i in range(30)]
    neg = [f"{90 + (i % 60) + 0.5:.6f}" for i in range(30)]  # outside -90..90
    add("LATITUDE", "LATITUDE", pos, neg)
    pos = [f"{(i % 360) - 180:.6f}" for i in range(30)]
    neg = [f"{180 + (i % 60) + 0.5:.6f}" for i in range(30)]  # outside -180..180
    add("LONGITUDE", "LONGITUDE", pos, neg)

    # --- CELL_ID ------------------------------------------------------------------
    pos = [f"010{10000000 + i * 137:08d}" for i in range(30)]
    neg = [f"CELL-{(i * 7919) % 10000:04d}-{i}" for i in range(30)]
    add("CELL_ID", "CELL_ID", pos, neg)

    # --- RFID -----------------------------------------------------------------------
    pos = [f"E280{(i * 7919) % 10**12:012X}" for i in range(30)]
    neg = [f"{(i * 31) % 10000:04d}" for i in range(30)]
    add("RFID_TAG", "RFID", pos, neg)

    # --- WIRELESS_NETWORK -------------------------------------------------------------
    pos = [f"Office-Wi-Fi-{i + 1}" for i in range(30)]
    neg = [f"Widget-{i + 1}" for i in range(30)]
    add("SSID", "WIRELESS_NETWORK", pos, neg)

    # --- AUTH_CREDENTIAL ------------------------------------------------------------
    pos = [f"Passw0rd{i}!" for i in range(30)]
    neg = [f"{(i * 7919) % 10**32:032x}" for i in range(30)]  # digest-shaped
    add("PASSWORD", "AUTH_CREDENTIAL", pos, neg)

    # --- CI: 16-24 char linkage key --------------------------------------------------
    pos = [f"{(i * 1000003) % 10**22:022d}" for i in range(30)]
    neg = [f"{(i * 1000003) % 10**10:010d}" for i in range(30)]  # too short for CI
    add("CI", "CI", pos, neg)

    # --- NAME -----------------------------------------------------------------------
    pos = [SURNAMES[i % 10] + GIVEN[(i * 3) % 10] for i in range(30)]
    neg = [f"B{chr(65 + i % 26)} Product Series" for i in range(30)]
    add("CUSTOMER_NAME", "NAME", pos, neg)

    # --- EMAIL ---------------------------------------------------------------------
    pos = [f"user{i}@company.example.com" for i in range(30)]
    neg = [f"user{i}company.example.com" for i in range(30)]
    add("EMAIL", "EMAIL", pos, neg)

    # --- PHONE_NUMBER ---------------------------------------------------------------
    pos = [f"010-{1000 + i:04d}-{1000 + i * 3:04d}" for i in range(30)]
    neg = [f"010-{100 + i:03d}" for i in range(30)]  # incomplete number
    add("PHONE", "PHONE_NUMBER", pos, neg)

    # --- FAX_NUMBER ---------------------------------------------------------------
    pos = [f"02-{1000 + i:04d}-{1000 + i * 3:04d}" for i in range(30)]
    neg = [f"02-{100 + i:03d}" for i in range(30)]
    add("FAX_NO", "FAX_NUMBER", pos, neg)

    # --- ADDRESS -------------------------------------------------------------------
    pos = [f"서울시 강남구 테헤란로 {i + 1}" for i in range(30)]
    neg = [f"REGION-{i + 1}" for i in range(30)]
    add("ADDRESS", "ADDRESS", pos, neg)

    # --- BIRTH_DATE ---------------------------------------------------------------
    pos = [f"{1970 + (i % 50):04d}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(30)]
    neg = [f"{1970 + (i % 50):04d}-{(i % 12) + 1:02d}-99" for i in range(30)]
    add("BIRTH_DATE", "BIRTH_DATE", pos, neg)

    # --- IMEI: 15 digits, Luhn valid ------------------------------------------------
    pos, neg = [], []
    for i in range(30):
        pos.append(luhn_complete(f"352031{(i * 137) % 100000000:08d}"))      # 14 + check = 15
        neg.append(f"352031{(i * 137) % 10000000000:010d}")                  # 16 digits
    add("IMEI_NO", "IMEI", pos, neg)

    # --- IMSI: 15 digits ----------------------------------------------------------
    pos = [f"310{(i * 137) % 1000000000:012d}"[:15] for i in range(30)]
    neg = [f"310{(i * 137) % 100000000:011d}"[:14] for i in range(30)]  # 14 digits
    add("IMSI_NO", "IMSI", pos, neg)

    # --- PERSONAL_ATTRIBUTE ---------------------------------------------------------
    pos = [f"{2015 + (i % 20):04d}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(30)]
    neg = [f"SKU-{(i * 7919) % 10**8:08d}" for i in range(30)]
    add("MARRIAGE_ANNIV", "PERSONAL_ATTRIBUTE", pos, neg)

    # --- ACCOUNT_GUID --------------------------------------------------------------
    pos = [f"1101{(i * 7919) % 10**28:028d}" for i in range(30)]
    neg = [f"SYS{(i * 7919) % 10**8:08d}" for i in range(30)]
    add("ACCOUNT_GUID", "ACCOUNT_GUID", pos, neg)

    # --- GAID ---------------------------------------------------------------
    pos = [f"gaid-{(i * 7919) % 10**16:016d}" for i in range(30)]
    neg = [f"{(i * 7919) % 10**8:08x}" for i in range(30)]
    add("GAID", "GAID", pos, neg)

    # --- SN -----------------------------------------------------------
    pos = [f"SN{(i * 7919) % 10**10:010d}" for i in range(30)]
    neg = [f"{(i * 7919) % 1000:03d}" for i in range(30)]
    add("DEVICE_SN", "SN", pos, neg)

    # --- UUID ----------------------------------------------------------
    pos = [f"{(i * 7919) % 10**8:08x}-1234-5678-abcd-{(i * 31) % 10**12:012x}" for i in range(30)]
    neg = [f"{(i * 7919) % 10**8:08x}12345678abcdef" for i in range(30)]
    add("UUID_VAL", "UUID", pos, neg)

    # --- MAC ----------------------------------------------------------
    pos = [f"{(i * 7) % 256:02x}:{(i * 11) % 256:02x}:{(i * 13) % 256:02x}:"
           f"{(i * 17) % 256:02x}:{(i * 19) % 256:02x}:{(i * 23) % 256:02x}" for i in range(30)]
    neg = [f"{(i * 7) % 256:02x}{(i * 11) % 256:02x}{(i * 13) % 256:02x}" for i in range(30)]
    add("MAC_ADDR", "MAC", pos, neg)

    # --- IP ----------------------------------------------------------
    pos = [f"192.168.{i % 256}.{(i * 7) % 256}" for i in range(30)]
    neg = [f"256.{i % 256}.{i % 256}.{i % 256}" for i in range(30)]
    add("CLIENT_IP", "IP", pos, neg)

    # --- POSTCODE ----------------------------------------------------
    pos = [f"{10000 + i * 37:05d}" for i in range(30)]
    neg = [f"{100000 + i * 37:06d}" for i in range(30)]
    add("POSTCODE", "POSTCODE", pos, neg)

    # --- PAYMENT_INFO --------------------------------------------------
    pos = [f"payment-{(i * 7919) % 10**12:012d}" for i in range(30)]
    neg = [f"invoice-{(i * 7919) % 10**8:08d}" for i in range(30)]
    add("PAYMENT_REF", "PAYMENT_INFO", pos, neg)

    # --- CUSTOM_IDENTIFIER -------------------------------------------
    pos = [f"member{(i * 7919) % 10**10:010d}" for i in range(30)]
    neg = [f"tmp{(i * 7919) % 1000:03d}" for i in range(30)]
    add("MEMBER_ID", "CUSTOM_IDENTIFIER", pos, neg)

    # --- MASKED: masked values must never be cleared as non-personal ----
    pos = [f"{1000 + i:04d}****{(i * 7) % 10000:04d}" for i in range(30)]
    neg = ["-" for _ in range(30)]
    add("MASKED_COL", "MASKED", pos, neg)

    return out
