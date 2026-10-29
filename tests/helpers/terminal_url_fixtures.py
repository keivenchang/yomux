# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Seeded URL corpus shared by real-client and terminal soft-wrap journeys."""

import random
import string


def terminal_url_matrix_examples():
    rng = random.Random(20261026)
    bases = [
        'https://developer.mozilla.org/en-US/docs/Web/URI',
        'https://www.python.org/doc/',
        'https://www.rfc-editor.org/rfc/rfc3986',
        'https://github.com/keivenchang/yomux',
        'https://en.wikipedia.org/wiki/Uniform_Resource_Identifier',
    ]
    examples = [bases[0]]
    alphabet = string.ascii_letters + string.digits + '-._~'
    for index in range(1, 100):
        length = [64, 96, 128, 192, 256, 384, 512, 768, 1024, 1400][index % 10]
        length += rng.randrange(-8, 9)
        base = bases[index % len(bases)] + f'?case={index}&encoded=%2F%20%28x%29&value='
        tail = ''.join(rng.choice(alphabet) for _ in range(max(1, length - len(base) - 9)))
        examples.append(base + tail + '#end-done')
    return examples
