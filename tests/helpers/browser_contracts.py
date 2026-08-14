"""Small reusable browser and gate contract helpers."""

def clean_browser_receipt_barrier(*, accepted=0):
    return {"epoch": "all", "accepted": accepted, "pending": 0, "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": True, "blocking": []}

def send_native_key(driver, character: str) -> None:
    key_code = ord(character.upper())
    common = {"key": character, "code": f"Key{character.upper()}", "windowsVirtualKeyCode": key_code, "nativeVirtualKeyCode": key_code}
    driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "text": character, "unmodifiedText": character, **common})
    driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", **common})
