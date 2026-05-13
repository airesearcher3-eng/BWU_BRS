"""Reference extraction and text-normalisation helpers."""

from __future__ import annotations

from datetime import date
import re
from typing import Iterable

from engine.normaliser import normalise_date


TN_NO_PATTERN = re.compile(
    r"Tn\.?\s*No\s*[:\s]\s*([A-Z0-9]+(?:/[A-Z0-9\-]+)*)",
    re.IGNORECASE,
)
TXN_NO_PATTERN = re.compile(
    r"Txn\s*No\s*[:\s]\s*([A-Z0-9]+(?:/[A-Z0-9\-]+)*)",
    re.IGNORECASE,
)
TXN_DATE_PATTERN = re.compile(
    r"(?:Tn\.?|Txn)\s*Dt\s*[:\s]\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})",
    re.IGNORECASE,
)
FD_BOOKING_PATTERN = re.compile(r"TRF TO FD no\.?\s*(\d+)", re.IGNORECASE)
FD_MATURITY_PATTERN = re.compile(r"(\d{12})\s+FD clos", re.IGNORECASE)
# Ujjivan FD patterns: "NEW FD BOOKING A/C 3314130340000045" and
# "Being New FD No 3314130340000045 booked"
FD_UJJIVAN_BOOKING_PATTERN = re.compile(
    r"(?:NEW FD BOOKING\s+A/C|New FD No)\s+(\d{10,})", re.IGNORECASE
)

GIB_KEYWORD_MAP = {
    "DTAX": ["TDS", "TCS"],
    "ESIC": ["ESIC", "E.S.I.C", "ESI"],
    "EPFO": ["EPF", "EPFO", "PF"],
    "GST": ["GST", "CGST", "SGST"],
}

# Pattern to detect IFSC codes (4 letters + 0 + 6 alphanumeric).
IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

# Pattern to extract BWU enrollment/registration IDs (e.g. MLM23003, BBT22035, BTE21024).
# Also matches BWU/XXX/YY/NNN pattern.
# The short-form uses a negative lookbehind to avoid matching inside IFSC codes
# (which are 4 letters + 0 + 6 alphanumeric, e.g. CNRB0004094).
ENROLLMENT_ID_PATTERN = re.compile(
    r"(?:BWU/[A-Z]+/\d{2}/\d{3}|(?<![A-Z])[A-Z]{3}[0-9]{4,6}(?![0-9]))",
    re.IGNORECASE,
)
# Pattern to extract MR (Money Receipt) numbers.
MR_NO_PATTERN = re.compile(r"BWU\d{4}/\d{4,6}", re.IGNORECASE)


# Noise tokens that appear in Tn.No fields but are not useful references.
_TN_NO_NOISE = frozenset({"UPI", "CR", "DR", "APPR", "CODE", "POS"})

# Broader pattern to capture the raw Tn.No / Txn No field value.
_TN_VALUE_BROAD = re.compile(
    r"(?:Tn\.?\s*No|Txn\s*No)\s*[:\s]\s*(.*?)(?:,\s*(?:Tn\.?\s*Dt|Txn|Remarks)|$)",
    re.IGNORECASE,
)

# Pattern to capture the Remarks field of MR narrations —
# often contains additional UPI UTR refs (space-separated 9-12 digit numbers).
_REMARKS_VALUE = re.compile(
    r"Remarks\s*:\s*(.*?)(?:,\s*(?:Rf\.?\s*No|POS\s*Tn)|$)",
    re.IGNORECASE,
)

# Pattern for free-form "Trxn Id ..." / "Trxn ID ..." references.
_TRXN_ID_PATTERN = re.compile(
    r"Trxn\s*Id\s*[:\s]*(\d{9,})",
    re.IGNORECASE,
)

def extract_tn_nos(text: str) -> list[str]:
    """Extract structured ``Tn. No`` / ``Txn No`` values from ledger text."""

    refs: list[str] = []
    seen: set[str] = set()

    for pattern in (TN_NO_PATTERN, TXN_NO_PATTERN):
        for match in pattern.finditer(text or ""):
            raw = match.group(1)
            for candidate in raw.split("/"):
                ref = candidate.strip().strip("-")
                if not ref or ref.upper().startswith("TRFR"):
                    continue
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)

    # Broader extraction for Ujjivan narrations where the Tn.No field
    # contains a leading slash, UPI/CR/ prefix, or APPR CODE format.
    for m in _TN_VALUE_BROAD.finditer(text or ""):
        value = m.group(1).strip()
        for num in re.findall(r"\d{9,}", value):
            if num not in seen:
                seen.add(num)
                refs.append(num)
        # Also extract comma-separated alphanumeric refs (e.g. "AXOMB3334984")
        # that aren't purely numeric and wouldn't be caught by the \d{9,} above.
        for part in value.split(","):
            part = part.strip().strip("-")
            if not part or part in seen:
                continue
            # Must be alphanumeric with at least 1 letter and 1 digit, 8+ chars.
            if (len(part) >= 8 and re.fullmatch(r"[A-Z0-9]+", part, re.IGNORECASE)
                    and re.search(r"[A-Z]", part, re.IGNORECASE) and re.search(r"\d", part)):
                if part not in seen:
                    seen.add(part)
                    refs.append(part)

    # Extract additional refs from the Remarks field of MR narrations.
    # MR entries often list additional UPI UTR numbers in the Remarks section
    # (e.g. "Remarks : 204260715796 15601540142 541194082022 ...").
    for m in _REMARKS_VALUE.finditer(text or ""):
        value = m.group(1).strip()
        for num in re.findall(r"\d{9,}", value):
            if num not in seen:
                seen.add(num)
                refs.append(num)

    # Extract refs from free-form "Trxn Id 563649393439" patterns.
    for m in _TRXN_ID_PATTERN.finditer(text or ""):
        num = m.group(1)
        if num not in seen:
            seen.add(num)
            refs.append(num)

    # Filter noise: direction indicators, non-ref tokens, and name-like tokens.
    refs = [
        r for r in refs
        if r.upper() not in _TN_NO_NOISE
        and not (r.isalpha() and len(r) < 8)  # Skip short names (TANUKA, MAFUJ)
    ]

    # Normalise: strip leading zeros from purely numeric refs.
    # HDFC book entries often pad UPI UTRs with leading zeros
    # (e.g. 0000602036485821 → 602036485821) which must match the
    # 12-digit UTR extracted from the statement UPI description.
    refs = [(r.lstrip("0") or r) if r.isdigit() else r for r in refs]

    return refs


def extract_transaction_date(text: str) -> date | None:
    """Extract the transaction date embedded in ledger narration when present."""

    match = TXN_DATE_PATTERN.search(text or "")
    return normalise_date(match.group(1)) if match else None


def extract_ref_from_description(description: str) -> list[str]:
    """Extract structural reference codes from a bank statement description.

    Supports ICICI (slash-separated), HDFC (hyphen-separated), and other formats.
    """

    desc = (description or "").strip()
    refs: list[str] = []

    patterns = (
        r"^UPI/(?:CR|DR)/([A-Z0-9]{6,})/",
        r"^UPI/([A-Z0-9]+)/",
        r"^RTGS-([A-Z0-9]+)-",
        r"^NEFT-RETURN-([A-Z0-9]{8,})-",
        r"^NEFT-([A-Z0-9]+)-",
        r"^INF/(?:NEFT|INFT)/([A-Z0-9]+)/",
        r"^MMT/IMPS/([0-9]+)/",
        r"^INF/INFT/([A-Z0-9]+)/",
        # Additional NEFT/INFT patterns
        r"^NEFT/([A-Z0-9]+)/",
        r"^NEFT Cr[- ]+([A-Z0-9]{8,})",
        r"^NEFT Dr[- ]+([A-Z0-9]{8,})",
        # HDFC RTGS CR format: "RTGS CR-IFSC-NAME-NAME-UTRREF"
        r"^RTGS CR-[A-Z0-9]+-.*-([A-Z0-9]{15,})$",
        # HDFC NEFT CR format: "NEFT CR-IFSC-NAME-REFCODE"
        r"^NEFT CR-([A-Z]{4}0[A-Z0-9]{6})-",
    )
    for pattern in patterns:
        match = re.match(pattern, desc, re.IGNORECASE)
        if match:
            refs.append(match.group(1))
            break

    # HDFC NEFT CR trailing ref: "NEFT CR-IFSC-NAME-...-REF"
    # The IFSC is already captured above; also grab the last segment when
    # it looks like an alphanumeric reference (8+ chars, not an IFSC).
    if desc.upper().startswith("NEFT CR-"):
        parts = desc.split("-")
        if len(parts) >= 4:
            trailing = parts[-1].strip()
            if (
                re.fullmatch(r"[A-Z0-9]{8,}", trailing, re.IGNORECASE)
                and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", trailing)
                and trailing not in refs
            ):
                refs.append(trailing)

    # NEFT-RETURN: extract the NEFT ref embedded after RETURN.
    if not refs and "RETURN" in desc.upper():
        m = re.search(r"NEFT-RETURN-([A-Z0-9]{8,})", desc, re.IGNORECASE)
        if m:
            refs.append(m.group(1))

    # Some UPI inward descriptions place the 12-digit reference later in the path.
    if not refs and desc.upper().startswith("UPI/"):
        for token in desc.split("/"):
            token = token.strip()
            if re.fullmatch(r"[0-9]{12}", token):
                refs.append(token)
                break

    # HDFC UPI format: "UPI-TXNID-VPA-UTR-DESC" (hyphen-separated).
    # The UTR is a numeric token (9-12 digits, sometimes zero-padded to 12)
    # that appears AFTER the VPA token (which contains '@').
    # Examples:
    #   UPI-110272501510-8695736978-1@NYES-000527553339-PAY  → UTR=527553339
    #   UPI-00000040030297338-8900655081@IBL-042566882263-...→ UTR=42566882263
    if not refs and desc.upper().startswith("UPI-"):
        tokens = desc.split("-")
        vpa_seen = False
        for token in tokens[1:]:  # skip "UPI"
            token = token.strip()
            if "@" in token:
                vpa_seen = True
                continue
            if vpa_seen and re.fullmatch(r"[0-9]{9,}", token):
                stripped = token.lstrip("0") or token
                refs.append(stripped)
                break

    # Fallback: find a NEFT reference code anywhere in the description.
    if not refs:
        match = re.search(r"NEFT[- ]+([A-Z0-9]{8,})", desc, re.IGNORECASE)
        if match:
            refs.append(match.group(1))

    return refs


def extract_fd_number(text: str) -> str | None:
    """Extract an FD number from a statement or ledger description."""

    for pattern in (FD_BOOKING_PATTERN, FD_MATURITY_PATTERN, FD_UJJIVAN_BOOKING_PATTERN):
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


def extract_gib_tax_type(description: str) -> str | None:
    """Return the GIB tax code token such as ``DTAX`` or ``ESIC``."""

    desc = (description or "").upper()
    if not desc.startswith("GIB/"):
        return None

    parts = desc.split("/")
    return parts[2].strip()[:4] if len(parts) > 2 else None


def normalise_text(text: str) -> str:
    """Upper-case text with punctuation collapsed to spaces for token matching."""

    return re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip()


def compact_text(text: str) -> str:
    """Upper-case text with all non-alphanumeric characters removed."""

    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())


def iter_significant_tokens(text: str, *, min_length: int = 5) -> Iterable[str]:
    """Yield de-duplicated significant tokens from free text."""

    seen: set[str] = set()
    for token in normalise_text(text).split():
        if len(token) < min_length or token in seen:
            continue
        seen.add(token)
        yield token


def is_fd_description(description: str) -> bool:
    """Return True if the description relates to a Fixed Deposit transaction.

    Uses word-boundary-aware matching to avoid false positives with strings
    like ``HDFC``, ``HDFCH00730672336``, etc.
    """
    desc_upper = (description or "").upper()
    return bool(
        re.search(r"\bTRF TO FD\b", desc_upper)
        or re.search(r"\bFD\s+(CLOS|MATURITY|NO|BOOKING)\b", desc_upper)
        or re.search(r"\bFD\s+no\b", desc_upper, re.IGNORECASE)
        or re.search(r"\bNEW FD BOOKING\b", desc_upper)
        or desc_upper.startswith("TRF TO FD")
    )


def is_neft_inft_description(description: str) -> bool:
    """Return True if the description is an NEFT/INFT/IMPS/RTGS transaction."""

    desc_upper = (description or "").upper().strip()
    return (
        desc_upper.startswith(("NEFT-", "NEFT/", "NEFT ", "INF/NEFT/", "INF/INFT/", "MMT/IMPS/", "RTGS-"))
        or "NEFT CR" in desc_upper
        or "NEFT DR" in desc_upper
    )


def _is_ifsc_code(text: str) -> bool:
    """Return True if the text looks like a bank IFSC code (e.g. SBIN0012364)."""
    return bool(IFSC_PATTERN.match(text.strip()))


def extract_enrollment_ids(text: str) -> list[str]:
    """Extract BWU enrollment/registration IDs from text.

    Matches patterns like MLM23003, BBT22035, BTE21024, BWU/BHM/23/008.
    """
    return [m.group(0).upper() for m in ENROLLMENT_ID_PATTERN.finditer(text or "")]


def extract_mr_numbers(text: str) -> list[str]:
    """Extract Money Receipt numbers like BWU2425/31788."""
    return [m.group(0).upper() for m in MR_NO_PATTERN.finditer(text or "")]


# Pattern to extract the NEFT payee from book narrations like
# "paid through Neft to Sariful Islam a/c" or "favour of Sabita Saha A/c".
_NEFT_PAYEE_NARRATION_RE = re.compile(
    r"(?:(?:Neft|NEFT)\s+(?:to|favour\s+of)|favour\s+of)\s+"
    r"([A-Z][A-Za-z\s.]+?)\s*(?:a/?c|account|$)",
    re.IGNORECASE,
)


def extract_neft_payee_from_narration(narration: str) -> str | None:
    """Extract the NEFT recipient name from a book narration.

    Handles patterns like 'paid through Neft to Sariful Islam a/c'
    and 'favour of Sabita Saha A/c'.
    """
    m = _NEFT_PAYEE_NARRATION_RE.search(narration or "")
    if m:
        name = m.group(1).strip().rstrip(".")
        if len(name) >= 3:
            return name
    return None


def extract_neft_payee(description: str) -> str | None:
    """Extract payee/beneficiary name from an NEFT/INFT description."""

    desc = (description or "").strip()
    desc_upper = desc.upper()

    # MMT/IMPS/REF/NAME/COMPACTNAME/IFSC
    if desc_upper.startswith("MMT/IMPS/"):
        parts = desc.split("/")
        for seg in parts[3:]:
            seg = seg.strip()
            if seg and not _is_ifsc_code(seg):
                return seg
        return None

    # RTGS-REF-PAYEE-ACCOUNT-IFSC
    if desc_upper.startswith("RTGS-"):
        parts = desc.split("-")
        if len(parts) >= 3:
            return parts[2].strip() or None
        return None

    # INF/NEFT/REF/IFSC/REF IDNAME/NAME or INF/INFT/REF/NAME/...
    if desc_upper.startswith(("INF/NEFT/", "INF/INFT/")):
        parts = desc.split("/")
        # Try the last non-empty segment first — it is often the clean name.
        for seg in reversed(parts[3:]):
            seg = seg.strip()
            if seg and not _is_ifsc_code(seg) and not seg.startswith("REF "):
                return seg
        # Fallback to position 3.
        if len(parts) > 3:
            return parts[3].strip()

    # NEFT-RETURN-REF-NAME-reason
    if desc_upper.startswith("NEFT-RETURN-"):
        parts = desc.split("-")
        if len(parts) >= 4:
            return parts[3].strip()

    # NEFT-REF-PAYEE-NAME
    if desc_upper.startswith("NEFT-"):
        parts = desc.split("-")
        if len(parts) >= 3:
            return parts[2].strip()

    # NEFT Cr REFCODE PAYEE NAME or NEFT Dr REFCODE PAYEE NAME
    m = re.match(r"NEFT\s+(?:Cr|Dr)\s+[A-Z0-9]+\s+(.+)", desc, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # NEFT/REF/PAYEE-NAME
    if desc_upper.startswith("NEFT/"):
        parts = desc.split("/")
        if len(parts) > 2:
            return parts[2].strip()

    return None


def extract_bill_aliases(description: str) -> list[str]:
    """Return normalised aliases used for BIL / NEFT / INF text matching."""

    desc = (description or "").upper()
    aliases: list[str] = []

    if desc.startswith(("BIL/ONL/", "BIL/INFT/")):
        parts = desc.split("/")
        payee = parts[3].strip() if len(parts) > 3 else desc
        aliases.append(payee)

        if "BHARTI AIR" in payee:
            aliases.extend(["AIRTEL", "AIRTEL SERVICE", "AIRTEL BROADBAND"])
        if "RELIANCE J" in payee:
            aliases.extend(["JIO", "JIO DIGITAL LIFE", "RELIANCE JIO"])
        if "PHARMACY C" in payee:
            aliases.extend(["PHARMACY COUNCIL OF INDIA", "PCI"])
        if "WEST BENGA" in payee:
            aliases.extend(["WEST BENGAL", "PANCHAYAT TAX"])
        if "COUNCIL FO" in payee:
            aliases.extend(
                [
                    "COUNCIL FOR THE INDIAN SCHOOL CERTIFICATE EXAMINATION",
                    "COUNCIL FOR THE INDIAN SCHOOL CERTIFICATE EXAMINATIONS",
                    "CISCE",
                ]
            )
        if "BHARAT SAN" in payee:
            aliases.extend(["BHARAT SANCHAR", "BSNL"])

    elif desc.startswith(("INF/NEFT/", "INF/INFT/")):
        parts = desc.split("/")
        # Filter out IFSC codes and "REF" prefixed tokens; keep payee names.
        for part in parts[3:]:
            token = part.strip()
            if not token or _is_ifsc_code(token):
                continue
            # Strip "REF " prefix then split embedded enrollment ID from name.
            if token.startswith("REF "):
                token = token[4:].strip()
            aliases.append(token)
        # Also add any enrollment IDs as aliases.
        aliases.extend(extract_enrollment_ids(desc))

    elif desc.startswith("MMT/IMPS/"):
        parts = desc.split("/")
        # MMT/IMPS/REF/NAME/COMPACTNAME/IFSC — extract payee names.
        for part in parts[3:]:
            token = part.strip()
            if not token or _is_ifsc_code(token):
                continue
            aliases.append(token)

    elif desc.startswith("RTGS-"):
        parts = desc.split("-")
        # RTGS-REF-PAYEE-ACCOUNT-IFSC — extract payee and account info.
        for part in parts[2:]:
            token = part.strip()
            if not token or _is_ifsc_code(token):
                continue
            aliases.append(token)

    elif desc.startswith("NEFT-RETURN-"):
        parts = desc.split("-")
        # NEFT-RETURN-REF-NAME-reason → extract name.
        if len(parts) >= 4:
            aliases.append(parts[3].strip())

    elif desc.startswith("NEFT-"):
        parts = desc.split("-")
        if len(parts) >= 3:
            aliases.append(parts[2].strip())
        if len(parts) >= 4:
            aliases.append(parts[3].strip())

    elif desc.startswith("NEFT/"):
        parts = desc.split("/")
        aliases.extend(
            [part.strip() for part in parts[2:5] if part.strip() and not _is_ifsc_code(part.strip())]
        )

    elif "NEFT CR" in desc or "NEFT DR" in desc:
        payee = extract_neft_payee(description)
        if payee:
            aliases.append(payee.upper())

    # HDFC RTGS CR format: "RTGS CR-IFSC-NAME-NAME-UTR"
    elif desc.startswith("RTGS CR-"):
        parts = desc.split("-")
        for part in parts[2:]:
            token = part.strip()
            if token and len(token) >= 5 and not token.isdigit() and not _is_ifsc_code(token):
                aliases.append(token)
                break

    # HDFC internal transfer: "BRAINWARE UNIV-BRAINWARE UNIVERSITY"
    elif desc.startswith("BRAINWARE UNIV-") or desc.startswith("FT-BRAINWARE"):
        aliases.append("BRAINWARE")
        aliases.append("BRAINWARE UNIVERSITY")

    # Salary entries: "SAL MMMYY NAME" → extract person name as alias.
    elif re.match(r"^SAL\s+[A-Z]{3}\d{2}\s+", desc):
        # Strip the "SAL MMMYY " prefix to get the person name.
        name_part = re.sub(r"^SAL\s+[A-Z]{3}\d{2}\s+", "", desc).strip()
        if name_part and len(name_part) >= 4:
            aliases.append(name_part)

    # Fallback: plain-text descriptions without any recognised prefix are
    # likely beneficiary names for direct debits (e.g. "ANKITA JANA").
    # Require at least one space (to avoid matching bare reference codes)
    # and a max length to stay safe.
    if not aliases:
        stripped = desc.strip()
        if (
            " " in stripped
            and 5 <= len(stripped) <= 50
            and not re.match(r"^[0-9]+$", stripped)
        ):
            aliases.append(stripped)

    return [alias for alias in aliases if len(alias.strip()) >= 4]
