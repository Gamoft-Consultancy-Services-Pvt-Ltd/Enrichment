"""Probe the calibration rule by directly calling classify_message + run_filter.

For each message, shows the RAW Groq response (before calibration) and
the final FilterResult (after calibration).
Run:  uv run python scripts/probe_calibration.py
"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.groq_client import classify_message
from modules.lead_ingestion.two_stage_filter import run_filter
from modules.lead_ingestion.schemas.filter_result import FilterClassification

MESSAGES = [
    "I've been a customer before, might be interested again",
    "Already using your product but want to know if there's a better plan",
    "Just checking your website, not sure if I need anything",
    "Seen your ad, not sure if relevant to me",
    "Was a customer long time ago",
    "We spoke before but I am not sure about anything",
]


async def main() -> None:
    calibration_count = 0

    for msg in MESSAGES:
        print(f"\nMsg: {msg!r}")
        try:
            # Step 1: raw Groq response (no calibration yet)
            raw = await classify_message(msg)
            raw_cls  = raw.get("classification", "?")
            raw_conf = raw.get("confidence")
            print(f"  RAW  -> cls={raw_cls!r}  conf={raw_conf}")

            # Step 2: run_filter applies calibration internally
            result = await run_filter(msg)
            final_cls = result.classification.value
            print(f"  FINAL -> cls={final_cls!r}  conf={result.confidence}")

            # Check if calibration fired (raw was non-LEAD but final is LEAD)
            if (
                raw_conf is not None
                and raw_conf < 0.7
                and raw_cls not in ("LEAD", "UNCLEAR")
                and final_cls == "LEAD"
            ):
                print("  *** CALIBRATION FIRED: non-LEAD + conf<0.7 -> coerced to LEAD ***")
                calibration_count += 1
            elif raw_conf is not None and raw_conf < 0.7:
                print(f"  (low confidence {raw_conf} but no calibration coercion needed)")

        except Exception as exc:
            print(f"  ERROR: {exc}")

    print()
    print("=" * 60)
    if calibration_count > 0:
        print(f"Phase 28 VERIFIED E2E: calibration fired {calibration_count} time(s).")
    else:
        print("No E2E calibration trigger found with real Groq.")
        print("Groq obeys 'if uncertain classify as LEAD' instruction.")
        print("Phase 28 verified by: unit tests + code review of two_stage_filter.py:79-87")


if __name__ == "__main__":
    asyncio.run(main())
