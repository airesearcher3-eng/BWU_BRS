"""Carry-forward resolution against the current month's counterpart transactions."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from engine.reference_extractor import (
    compact_text,
    extract_enrollment_ids,
    iter_significant_tokens,
)


def resolve_carry_forward_items(
    carry_items: list[dict[str, Any]],
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve historical BRS items against the appropriate current-side source.

    Both pending items (no clearing date) and resolved items (with clearing date)
    are matched, because manually-prepared BRS files often have clearing dates
    filled in retroactively.
    """

    resolved_matches: list[dict[str, Any]] = []

    # --- Pass 1: ref-based N:1 matching for add_bank_credit → book entries ---
    # When multiple carry-forward add_bank_credit items (UPI credits from prior
    # months) share refs with a single current-month book MR entry, and their
    # amounts sum to the book entry's amount, resolve both sides.
    # Run BEFORE individual 1:1 matching so that ref-linked groups are not
    # broken up by amount-only matching consuming individual CF items.
    ref_matches = _resolve_cf_ref_groups_to_book(carry_items, book_rows)
    resolved_matches.extend(ref_matches)

    # --- Pass 2: aggregate N:1 matching for resolved items ---
    # Group unmatched resolved items (cleared_on is set) by (section, cleared_on).
    # When multiple previous-BRS items were entered in the book as a single
    # combined journal entry, the sum of the group matches one counterpart row.
    # Run BEFORE individual 1:1 so that greedy individual matches don't break
    # apart groups that should resolve as a single aggregate.
    aggregate_matches = _resolve_aggregate_groups(
        carry_items, statement_rows, book_rows,
    )
    resolved_matches.extend(aggregate_matches)

    # --- Pass 3: individual 1:1 matching ---
    for item in carry_items:
        if item.get("matched"):
            continue
        counterpart_pool = _counterpart_pool(item, statement_rows, book_rows)
        match = _find_best_counterpart(item, counterpart_pool)
        if not match:
            continue

        match["matched"] = True
        match["match_state"] = "matched"
        item["matched"] = True
        resolved_matches.append(
            {
                "match_type": "carry_forward_resolution",
                "pass_number": 0,
                "statement_rows": [match["row_number"]] if match["kind"] == "statement" else [],
                "book_rows": [match["row_number"]] if match["kind"] == "book" else [],
                "carry_forward_rows": [item["row_number"]],
                "amount": item["amount"],
                "notes": f"Resolved historical {item['section']} item",
            }
        )

    # --- Pass 4: aggregate fallback for remaining items ---
    # Re-run aggregate for any items not yet matched (picks up groups that
    # only became eligible after individual matches consumed rival candidates).
    aggregate_matches_2 = _resolve_aggregate_groups(
        carry_items, statement_rows, book_rows,
    )
    resolved_matches.extend(aggregate_matches_2)

    # Items already resolved in the previous BRS (cleared_on set) that still
    # couldn't be matched individually or via aggregation should NOT be carried
    # forward — the previous BRS already closed them.  EXCEPTION: items whose
    # cleared_on falls within the current statement period (the clearing has not
    # been verified yet and should still appear in the BRS).
    stmt_dates = [r.get("value_date") for r in statement_rows if r.get("value_date")]
    period_start = min(stmt_dates) if stmt_dates else None

    pending = [
        item for item in carry_items
        if not item.get("matched") and (
            item.get("is_pending", True)
            or (item.get("cleared_on") and period_start
                and item["cleared_on"] >= period_start)
        )
    ]

    return {
        "resolved_matches": resolved_matches,
        "pending_items": pending,
    }


def _resolve_aggregate_groups(
    carry_items: list[dict[str, Any]],
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match groups of unmatched resolved carry-forward items to single counterparts.

    When the previous BRS has N items that were all cleared on the same date
    (e.g. entered in the book as a single combined journal entry), their
    individual amounts won't match the combined counterpart.  This function
    groups such items by (section, cleared_on), sums them, and looks for a
    single counterpart whose amount equals the group total.
    """
    matches: list[dict[str, Any]] = []

    # Only consider unmatched items that are already resolved in the prev BRS.
    unmatched_resolved = [
        item for item in carry_items
        if not item.get("matched") and item.get("cleared_on")
    ]
    if not unmatched_resolved:
        return matches

    # Group by (section, cleared_on).
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in unmatched_resolved:
        groups[(item["section"], item["cleared_on"])].append(item)

    for (section, cleared_on), group in groups.items():
        if len(group) < 2:
            continue  # Single items already handled by pass 1.

        group_total = sum(item["amount"] for item in group)
        # Collect text tokens from the group for validation.
        group_tokens = set()
        for item in group:
            group_tokens.update(
                compact_text(t) for t in iter_significant_tokens(item["remarks"])
            )

        # Build the counterpart pool using the first item's section.
        pool = _counterpart_pool(group[0], statement_rows, book_rows)

        # Check if only one counterpart has the exact aggregate amount.
        amount_matches = [c for c in pool if c["amount"] == group_total]
        unique_amount = len(amount_matches) == 1

        best_candidate = None
        best_score = 0
        for candidate in pool:
            if candidate["amount"] != group_total:
                continue
            # Validate via text overlap: the counterpart's narration should
            # contain at least one significant token from the group.
            cand_text = compact_text(" ".join(filter(None, [
                candidate.get("description"),
                candidate.get("narration"),
                candidate.get("particulars"),
            ])))
            token_hits = sum(1 for t in group_tokens if t and t in cand_text)
            # When only one candidate has the exact aggregate amount,
            # the match is unambiguous — allow even without text overlap.
            if token_hits == 0 and not unique_amount:
                continue
            # Prefer candidate whose date matches the cleared_on date.
            date_bonus = 0
            cand_date = candidate.get("value_date", candidate.get("voucher_date"))
            if cand_date == cleared_on:
                date_bonus = 5
            score = token_hits + date_bonus
            # Ensure unique-amount matches get at least score 1.
            if unique_amount and score == 0:
                score = 1
            if score > best_score:
                best_score = score
                best_candidate = candidate

        if best_candidate is None:
            continue

        # Mark all group items and the counterpart as matched.
        best_candidate["matched"] = True
        best_candidate["match_state"] = "matched"
        for item in group:
            item["matched"] = True

        kind = best_candidate.get("kind", "book")
        matches.append(
            {
                "match_type": "carry_forward_aggregate",
                "pass_number": 0,
                "statement_rows": [best_candidate["row_number"]] if kind == "statement" else [],
                "book_rows": [best_candidate["row_number"]] if kind == "book" else [],
                "carry_forward_rows": [item["row_number"] for item in group],
                "amount": group_total,
                "notes": (
                    f"Aggregate resolution: {len(group)} historical "
                    f"{section} items (cleared {cleared_on})"
                ),
            }
        )

    return matches


def _counterpart_pool(
    item: dict[str, Any],
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the current-month source that can clear this carry-forward item."""

    if item["section"] == "add_bank_credit":
        return [row for row in book_rows if not row["matched"] and row["direction"] == "IN"]
    if item["section"] == "add_cheque_issued":
        return [row for row in statement_rows if not row["matched"] and row["direction"] == "OUT"]
    if item["section"] == "less_cheque_deposit":
        return [row for row in statement_rows if not row["matched"] and row["direction"] == "IN"]
    return [row for row in book_rows if not row["matched"] and row["direction"] == "OUT"]


def _find_best_counterpart(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the strongest counterpart candidate using refs, enrollment IDs, text, and cleared date."""

    scored: list[tuple[int, dict[str, Any]]] = []
    remarks_compact = compact_text(item["remarks"])
    tokens = list(iter_significant_tokens(item["remarks"]))
    item_enrollment_ids = set(_normalise_enrollment_ids(extract_enrollment_ids(item["remarks"])))

    for candidate in candidates:
        if candidate["amount"] != item["amount"]:
            continue

        score = 0
        if item["refs"] and set(item["refs"]).intersection(candidate.get("refs", [])):
            score += 10

        candidate_raw_text = " ".join(
            filter(
                None,
                [
                    candidate.get("description"),
                    candidate.get("narration"),
                    candidate.get("particulars"),
                ],
            )
        )
        candidate_text = compact_text(candidate_raw_text)

        # --- Enrollment ID matching (strongest discriminator for student refunds) ---
        if item_enrollment_ids:
            cand_enrollment_ids = set(_normalise_enrollment_ids(
                extract_enrollment_ids(candidate_raw_text)
            ))
            if item_enrollment_ids & cand_enrollment_ids:
                score += 15

        if remarks_compact and remarks_compact[:12] and remarks_compact[:12] in candidate_text:
            score += 3
        if any(compact_text(token) in candidate_text for token in tokens):
            score += 2
        if item["cleared_on"] and candidate.get("value_date", candidate.get("voucher_date")) == item["cleared_on"]:
            score += 3

        if score:
            scored.append((score, candidate))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["row_number"]))
    if not scored:
        return None
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        return scored[0][1]
    return None


_ENROLLMENT_NORM_RE = re.compile(r"BWU/([A-Z]+)/(\d{2})/(\d+)", re.IGNORECASE)


def _normalise_enrollment_ids(ids: list[str]) -> list[str]:
    """Normalise enrollment IDs to short form (e.g. BWU/BBT/22/035 → BBT22035)."""
    result: list[str] = []
    for eid in ids:
        m = _ENROLLMENT_NORM_RE.fullmatch(eid)
        if m:
            result.append(f"{m.group(1)}{m.group(2)}{m.group(3)}".upper())
        else:
            result.append(eid.upper())
    return result


def _resolve_cf_ref_groups_to_book(
    carry_items: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match groups of CF add_bank_credit items to book entries by shared refs.

    Pattern: the bank credited multiple UPI/NEFT payments in prior months (each
    appears as a carry-forward add_bank_credit item with a UTR ref).  The book
    recorded a single MR entry in the current month listing those same UTRs.
    Grouping the CF items by ref overlap with the book entry and checking
    that their sum equals the book amount resolves both sides.
    """
    matches: list[dict[str, Any]] = []

    # Build a ref → CF item lookup for unmatched add_bank_credit items.
    cf_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in carry_items:
        if item.get("matched") or item["section"] != "add_bank_credit":
            continue
        for ref in item.get("refs", []):
            cf_by_ref[ref].append(item)

    if not cf_by_ref:
        return matches

    # For each unmatched book IN entry with refs, find matching CF items.
    for book_row in book_rows:
        if book_row["matched"] or book_row["direction"] != "IN":
            continue
        book_refs = book_row.get("refs", [])
        if not book_refs:
            continue

        # Collect CF items whose refs match any of this book entry's refs.
        matched_cf: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for ref in book_refs:
            for cf_item in cf_by_ref.get(ref, []):
                if cf_item.get("matched"):
                    continue
                cf_id = id(cf_item)
                if cf_id not in seen_ids:
                    seen_ids.add(cf_id)
                    matched_cf.append(cf_item)

        if len(matched_cf) < 1:
            continue

        cf_total = sum(item["amount"] for item in matched_cf)
        if cf_total != book_row["amount"]:
            continue

        # Mark all CF items and the book entry as resolved.
        book_row["matched"] = True
        book_row["match_state"] = "matched"
        for item in matched_cf:
            item["matched"] = True

        matches.append(
            {
                "match_type": "cf_ref_group_to_book",
                "pass_number": 0,
                "statement_rows": [],
                "book_rows": [book_row["row_number"]],
                "carry_forward_rows": [item["row_number"] for item in matched_cf],
                "amount": cf_total,
                "notes": (
                    f"Resolved {len(matched_cf)} CF add_bank_credit items "
                    f"against book entry via shared refs"
                ),
            }
        )

    # --- Reverse pattern: 1 CF item → N book entries ---
    # A single CF add_bank_credit item (e.g. 6,000 UPI credit) can correspond
    # to multiple book MR entries that reference the same UTR (e.g. 2,000 + 4,000).
    book_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for book_row in book_rows:
        if book_row["matched"] or book_row["direction"] != "IN":
            continue
        for ref in book_row.get("refs", []):
            book_by_ref[ref].append(book_row)

    for item in carry_items:
        if item.get("matched") or item["section"] != "add_bank_credit":
            continue
        item_refs = item.get("refs", [])
        if not item_refs:
            continue

        matched_books: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for ref in item_refs:
            for book_row in book_by_ref.get(ref, []):
                if book_row["matched"]:
                    continue
                bid = id(book_row)
                if bid not in seen_ids:
                    seen_ids.add(bid)
                    matched_books.append(book_row)

        if len(matched_books) < 2:
            continue

        book_total = sum(b["amount"] for b in matched_books)
        if book_total != item["amount"]:
            continue

        item["matched"] = True
        for b in matched_books:
            b["matched"] = True
            b["match_state"] = "matched"

        matches.append(
            {
                "match_type": "cf_ref_to_book_group",
                "pass_number": 0,
                "statement_rows": [],
                "book_rows": [b["row_number"] for b in matched_books],
                "carry_forward_rows": [item["row_number"]],
                "amount": item["amount"],
                "notes": (
                    f"Resolved 1 CF add_bank_credit item against "
                    f"{len(matched_books)} book entries via shared refs"
                ),
            }
        )

    return matches
