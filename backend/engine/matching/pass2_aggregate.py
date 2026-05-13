"""Pass 2: aggregate reference and text-based amount grouping."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from engine.matching.utils import (
    alias_score,
    iter_amount_matching_subsets,
    mark_match,
    mark_partial_match,
    sum_amounts,
)
from engine.matching.pass1_exact import _IFSC_RE
from engine.reference_extractor import compact_text, is_fd_description


def run_pass2(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Handle one-to-many, many-to-one, and text-driven amount group matches."""

    matches: list[dict[str, Any]] = []

    # One statement reference -> many ledger rows with the same reference.
    for statement_row in [row for row in statement_rows if not row["matched"] and row.get("refs")]:
        for ref in statement_row["refs"]:
            if _IFSC_RE.match(ref):
                continue
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and ref in row.get("refs", [])
            ]
            for subset in iter_amount_matching_subsets(candidates, statement_row["amount"]):
                matches.append(
                    mark_match(
                        "one_to_many_ref",
                        [statement_row],
                        subset,
                        pass_number=2,
                        notes=f"Aggregated ledger refs for {ref}",
                    )
                )
                break
            if statement_row["matched"]:
                break

            # Partial match fallback: ref-linked book rows cover part of the
            # statement amount.  Match what we can and leave the remainder.
            if candidates:
                candidate_total = sum_amounts(candidates)
                if Decimal("0") < candidate_total < statement_row["amount"]:
                    matches.append(
                        mark_partial_match(
                            "one_to_many_ref_partial",
                            statement_row,
                            candidates,
                            pass_number=2,
                            notes=f"Partial ref match for {ref}: {candidate_total} of {statement_row['amount']} matched",
                        )
                    )
                    break

    # One book entry with multiple refs → many statement entries.
    # Handles HDFC-style MR receipts where one book entry lists N Tn.Nos and
    # each maps to a separate UPI entry on the statement side.
    for book_row in [row for row in book_rows if not row["matched"] and len(row.get("refs", [])) > 1]:
        # Find statement entries whose ref matches any of this book row's refs,
        # including prefix matching for truncated refs (min 10 chars).
        book_refs = book_row["refs"]
        candidates = []
        for row in statement_rows:
            if row["matched"] or row["direction"] != book_row["direction"] or not row.get("refs"):
                continue
            # Exact intersection first.
            if set(row["refs"]).intersection(book_refs):
                candidates.append(row)
                continue
            # Prefix match: book ref may be truncated (e.g. AXOMB3334984
            # matches stmt ref AXOMB33349840012).
            for s_ref in row["refs"]:
                if len(s_ref) < 10:
                    continue
                for b_ref in book_refs:
                    if len(b_ref) < 10:
                        continue
                    if s_ref.startswith(b_ref) or b_ref.startswith(s_ref):
                        candidates.append(row)
                        break
                else:
                    continue
                break
        if not candidates:
            continue

        # Try exact sum first.
        if sum_amounts(candidates) == book_row["amount"]:
            matches.append(
                mark_match(
                    "many_stmt_to_one_book_ref",
                    candidates,
                    [book_row],
                    pass_number=2,
                    notes=f"Matched {len(candidates)} statement entries to 1 book entry via shared refs",
                )
            )
            continue

        # Try subsets if some refs matched entries with wrong amounts.
        for subset in iter_amount_matching_subsets(candidates, book_row["amount"]):
            matches.append(
                mark_match(
                    "many_stmt_to_one_book_ref",
                    subset,
                    [book_row],
                    pass_number=2,
                    notes=f"Matched {len(subset)}/{len(candidates)} statement entries to 1 book entry via refs",
                )
            )
            break

    # Don Bosco-style narration substring fallback where the bank ref appears in free text.
    for book_row in [row for row in book_rows if not row["matched"]]:
        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == book_row["direction"]
            and any(
                ref in f"{book_row['narration']} {book_row['particulars']}"
                for ref in row.get("refs", [])
                if not _IFSC_RE.match(ref)
            )
        ]
        if not candidates:
            continue

        for subset in [[row] for row in candidates] + list(
            iter_amount_matching_subsets(candidates, book_row["amount"])
        ):
            if sum_amounts(subset) != book_row["amount"]:
                continue
            matches.append(
                mark_match(
                    "narration_ref_group",
                    subset,
                    [book_row],
                    pass_number=2,
                    notes="Narration free-text contained the bank statement reference",
                )
            )
            break

    # Reverse ref-group: multiple book entries whose narration contains a
    # statement ref, aggregating to one statement entry.  Handles MR receipts
    # where the book records two or more MRs sharing the same NEFT Tn.No.
    for stmt_row in [row for row in statement_rows if not row["matched"]]:
        stmt_refs = stmt_row.get("refs", [])
        if not stmt_refs:
            continue
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == stmt_row["direction"]
            and any(
                ref in f"{row.get('narration', '')} {row.get('particulars', '')}"
                for ref in stmt_refs
            )
        ]
        if len(candidates) < 2:
            continue
        for subset in iter_amount_matching_subsets(
            candidates, stmt_row["amount"], max_size=min(len(candidates), 6)
        ):
            matches.append(
                mark_match(
                    "ref_many_book_to_one_stmt",
                    [stmt_row],
                    subset,
                    pass_number=2,
                    notes=f"Matched {len(subset)} book entries to 1 statement entry via shared ref",
                )
            )
            break

    # Text exact matches for INF/NEFT and similar outward rows without structured refs in the book.
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and row["amount"] == statement_row["amount"]
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 5
            and alias_score(statement_row["description"], row) > 0
        ]
        if len(candidates) != 1:
            continue

        matches.append(
            mark_match(
                "text_exact",
                [statement_row],
                [candidates[0]],
                pass_number=2,
                notes="Unique amount/date/text match",
            )
        )

    # Group multiple statement rows into one book entry by name fragments.
    # First, try itemized payment matching for narrations listing individual amounts.
    _DBG = True  # TODO: remove debug flag
    for book_row in [row for row in book_rows if not row["matched"]]:
        parsed = _parse_itemized_amounts(book_row.get("narration", ""))
        if len(parsed) < 2:
            continue

        if _DBG and book_row["amount"] in (Decimal("78500"), Decimal("113000"), Decimal("57500")):
            import sys
            print(f"  [DBG] itemized {book_row['amount']}: {len(parsed)} parsed, matched={book_row['matched']}", file=sys.stderr, flush=True)

        # Match each (name, amount) pair to a statement entry by checking if
        # the parsed name appears inside the statement description.
        matched_stmts: list[dict[str, Any]] = []
        used: set[int] = set()
        unmatched_names: list[tuple[str, Decimal]] = []
        for name, amt in parsed:
            name_compact = compact_text(name)
            best = None
            for s_row in statement_rows:
                if s_row["matched"] or id(s_row) in used:
                    continue
                if s_row["direction"] != book_row["direction"]:
                    continue
                if s_row["amount"] != amt:
                    continue
                if abs(_row_date(book_row).toordinal() - _row_date(s_row).toordinal()) > 7:
                    continue
                s_desc_compact = compact_text(s_row.get("description", ""))
                if name_compact and (
                    name_compact in s_desc_compact
                    or _fuzzy_name_match(name_compact, s_desc_compact)
                ):
                    best = s_row
                    break
            if best:
                matched_stmts.append(best)
                used.add(id(best))
            else:
                unmatched_names.append((name, amt))

        matched_sum = sum_amounts(matched_stmts)

        if _DBG and book_row["amount"] in (Decimal("78500"), Decimal("113000"), Decimal("57500")):
            import sys
            print(f"    [DBG] {len(matched_stmts)}/{len(parsed)} matched, sum={matched_sum}, gap={book_row['amount']-matched_sum}, unmatched={unmatched_names}", file=sys.stderr, flush=True)

        if len(matched_stmts) >= 2 and matched_sum == book_row["amount"]:
            matches.append(
                mark_match(
                    "itemized_group",
                    matched_stmts,
                    [book_row],
                    pass_number=2,
                    notes=f"Matched {len(matched_stmts)} itemized payments from narration",
                )
            )
            continue

        # Reversal netting: when some parsed items had their NEFT payment reversed
        # (incorrect account, etc.), the statement debit+return cancel out, leaving
        # a gap equal to the reversal amount.  Net the gap against the reversal
        # credit entry in the book.
        if len(matched_stmts) >= 2 and matched_sum < book_row["amount"]:
            gap = book_row["amount"] - matched_sum
            if _DBG and book_row["amount"] in (Decimal("78500"), Decimal("113000")):
                import sys
                print(f"    [DBG] Reversal netting: gap={gap}, looking for reversals", file=sys.stderr, flush=True)
            opposite = "IN" if book_row["direction"] == "OUT" else "OUT"
            reversal_rows: list[dict[str, Any]] = []
            reversal_total = Decimal("0")
            for bk in book_rows:
                if bk["matched"] or bk["direction"] != opposite:
                    continue
                narr_upper = (bk.get("narration", "") or "").upper()
                if not any(kw in narr_upper for kw in ("REVERSED", "REVERSAL", "INCORRECT")):
                    continue
                # Check that the reversal mentions one of our unmatched names.
                narr_compact = compact_text(narr_upper)
                for uname, uamt in unmatched_names:
                    if compact_text(uname) in narr_compact and bk["amount"] == uamt:
                        reversal_rows.append(bk)
                        reversal_total += bk["amount"]
                        break
            if _DBG and book_row["amount"] in (Decimal("78500"), Decimal("113000")):
                import sys
                print(f"    [DBG] Found {len(reversal_rows)} reversals, total={reversal_total}, gap={gap}, match={reversal_total == gap}", file=sys.stderr, flush=True)
            if reversal_total == gap:
                matches.append(
                    mark_match(
                        "itemized_reversal_net",
                        matched_stmts,
                        [book_row] + reversal_rows,
                        pass_number=2,
                        notes=f"Matched {len(matched_stmts)} items + netted {len(reversal_rows)} reversal(s)",
                    )
                )
                continue
            continue

    # Uniform-amount batch: narration lists N names (no individual amounts) and
    # the book amount equals N × some unit amount.  Match N statement entries
    # all with the same unit amount, direction, date window, and name overlap.
    for book_row in [row for row in book_rows if not row["matched"]]:
        narr = book_row.get("narration", "") or ""
        # Quick filter: must list names (commas) and be a payment narration.
        if narr.count(",") < 3:
            continue
        # Already handled by itemized_group if amounts are present.
        if _ITEMIZED_RS_PATTERN.search(narr):
            continue

        # Extract names: split by commas and common separators.
        stripped = _NAME_STRIP_RE.sub("", narr).strip()
        # Remove trailing "towards..." clause.
        stripped = re.split(r"\btowards\b", stripped, maxsplit=1, flags=re.IGNORECASE)[0]
        raw_names = re.split(r"[,&]|\band\b", stripped, flags=re.IGNORECASE)
        name_list = []
        for rn in raw_names:
            rn = rn.strip().rstrip(".").strip()
            if len(rn) >= 4 and rn[0].isalpha():
                name_list.append(compact_text(rn))
        if len(name_list) < 3:
            continue

        n = len(name_list)
        # Check if book amount divides evenly by N.
        unit_amount, remainder = divmod(book_row["amount"], n)
        if remainder != 0 or unit_amount <= 0:
            continue

        # Find statement entries matching (direction, amount, date, name).
        matched_stmts_u: list[dict[str, Any]] = []
        used_u: set[int] = set()
        for nc in name_list:
            for s_row in statement_rows:
                if s_row["matched"] or id(s_row) in used_u:
                    continue
                if s_row["direction"] != book_row["direction"]:
                    continue
                if s_row["amount"] != unit_amount:
                    continue
                if abs(_row_date(book_row).toordinal() - _row_date(s_row).toordinal()) > 7:
                    continue
                s_compact = compact_text(s_row.get("description", ""))
                if nc in s_compact:
                    matched_stmts_u.append(s_row)
                    used_u.add(id(s_row))
                    break

        if len(matched_stmts_u) == n and sum_amounts(matched_stmts_u) == book_row["amount"]:
            matches.append(
                mark_match(
                    "uniform_batch",
                    matched_stmts_u,
                    [book_row],
                    pass_number=2,
                    notes=f"Matched {n} uniform-amount ({unit_amount}) payments",
                )
            )
            continue

    # Then try generic text_group matching.
    for book_row in [row for row in book_rows if not row["matched"]]:
        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == book_row["direction"]
            and abs(_row_date(book_row).toordinal() - _row_date(row).toordinal()) <= 5
            and alias_score(row["description"], book_row) > 0
        ]
        if not candidates:
            continue
        for subset in iter_amount_matching_subsets(candidates, book_row["amount"], max_size=8):
            matches.append(
                mark_match(
                    "text_group",
                    subset,
                    [book_row],
                    pass_number=2,
                    notes="Multiple bank rows aggregated into one book entry",
                )
            )
            break

    # Salary aggregation: one bulk salary book entry matched to many SAL
    # statement entries.  Runs after text_exact (individual name matches)
    # so that professional-fee entries are already paired.
    _salary_aggregation(statement_rows, book_rows, matches)

    # Settlement aggregation: multiple book IN entries (income) from one day
    # sum to a single statement IN entry (payment aggregator settlement) on
    # the next business day.  Handles PhonePe, Paytm, etc.
    _settlement_aggregation(statement_rows, book_rows, matches)

    # Text-aware many-to-one: multiple book rows whose narration mentions the
    # same vendor/payee name sum to one statement row.  Handles consolidated
    # payments where the bank debits one lump sum but the book records each
    # invoice separately (e.g. FIREMAX, SCIENTIFIC HOUSE).
    _text_many_to_one(statement_rows, book_rows, matches)

    # General many-to-one: multiple book rows sum to one statement row on the
    # same date.  Handles inter-company transfers (BWU NEFT) and similar.
    _general_many_to_one(statement_rows, book_rows, matches)

    # Batch pairing: when N statement and N book rows share the same amount,
    # direction, date window, and alias match, pair them 1-to-1.
    _batch_pair_identical_amounts(statement_rows, book_rows, matches)

    # Safe fallback: unique exact amount and close date when nothing else fits.
    # Portal-settled book entries (narration contains "Portal Settled on") are
    # reserved for the portal settlement rule in pass3 — skip them here.
    _PORTAL_SETTLED_RE = re.compile(r"Portal\s+Settled\s+on", re.IGNORECASE)
    # Portal settlement statement entries should also not be used in this
    # generic fallback — they are matched explicitly in pass3.
    _PORTAL_STMT_PREFIXES = (
        "76017672TERMINAL",
        "UPI SETTLEMENT -I78231",
        "PAYU PAYMENTS",
        "99857247TERMINAL",
        "99863812TERMINAL",
    )
    for tolerance in (0, 1, 2, 3, 5):
        for statement_row in [row for row in statement_rows if not row["matched"]]:
            if is_fd_description(statement_row["description"]):
                continue
            # Skip portal settlement statement entries — handled in pass3.
            if any(statement_row["description"].upper().startswith(p.upper())
                   or p.upper() in statement_row["description"].upper()
                   for p in _PORTAL_STMT_PREFIXES):
                continue
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= tolerance
                # Skip portal-settled book entries — reserved for pass3.
                and not _PORTAL_SETTLED_RE.search(row.get("narration", "") or "")
            ]
            if len(candidates) != 1:
                continue

            matches.append(
                mark_match(
                    f"amount_date_{tolerance}",
                    [statement_row],
                    [candidates[0]],
                    pass_number=2,
                    notes="Unique amount/date fallback",
                )
            )

    return matches


def _batch_pair_identical_amounts(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    """Pair N statement rows with N book rows sharing the same amount, direction, and text."""

    from collections import defaultdict

    # Group unmatched statement rows by (direction, amount).
    stmt_groups: dict[tuple, list] = defaultdict(list)
    for row in statement_rows:
        if not row["matched"]:
            stmt_groups[(row["direction"], row["amount"])].append(row)

    for (direction, amount), stmt_group in stmt_groups.items():
        if len(stmt_group) < 2:
            continue

        book_candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == direction
            and row["amount"] == amount
        ]
        if len(book_candidates) != len(stmt_group):
            continue

        # Verify text affinity: each statement row must score > 0 against at
        # least one book candidate and vice versa.
        all_linked = True
        for s_row in stmt_group:
            if not any(alias_score(s_row["description"], b) > 0 for b in book_candidates):
                all_linked = False
                break
        if not all_linked:
            continue

        # Pair by closest date first.
        paired_book: set[int] = set()
        for s_row in sorted(stmt_group, key=lambda r: _row_date(r)):
            best = None
            best_dist = 999
            for b_row in book_candidates:
                if id(b_row) in paired_book:
                    continue
                dist = abs(_row_date(s_row).toordinal() - _row_date(b_row).toordinal())
                if dist < best_dist:
                    best_dist = dist
                    best = b_row
            if best is None or best_dist > 5:
                all_linked = False
                break
            paired_book.add(id(best))
            matches.append(
                mark_match(
                    "batch_pair",
                    [s_row],
                    [best],
                    pass_number=2,
                    notes="Batch-paired identical amount entries",
                )
            )


def _settlement_aggregation(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    """Match daily income book entries to next-day settlement NEFT entries.

    Payment aggregators (PhonePe, Paytm, etc.) collect digital payments during
    the day and settle them as a single NEFT the next business day.  The book
    records individual income entries per counter/canteen.  This function
    processes settlement NEFTs in chronological order, maintaining a rolling
    pool of unmatched book IN entries and using subset-sum (meet-in-middle)
    to find the right combination.

    "Collection deposit" entries are excluded because they are aggregate
    entries that duplicate individual canteen/food court entries.
    """
    from collections import defaultdict
    from datetime import timedelta

    _SETTLEMENT_KEYWORDS = ("PHONEPE", "PAYTM", "RAZORPAY", "PAYMENT AGGREGATOR", "PINE LABS")

    settlement_stmts = sorted(
        [
            row for row in statement_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and any(kw in row["description"].upper() for kw in _SETTLEMENT_KEYWORDS)
            # Skip individual UPI entries with UTR refs UNLESS they are
            # settlement NEFTs (identified by the NEFT prefix in description).
            and (not row.get("refs") or "NEFT" in row["description"].upper())
        ],
        key=lambda r: (_row_date(r), -r["amount"]),
    )
    if not settlement_stmts:
        return

    # Build date-keyed index of book IN entries, excluding "Collection deposit"
    # aggregates which duplicate individual canteen/food court entries, and
    # excluding BWU / inter-company entries that match via many_to_one_amount.
    # Use word-boundary matching for BWU to avoid excluding Ujjivan's MR numbers
    # (e.g. BWU2526/44695) while still excluding standalone "BWU" entity references.
    _EXCLUDE_SETTLEMENT_PLAIN = ("COLLECTION DEPOSIT", "BRAINWARE", "HOSTEL",
                                 "FOOD COUPON")
    _EXCLUDE_SETTLEMENT_REGEX = re.compile(r"\bBWU\b")
    book_by_date: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in book_rows:
        if not row["matched"] and row["direction"] == "IN":
            desc = (row.get("narration") or row.get("particulars") or "").upper()
            if any(kw in desc for kw in _EXCLUDE_SETTLEMENT_PLAIN):
                continue
            if _EXCLUDE_SETTLEMENT_REGEX.search(desc):
                continue
            book_by_date[_row_date(row)].append(row)

    # Rolling pool: entries from earlier dates that haven't been matched yet
    # carry over so that spill-over settlements across days are handled.
    pool: list[dict[str, Any]] = []
    pool_dates_added: set = set()

    # Pattern to identify POS/card-based entries for Pine Labs filtering.
    _POS_INDICATOR = re.compile(r"APPR|POS\b", re.IGNORECASE)

    # -- Pine Labs combined-day pre-pass: group Pine Labs NEFTs by date --
    # When multiple Pine Labs NEFTs on the same day (e.g., NODAL + ESCROW)
    # have a combined total matching the previous day's POS entries, match
    # them as a group to avoid splitting issues.
    pine_labs_stmts = [
        r for r in settlement_stmts
        if "PINE LABS" in r["description"].upper()
    ]
    if pine_labs_stmts:
        from collections import defaultdict as _dd
        pine_by_date: dict[Any, list] = _dd(list)
        for r in pine_labs_stmts:
            pine_by_date[_row_date(r)].append(r)

        for pine_date, pine_group in sorted(pine_by_date.items()):
            if all(r["matched"] for r in pine_group):
                continue
            unmatched_pine = [r for r in pine_group if not r["matched"]]
            combined_target = sum_amounts(unmatched_pine)

            # Try matching combined target against previous day's POS entries.
            for offset in range(1, 6):
                bdate = pine_date - timedelta(days=offset)
                day_entries = [r for r in book_by_date.get(bdate, []) if not r["matched"]]
                pos_day = [
                    r for r in day_entries
                    if _POS_INDICATOR.search(r.get("narration") or r.get("particulars") or "")
                ]
                if not pos_day:
                    continue
                if sum_amounts(pos_day) == combined_target:
                    # Match all Pine Labs NEFTs to ALL POS entries from that day.
                    matches.append(
                        mark_match(
                            "settlement_aggregation",
                            unmatched_pine,
                            pos_day,
                            pass_number=2,
                            notes=f"Combined Pine Labs: {len(unmatched_pine)} NEFTs ← {len(pos_day)} POS from {bdate}",
                        )
                    )
                    break
                # Try subset-sum on single-day POS entries.
                if len(pos_day) <= 40:
                    subset = _meet_in_middle_subset_sum(pos_day, combined_target)
                    if subset:
                        matches.append(
                            mark_match(
                                "settlement_aggregation",
                                unmatched_pine,
                                subset,
                                pass_number=2,
                                notes=f"Combined Pine Labs: {len(unmatched_pine)} NEFTs ← {len(subset)} POS from {bdate}",
                            )
                        )
                        break

    for s_row in settlement_stmts:
        if s_row["matched"]:
            continue
        s_date = _row_date(s_row)
        target = s_row["amount"]
        is_pine_labs = "PINE LABS" in s_row["description"].upper()

        # Add book entries from dates not yet added to the pool.
        # Look back up to 10 days to cover weekends/holidays and carry-forward.
        for offset in range(0, 11):
            bdate = s_date - timedelta(days=offset)
            if bdate not in pool_dates_added:
                pool.extend(book_by_date.get(bdate, []))
                pool_dates_added.add(bdate)

        # Filter to only unmatched entries still in the pool.
        live = [r for r in pool if not r["matched"]]
        if not live:
            continue

        # For Pine Labs, filter to POS/card entries only (identified by
        # APPR CODE or POS PayMode in narration).  Pine Labs only settles
        # POS/card transactions, while UPI transactions go directly.
        if is_pine_labs:
            pos_live = [
                r for r in live
                if _POS_INDICATOR.search(r.get("narration") or r.get("particulars") or "")
            ]
        else:
            pos_live = None  # Use full pool

        # Quick path: try all entries from a single preceding date.
        for offset in range(1, 6):
            bdate = s_date - timedelta(days=offset)
            day_entries = [r for r in book_by_date.get(bdate, []) if not r["matched"]]
            if is_pine_labs:
                day_entries = [
                    r for r in day_entries
                    if _POS_INDICATOR.search(r.get("narration") or r.get("particulars") or "")
                ]
            if day_entries and sum_amounts(day_entries) == target:
                matches.append(
                    mark_match(
                        "settlement_aggregation",
                        [s_row],
                        day_entries,
                        pass_number=2,
                        notes=f"Settlement: {len(day_entries)} from {bdate} → {s_date}",
                    )
                )
                break
            # Quick cross-day: try combining a preceding day with the
            # settlement's own date or the next day (handles "evening" entries
            # recorded on D+1 that are part of the D settlement).
            if day_entries and not is_pine_labs:
                for fwd in (0, 1):
                    fwd_date = s_date + timedelta(days=fwd)
                    if fwd_date == bdate:
                        continue
                    fwd_entries = [r for r in book_by_date.get(fwd_date, []) if not r["matched"]]
                    if not fwd_entries:
                        continue
                    combined = day_entries + fwd_entries
                    if len(combined) > 40:
                        continue
                    if sum_amounts(combined) == target:
                        matches.append(
                            mark_match(
                                "settlement_aggregation",
                                [s_row],
                                combined,
                                pass_number=2,
                                notes=f"Settlement: {len(combined)} from {bdate}+{fwd_date} → {s_date}",
                            )
                        )
                        break
                    subset = _meet_in_middle_subset_sum(combined, target)
                    if subset:
                        matches.append(
                            mark_match(
                                "settlement_aggregation",
                                [s_row],
                                subset,
                                pass_number=2,
                                notes=f"Settlement: {len(subset)}/{len(combined)} from {bdate}+{fwd_date} → {s_date}",
                            )
                        )
                        break
                if s_row["matched"]:
                    break
            # For Pine Labs, try subset-sum on single-day POS entries.
            if is_pine_labs and day_entries and len(day_entries) <= 40:
                subset = _meet_in_middle_subset_sum(day_entries, target)
                if subset:
                    matches.append(
                        mark_match(
                            "settlement_aggregation",
                            [s_row],
                            subset,
                            pass_number=2,
                            notes=f"Settlement: {len(subset)}/{len(day_entries)} POS from {bdate} → {s_date}",
                        )
                    )
                    break
        if s_row["matched"]:
            continue

        # For Pine Labs, try POS-filtered subset-sum first.
        if pos_live:
            if sum_amounts(pos_live) == target:
                matches.append(
                    mark_match(
                        "settlement_aggregation",
                        [s_row],
                        pos_live,
                        pass_number=2,
                        notes=f"Settlement: all {len(pos_live)} POS entries → {s_date}",
                    )
                )
                continue

            subset = _meet_in_middle_subset_sum(pos_live, target)
            if subset:
                matches.append(
                    mark_match(
                        "settlement_aggregation",
                        [s_row],
                        subset,
                        pass_number=2,
                        notes=f"Settlement: {len(subset)}/{len(pos_live)} POS → {s_date}",
                    )
                )
                continue

        # Full pool check: try all live entries.
        if sum_amounts(live) == target:
            matches.append(
                mark_match(
                    "settlement_aggregation",
                    [s_row],
                    live,
                    pass_number=2,
                    notes=f"Settlement: all {len(live)} pooled entries → {s_date}",
                )
            )
            continue

        # Subset-sum via meet-in-middle on the entire live pool.
        subset = _meet_in_middle_subset_sum(live, target)
        if subset:
            matches.append(
                mark_match(
                    "settlement_aggregation",
                    [s_row],
                    subset,
                    pass_number=2,
                    notes=f"Settlement: {len(subset)}/{len(live)} pooled → {s_date}",
                )
            )
            continue

    # Second pass: tolerance matching for any remaining unmatched settlements.
    # Handles small discrepancies (e.g. ₹1 PhonePe cancellation adjustments).
    for s_row in settlement_stmts:
        if s_row["matched"]:
            continue
        s_date = _row_date(s_row)
        target = s_row["amount"]
        for offset in range(1, 6):
            bdate = s_date - timedelta(days=offset)
            day_entries = [r for r in book_by_date.get(bdate, []) if not r["matched"]]
            if not day_entries:
                continue
            day_total = sum_amounts(day_entries)
            diff = abs(day_total - target)
            if Decimal(0) < diff <= Decimal(5):
                matches.append(
                    mark_match(
                        "settlement_aggregation",
                        [s_row],
                        day_entries,
                        pass_number=2,
                        notes=f"Settlement (±{diff}): {len(day_entries)} from {bdate} → {s_date}",
                    )
                )
                break


def _meet_in_middle_subset_sum(
    entries: list[dict[str, Any]], target: Decimal,
) -> list[dict[str, Any]] | None:
    """Find a non-empty subset of *entries* whose amounts sum to *target*.

    Uses the meet-in-middle algorithm: split the entries into two halves,
    enumerate all 2^(n/2) subset sums for each half, then pair complementary
    sums.  Efficient for n up to ~40; we cap at 30 for safety.
    """
    n = len(entries)
    if n == 0 or n > 40:
        return None

    amounts = [e["amount"] for e in entries]
    half = n // 2

    # ---- left half: indices 0..half-1 ----
    left_sums: dict[Decimal, int] = {}          # sum -> bitmask of indices
    for mask in range(1 << half):
        s = sum(amounts[i] for i in range(half) if mask & (1 << i))
        if s <= target:
            left_sums.setdefault(s, mask)
    left_sums.setdefault(Decimal(0), 0)         # empty subset

    # ---- right half: indices half..n-1 ----
    right_len = n - half
    for mask in range(1 << right_len):
        s = sum(amounts[half + j] for j in range(right_len) if mask & (1 << j))
        complement = target - s
        if complement in left_sums:
            left_mask = left_sums[complement]
            # Ensure the combined subset is non-empty.
            if left_mask or mask:
                indices = []
                for i in range(half):
                    if left_mask & (1 << i):
                        indices.append(i)
                for j in range(right_len):
                    if mask & (1 << j):
                        indices.append(half + j)
                return [entries[i] for i in indices]

    return None


_SALARY_BOOK_RE = re.compile(r"\bsalar(?:y|ies)\b", re.IGNORECASE)
_SAL_STMT_RE = re.compile(r"^SAL\s+[A-Z]{3}\d{2}\s+", re.IGNORECASE)


def _salary_aggregation(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    """Match a bulk salary book entry to many SAL statement entries.

    The book records one lump-sum salary payment (e.g. "salary for October
    2024") while the bank statement has hundreds of individual "SAL OCT24
    <NAME>" entries.  After individual name-based matching (text_exact)
    pairs some SAL entries to professional-fee book entries, the remaining
    SAL entries should aggregate to the bulk salary amount.
    """
    from itertools import combinations

    for book_row in book_rows:
        if book_row["matched"]:
            continue
        narr = (book_row.get("narration", "") or "") + " " + (book_row.get("particulars", "") or "")
        if not _SALARY_BOOK_RE.search(narr):
            continue

        sal_candidates = [
            r for r in statement_rows
            if not r["matched"]
            and r["direction"] == book_row["direction"]
            and _SAL_STMT_RE.match(r.get("description", "") or "")
        ]
        if len(sal_candidates) < 2:
            continue

        total = sum_amounts(sal_candidates)
        if total == book_row["amount"]:
            matches.append(
                mark_match(
                    "salary_aggregation",
                    sal_candidates,
                    [book_row],
                    pass_number=2,
                    notes=f"Matched {len(sal_candidates)} SAL entries to bulk salary ({book_row['amount']})",
                )
            )
            continue

        # Overshoot: a few SAL entries were individually matched but their
        # book counterpart was NOT a salary entry.  Exclude N entries so
        # the remainder sums correctly.  Only try small exclusion sets.
        if total > book_row["amount"]:
            overshoot = total - book_row["amount"]
            # Size-1 exclusion: single SAL entry equals the overshoot.
            found = False
            for r in sal_candidates:
                if r["amount"] == overshoot:
                    exclude = {id(r)}
                    to_match = [c for c in sal_candidates if id(c) not in exclude]
                    matches.append(
                        mark_match(
                            "salary_aggregation",
                            to_match,
                            [book_row],
                            pass_number=2,
                            notes=f"Matched {len(to_match)} SAL entries to bulk salary, 1 excluded",
                        )
                    )
                    found = True
                    break
            if found:
                continue

            # Size-2 exclusion via hash set.
            amt_index: dict[Decimal, list[dict]] = {}
            for r in sal_candidates:
                amt_index.setdefault(r["amount"], []).append(r)
            for r in sal_candidates:
                complement = overshoot - r["amount"]
                if complement <= 0:
                    continue
                pool = amt_index.get(complement, [])
                partner = next((p for p in pool if id(p) != id(r)), None)
                if partner:
                    exclude = {id(r), id(partner)}
                    to_match = [c for c in sal_candidates if id(c) not in exclude]
                    matches.append(
                        mark_match(
                            "salary_aggregation",
                            to_match,
                            [book_row],
                            pass_number=2,
                            notes=f"Matched {len(to_match)} SAL entries to bulk salary, 2 excluded",
                        )
                    )
                    found = True
                    break
            if found:
                continue


def _text_many_to_one(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    """Match multiple book rows to one statement row using text/alias affinity.

    For each unmatched statement entry, find unmatched book entries whose
    narration mentions the same vendor (alias_score > 0), then try subsets
    that sum to the statement amount.  Handles consolidated bank debits where
    the book records each invoice separately (e.g. FIREMAX, SCIENTIFIC HOUSE).
    """
    from itertools import combinations

    for s_row in statement_rows:
        if s_row["matched"]:
            continue
        s_date = _row_date(s_row)

        # Find book candidates by text affinity + direction + date window.
        candidates = [
            r for r in book_rows
            if not r["matched"]
            and r["direction"] == s_row["direction"]
            and abs(_row_date(r).toordinal() - s_date.toordinal()) <= 3
            and alias_score(s_row["description"], r) > 0
        ]
        if len(candidates) < 2 or len(candidates) > 15:
            continue

        # Check if all candidates sum exactly.
        if sum_amounts(candidates) == s_row["amount"]:
            matches.append(
                mark_match(
                    "text_many_to_one",
                    [s_row],
                    candidates,
                    pass_number=2,
                    notes=f"Text-matched {len(candidates)} book entries to 1 statement entry",
                )
            )
            continue

        # Try subsets size 2..8.
        for size in range(2, min(9, len(candidates) + 1)):
            found = False
            for subset in combinations(candidates, size):
                if sum_amounts(list(subset)) == s_row["amount"]:
                    matches.append(
                        mark_match(
                            "text_many_to_one",
                            [s_row],
                            list(subset),
                            pass_number=2,
                            notes=f"Text-matched {size} of {len(candidates)} book entries to 1 statement entry",
                        )
                    )
                    found = True
                    break
            if found:
                break


def _general_many_to_one(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    """Match multiple book rows to a single statement row when their amounts sum.

    Handles inter-company transfers and similar cases where 2-5 book entries
    on the same date sum to one statement entry on the same date (+/- 1 day).
    """
    from itertools import combinations

    for s_row in statement_rows:
        if s_row["matched"]:
            continue
        s_date = _row_date(s_row)
        target = s_row["amount"]

        candidates = [
            r for r in book_rows
            if not r["matched"]
            and r["direction"] == s_row["direction"]
            and abs(_row_date(r).toordinal() - s_date.toordinal()) <= 1
        ]
        if len(candidates) < 2 or len(candidates) > 15:
            continue

        # Try subsets size 2..8.
        for size in range(2, min(9, len(candidates) + 1)):
            found = False
            for subset in combinations(candidates, size):
                if sum_amounts(list(subset)) == target:
                    matches.append(
                        mark_match(
                            "many_to_one_amount",
                            [s_row],
                            list(subset),
                            pass_number=2,
                            notes=f"Aggregated {size} book entries to match statement {target}",
                        )
                    )
                    found = True
                    break
            if found:
                break


def _row_date(row: dict[str, Any]):
    """Return the canonical transaction date field for mixed row types."""

    return row.get("value_date") or row.get("voucher_date")


# Pattern to extract "NAME Rs.AMOUNT/-" entries where Rs prefix provides strong evidence.
_ITEMIZED_RS_PATTERN = re.compile(
    r"([A-Z][A-Z\s.(),'&]+?)\s*(?:Rs\.?[\s-]*|INR\s*|₹\s*)"
    r"([0-9,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# Pattern to extract "NAME AMOUNT/-" entries where /- suffix provides evidence.
_ITEMIZED_SLASH_PATTERN = re.compile(
    r"([A-Z][A-Z\s.()]+?)[\s-]+"
    r"([0-9,]+(?:\.\d{1,2})?)\s*/-",
    re.IGNORECASE,
)
# Alternative pattern: "NAME (Rs AMOUNT)"
_ITEMIZED_PAREN_PATTERN = re.compile(
    r"([A-Z][A-Za-z\s.]+?)\s*\(\s*(?:Rs\.?[\s-]*|INR\s*|₹\s*)"
    r"([0-9,]+(?:\.\d{1,2})?)\s*\)",
    re.IGNORECASE,
)

# Prefixes to strip from parsed names.
_NAME_STRIP_RE = re.compile(
    r"^(?:Being\s+(?:the\s+)?amount\s+paid\s+to|paid\s+to|amount\s+paid\s+to|and)\s+",
    re.IGNORECASE,
)


def _parse_itemized_amounts(narration: str) -> list[tuple[str, Decimal]]:
    """Extract (name, amount) pairs from a narration that lists individual payments."""

    results: list[tuple[str, Decimal]] = []
    seen_amounts: set[str] = set()

    for pattern in (_ITEMIZED_RS_PATTERN, _ITEMIZED_SLASH_PATTERN, _ITEMIZED_PAREN_PATTERN):
        for match in pattern.finditer(narration or ""):
            name = match.group(1).strip().rstrip(",").rstrip("&").strip()
            name = _NAME_STRIP_RE.sub("", name).strip()
            # Strip trailing "Rs." or "Rs" that may be captured as part of the name.
            name = re.sub(r"\s*Rs\.?\s*$", "", name, flags=re.IGNORECASE).strip()
            amount_str = match.group(2).replace(",", "")
            try:
                amount = Decimal(amount_str)
            except Exception:
                continue
            if amount <= 0 or len(name) < 3:
                continue
            key = f"{compact_text(name)}_{amount}"
            if key not in seen_amounts:
                seen_amounts.add(key)
                results.append((name, amount))

    return results


def _fuzzy_name_match(name_compact: str, desc_compact: str) -> bool:
    """Fuzzy name match to handle common Bengali name spelling variations.

    Handles cases like ASHISHRISHIDAS vs ASHISRISHIDAS, BINOYDAS vs BINAYDAS,
    NURJAHANBIBI vs NOORJAHANBIBI, etc.
    """
    if len(name_compact) < 4 or len(desc_compact) < len(name_compact):
        return False

    # Use longest common subsequence ratio as a robust fuzzy measure.
    # Check every window of desc_compact that could contain the name.
    name_len = len(name_compact)
    # Allow the window to be slightly smaller or larger than name to handle
    # insertions/deletions.
    for extra in range(0, 4):
        win_size = name_len + extra
        if win_size > len(desc_compact):
            break
        for start in range(len(desc_compact) - win_size + 1):
            window = desc_compact[start:start + win_size]
            lcs = _lcs_length(name_compact, window)
            if lcs >= name_len - max(1, name_len // 6):
                return True
        # Also try shorter windows (deletions in description).
        if extra > 0:
            win_size = name_len - extra
            if win_size < 4:
                break
            for start in range(len(desc_compact) - win_size + 1):
                window = desc_compact[start:start + win_size]
                lcs = _lcs_length(name_compact, window)
                if lcs >= name_len - max(1, name_len // 6):
                    return True

    return False


def _lcs_length(a: str, b: str) -> int:
    """Length of the longest common subsequence between two strings."""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    # Space-optimised DP.
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]
