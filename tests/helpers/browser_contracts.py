"""Small reusable browser and gate contract helpers."""

from tests.browser_helpers.native_keyboard import native_logical_key

def clean_browser_receipt_barrier(*, accepted=0):
    return {"epoch": "all", "accepted": accepted, "pending": 0, "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": True, "blocking": []}

def send_native_key(driver, character: str) -> None:
    native_logical_key(driver, character)
