# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native Chrome key events with logical keys independent of the host layout."""

_MODIFIERS = {'alt': 1, 'ctrl': 2, 'meta': 4, 'shift': 8}
_SPECIAL_KEYS = {
    'Enter': ('Enter', 13),
    'Home': ('Home', 36),
    'End': ('End', 35),
    'ArrowUp': ('ArrowUp', 38),
    'ArrowRight': ('ArrowRight', 39),
    '\\': ('Backslash', 220),
    '[': ('BracketLeft', 219),
    ']': ('BracketRight', 221),
}


def native_editor_modifier(browser):
    """Editor keymaps use the browser OS even when app chrome is overridden."""
    return 'meta' if browser.capabilities['platformName'].lower() in {'mac', 'macos', 'darwin'} else 'ctrl'


def native_logical_key(browser, key, modifiers=(), *, code=None, commands=()):
    """Send trusted key events without translating their letter through the OS layout."""
    if key in _SPECIAL_KEYS:
        default_code, virtual_key = _SPECIAL_KEYS[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        default_code = ('Digit' if key.isdigit() else 'Key') + key.upper()
        virtual_key = ord(key.upper())
    elif len(key) == 1 and key.isascii() and key.isprintable():
        default_code, virtual_key = ('Space', 32) if key == ' ' else ('', 0)
    else:
        raise ValueError(f'Unsupported native logical key: {key!r}')
    modifier_bits = sum(_MODIFIERS[value] for value in set(modifiers))
    event = {'key': key, 'code': code or default_code, 'modifiers': modifier_bits,
             'windowsVirtualKeyCode': virtual_key}
    down = {'type': 'keyDown', **event}
    if commands:
        down['commands'] = list(commands)
    if not modifier_bits and len(key) == 1:
        down['text'] = key
    # Chromium on macOS needs an explicit native editing command for select-all.
    if key.lower() == 'a' and set(modifiers) == {native_editor_modifier(browser)}:
        down['commands'] = ['selectAll']
    try:
        browser.execute_cdp_cmd('Input.dispatchKeyEvent', down)
    finally:
        browser.execute_cdp_cmd('Input.dispatchKeyEvent', {'type': 'keyUp', **event})


def native_logical_text(browser, text):
    """Type each character through trusted keyboard events with explicit text."""
    for character in text:
        native_logical_key(browser, 'Enter' if character == '\n' else character)


def native_document_start(browser):
    """Use the OS document-start chord, preserving the editor's native key handling."""
    if native_editor_modifier(browser) == 'meta':
        native_logical_key(browser, 'ArrowUp', ['meta'], commands=['moveToBeginningOfDocument'])
    else:
        native_logical_key(browser, 'Home', ['ctrl'])


def native_document_end(browser):
    """Use the OS document-end chord, preserving the editor's native key handling."""
    if native_editor_modifier(browser) == 'meta':
        native_logical_key(browser, 'End', ['meta'], commands=['moveToEndOfDocument'])
    else:
        native_logical_key(browser, 'End', ['ctrl'])
