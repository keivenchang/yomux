import json
from pathlib import Path
from types import MappingProxyType

import pytest

from yolomux_lib.diagnostic_redaction import redact_diagnostic_value

SHARED_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "diagnostic_redaction.json").read_text(encoding="utf-8")
)
SHARED_FIXTURE_CASES = SHARED_FIXTURE["cases"]

# The exact credential fragments the shared fixture inputs embed. The negative "zero secrets" proof
# below asserts none survive Python redaction; the JavaScript half asserts the same fragments against
# the browser redactor (tests/diagnostic_redaction.test.js), so both conformance implementations of
# the one neutral contract are held to the identical evidence.
SHARED_FIXTURE_SECRET_FRAGMENTS = (
    "browser-secret", "server-secret", "csrf-secret", "proxy-secret", "proxy-user", "digest-secret",
    "first-secret", "second-secret", "s-secret", "url-secret", "fragment-secret",
    "unterminated-secret", "x-api-secret", "token-secret", "basic-secret", "deep-secret", "deep-token",
    "a-secret", "b-secret", "matrix-secret", "utf8-secret", "utf8-token-secret", "AbC-123_xyz",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (
            "Bearer browser-secret password=server-secret",
            "Bearer [redacted-secret] password=[redacted-secret]",
        ),
        (
            "authorization: Basic-auth-value; passwd='db-secret'; api_key=key-secret",
            "authorization: [redacted-secret]",
        ),
        (
            'request failed with {"password":"json-secret","token":"json-token"}',
            'request failed with {"password":"[redacted-secret]","token":"[redacted-secret]"}',
        ),
        (
            'request failed with {"Authorization":"Basic dXNlcjpwYXNz"}',
            'request failed with {"Authorization":"[redacted-secret]"}',
        ),
        (
            'request failed with {"Authorization":"Bearer browser-secret"}',
            'request failed with {"Authorization":"[redacted-secret]"}',
        ),
        (
            'request failed with {"Cookie":"sid=browser-secret; csrf=csrf-secret"}',
            'request failed with {"Cookie":"[redacted-secret]"}',
        ),
        (
            "https://example.test/api?password=url-secret&mode=debug#token=fragment-secret",
            "https://example.test/api?password=[redacted-secret]&mode=debug#token=[redacted-share-token]",
        ),
        (
            "Authorization: Basic browser-secret Cookie: session=browser-cookie; csrf=csrf-secret",
            "Authorization: [redacted-secret]",
        ),
    ),
)
def test_diagnostic_free_text_retains_the_error_while_redacting_credentials(raw, expected):
    assert redact_diagnostic_value(raw) == expected


def test_diagnostic_free_text_does_not_hide_noncredential_context():
    raw = "request failed because password validation rejected the ordinary input; mode=debug"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (
            "upstream Authorization: Basic abc failed at /api/ping after 503",
            "upstream Authorization: [redacted-secret] failed at /api/ping after 503",
        ),
        (
            "request Cookie: sid=abc failed after reconnect",
            "request Cookie: [redacted-secret] failed after reconnect",
        ),
    ),
)
def test_diagnostic_headers_preserve_trailing_failure_context(raw, expected):
    assert redact_diagnostic_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            'request Cookie: sid="browser-secret"; csrf=csrf-secret failed after reconnect',
            "request Cookie: [redacted-secret] failed after reconnect",
            ("browser-secret", "csrf-secret"),
        ),
        (
            "request Cookie: sid = 'owner\"s-secret' ; csrf = \"second's-secret\" failed at /api/ping",
            "request Cookie: [redacted-secret] failed at /api/ping",
            ("owner", "second"),
        ),
        (
            r'request Cookie: sid="first\"second" failed after reconnect',
            "request Cookie: [redacted-secret] failed after reconnect",
            ("first", "second"),
        ),
        (
            r"request Set-Cookie: sid='first\'second'; csrf=csrf-secret failed at /api/ping",
            "request Set-Cookie: [redacted-secret] failed at /api/ping",
            ("first", "second", "csrf-secret"),
        ),
    ),
)
def test_diagnostic_cookie_headers_parse_complete_credential_pairs(raw, expected, secret_fragments):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            'password="owner\'s-secret" failed at /api/ping',
            'password="[redacted-secret]" failed at /api/ping',
            ("owner", "s-secret"),
        ),
        (
            "password='owner\"s-secret' failed after reconnect",
            "password='[redacted-secret]' failed after reconnect",
            ("owner", "s-secret"),
        ),
        (
            r'{"password":"first\"second"}',
            '{"password":"[redacted-secret]"}',
            ("first", "second"),
        ),
        (
            r"{'password':'first\'second'}",
            "{'password':'[redacted-secret]'}",
            ("first", "second"),
        ),
    ),
)
def test_diagnostic_quoted_assignments_follow_the_selected_delimiter(raw, expected, secret_fragments):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize("quote", ('"', "'"))
@pytest.mark.parametrize("ending", ("unterminated-secret failed at /api/ping", "escaped-secret\\"))
def test_diagnostic_unterminated_assignments_fail_closed_at_the_line_boundary(quote, ending):
    raw = f"safe prefix password={quote}{ending}\nnext safe line"

    redacted = redact_diagnostic_value(raw)

    assert redacted == "safe prefix password=[redacted-secret]\nnext safe line"
    assert "unterminated-secret" not in redacted
    assert "escaped-secret" not in redacted


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("quote", ('"', "'"))
def test_diagnostic_unterminated_cookie_values_fail_closed_at_the_line_boundary(header, quote):
    raw = f"safe prefix {header}: sid={quote}cookie-secret failed at /api/ping\nnext safe line"

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe prefix {header}: [redacted-secret]\nnext safe line"
    assert "cookie-secret" not in redacted


@pytest.mark.parametrize("credential_key", ("password", "secret", "token", "api_key", "Authorization"))
@pytest.mark.parametrize(
    ("quoted_value", "expected_suffix"),
    (
        ("matrix-secret", " failed at /api/ping"),
        ('"matrix-secret"', " failed at /api/ping"),
        ("'matrix-secret'", " failed at /api/ping"),
        ('"matrix-secret failed at /api/ping', ""),
        ("'matrix-secret failed at /api/ping", ""),
    ),
)
def test_diagnostic_assignment_matrix_redacts_each_quote_and_termination_mode(
    credential_key,
    quoted_value,
    expected_suffix,
):
    raw = f"{credential_key}={quoted_value} failed at /api/ping"

    redacted = redact_diagnostic_value(raw)

    terminated_quote = (
        quoted_value[0]
        if len(quoted_value) >= 2 and quoted_value[0] in {'"', "'"} and quoted_value[-1] == quoted_value[0]
        else ""
    )
    marker = "[redacted-secret]"
    expected_value = f"{terminated_quote}{marker}{terminated_quote}"
    if credential_key == "Authorization" and not quoted_value.startswith(('"', "'")):
        expected_suffix = ""
    assert redacted == f"{credential_key}={expected_value}{expected_suffix}"
    assert "matrix-secret" not in redacted


@pytest.mark.parametrize("pair_count", (1, 2, 3))
@pytest.mark.parametrize("quote", ("", '"', "'"))
def test_diagnostic_cookie_matrix_redacts_every_pair_without_hiding_trailing_context(pair_count, quote):
    pairs = []
    fragments = []
    for index in range(pair_count):
        fragment = f"cookie-secret-{index}"
        fragments.append(fragment)
        pairs.append(f"key{index}={quote}{fragment}{quote}")
    raw = f"Cookie: {'; '.join(pairs)} failed after reconnect"

    redacted = redact_diagnostic_value(raw)

    assert redacted == "Cookie: [redacted-secret] failed after reconnect"
    assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (
            "request Cookie:sid=browser-secret failed after reconnect",
            "request Cookie:[redacted-secret] failed after reconnect",
        ),
        (
            "request Cookie :  sid=browser-secret; csrf=csrf-secret failed at /api/ping",
            "request Cookie :  [redacted-secret] failed at /api/ping",
        ),
        (
            "request Set-Cookie\t:\tsid=browser-secret failed after reconnect",
            "request Set-Cookie\t:\t[redacted-secret] failed after reconnect",
        ),
    ),
)
def test_diagnostic_cookie_headers_preserve_separator_spacing_and_trailing_context(raw, expected):
    assert redact_diagnostic_value(raw) == expected


@pytest.mark.parametrize(
    "raw",
    (
        "Cookie:[redacted-secret] failed after reconnect",
        "Cookie :  [redacted-secret] failed at /api/ping",
        "Set-Cookie\t:\t[redacted-secret] failed after reconnect",
    ),
)
def test_diagnostic_redacted_cookie_headers_do_not_reenter_the_malformed_fallback(raw):
    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
def test_diagnostic_cookie_headers_fail_closed_on_unparsed_semicolon_segments(header):
    raw = f"safe {header}: sid=first-secret; malformed; csrf=second-secret failed\nnext safe line"

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe {header}: [redacted-secret]\nnext safe line"
    assert "first-secret" not in redacted
    assert "second-secret" not in redacted


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("pair_count", (1, 2, 3))
@pytest.mark.parametrize("quote", ("", '"', "'"))
def test_diagnostic_malformed_cookie_segment_matrix_fails_closed_through_the_line(header, pair_count, quote):
    for malformed_position in range(pair_count + 1):
        fragments = [f"matrix-secret-{index}" for index in range(pair_count)]
        segments = [f"key{index}={quote}{fragment}{quote}" for index, fragment in enumerate(fragments)]
        segments.insert(malformed_position, "bare-invalid-segment")
        raw = f"safe {header}: {'; '.join(segments)} failed at /api/ping\nnext safe line"

        redacted = redact_diagnostic_value(raw)

        assert redacted == f"safe {header}: [redacted-secret]\nnext safe line"
        assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize("header", ("Cookie", "cookie", "Set-Cookie", "SET-cookie"))
@pytest.mark.parametrize("missing_separator", (" ", "\t", " \t "))
@pytest.mark.parametrize("quote", ("", '"', "'"))
def test_diagnostic_cookie_headers_fail_closed_when_pair_separator_is_missing(header, missing_separator, quote):
    raw = (
        f"safe {header}: sid={quote}first-secret{quote}{missing_separator}"
        f"csrf={quote}second-secret{quote} failed after reconnect\nnext safe line"
    )

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe {header}: [redacted-secret]\nnext safe line"
    assert "first-secret" not in redacted
    assert "second-secret" not in redacted


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("later_pair_count", (2, 3))
@pytest.mark.parametrize("quote", ("", '"', "'"))
def test_diagnostic_missing_cookie_separator_matrix_removes_every_later_pair(
    header,
    later_pair_count,
    quote,
):
    fragments = [f"matrix-secret-{index}" for index in range(later_pair_count + 1)]
    first_pair = f"key0={quote}{fragments[0]}{quote}"
    later_pairs = "; ".join(
        f"key{index}={quote}{fragments[index]}{quote}" for index in range(1, later_pair_count + 1)
    )
    raw = f"safe {header}: {first_pair}\t {later_pairs} failed at /api/ping\nnext safe line"

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe {header}: [redacted-secret]\nnext safe line"
    assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("trailing", (" failed after reconnect", "\tfailed at /api/ping", " ordinary prose"))
def test_diagnostic_cookie_header_trailing_prose_without_a_pair_stays_visible(header, trailing):
    raw = f"safe {header}: sid=browser-secret{trailing}"

    assert redact_diagnostic_value(raw) == f"safe {header}: [redacted-secret]{trailing}"


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
def test_diagnostic_trailing_cookie_semicolon_fails_closed_at_the_line_boundary(header):
    raw = f"safe {header}: sid=browser-secret;\nnext safe line"

    assert redact_diagnostic_value(raw) == f"safe {header}: [redacted-secret]\nnext safe line"


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("missing_separator", (" ", "\t", " / "))
def test_diagnostic_cookie_header_declines_when_a_later_pair_follows_invalid_prose(header, missing_separator):
    raw = (
        f"safe {header}: sid=first-secret{missing_separator}malformed{missing_separator}"
        "csrf=second-secret failed\nnext safe line"
    )

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe {header}: [redacted-secret]\nnext safe line"
    assert "first-secret" not in redacted
    assert "second-secret" not in redacted


@pytest.mark.parametrize("header", ("Cookie", "cookie", "Set-Cookie", "SET-cookie"))
@pytest.mark.parametrize("missing_separator", (" ", "\t", " / "))
@pytest.mark.parametrize("quote", ("", '"', "'"))
@pytest.mark.parametrize("invalid_token_count", (1, 2))
@pytest.mark.parametrize("later_pair_count", (1, 2, 3))
@pytest.mark.parametrize("line_boundary", ("", "\nnext safe line"))
def test_diagnostic_later_cookie_pair_matrix_fails_closed_through_the_current_line(
    header,
    missing_separator,
    quote,
    invalid_token_count,
    later_pair_count,
    line_boundary,
):
    fragments = [f"matrix-secret-{index}" for index in range(later_pair_count + 1)]
    first_pair = f"key0={quote}{fragments[0]}{quote}"
    invalid = missing_separator.join(f"invalid-{index}" for index in range(invalid_token_count))
    later_pairs = "; ".join(
        f"key{index}={quote}{fragments[index]}{quote}" for index in range(1, later_pair_count + 1)
    )
    raw = f"safe {header}: {first_pair}{missing_separator}{invalid}{missing_separator}{later_pairs} failed{line_boundary}"

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"safe {header}: [redacted-secret]{line_boundary}"
    assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie", "Authorization"))
@pytest.mark.parametrize("line_ending", ("\n", "\r\n"))
@pytest.mark.parametrize("horizontal_space", ("", " ", "\t", " \t"))
def test_diagnostic_empty_header_value_does_not_consume_the_next_line(
    header,
    line_ending,
    horizontal_space,
):
    raw = f"{header}:{horizontal_space}{line_ending}status=healthy"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    "credential_key",
    ("password", "passwd", "secret", "token", "authorization", "cookie", "api_key", "api-key", "bearer"),
)
@pytest.mark.parametrize("separator", ("=", ":"))
@pytest.mark.parametrize("line_ending", ("\n", "\r\n"))
@pytest.mark.parametrize("horizontal_space", ("", " ", "\t", " \t"))
def test_diagnostic_empty_assignment_does_not_consume_the_next_line(
    credential_key,
    separator,
    line_ending,
    horizontal_space,
):
    raw = f"{credential_key}{horizontal_space}{separator}{horizontal_space}{line_ending}validation failed"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            "Cookie: sid=first-secret =second-secret failed after reconnect",
            "Cookie: [redacted-secret]",
            ("first-secret", "second-secret"),
        ),
        (
            'Set-Cookie: sid="first-secret"\t="second-secret" failed after reconnect',
            "Set-Cookie: [redacted-secret]",
            ("first-secret", "second-secret"),
        ),
        (
            "Cookie: sid=first-secret; =second-secret failed\nnext safe line",
            "Cookie: [redacted-secret]\nnext safe line",
            ("first-secret", "second-secret"),
        ),
    ),
)
def test_diagnostic_cookie_header_declines_when_a_residual_pair_name_is_empty(raw, expected, secret_fragments):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize("header", ("Cookie", "Set-Cookie"))
@pytest.mark.parametrize("separator", (" ", "\t", " \t "))
@pytest.mark.parametrize("first_quote", ("", '"', "'"))
@pytest.mark.parametrize("later_quote", ("", '"', "'"))
@pytest.mark.parametrize("residual_count", (1, 2, 3))
@pytest.mark.parametrize("line_boundary", ("", "\nnext safe line"))
def test_diagnostic_empty_cookie_name_matrix_fails_closed_through_the_current_line(
    header,
    separator,
    first_quote,
    later_quote,
    residual_count,
    line_boundary,
):
    fragments = ["first-secret"] + [f"later-secret-{index}" for index in range(residual_count)]
    residuals = separator.join(f"={later_quote}{fragment}{later_quote}" for fragment in fragments[1:])
    raw = (
        f"{header}: sid={first_quote}{fragments[0]}{first_quote}{separator}{residuals}"
        f" failed after reconnect{line_boundary}"
    )

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"{header}: [redacted-secret]{line_boundary}"
    assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("Cookie: =empty-name-secret", "Cookie: [redacted-secret]"),
        ("Set-Cookie: sid=first-secret; =empty-name-secret", "Set-Cookie: [redacted-secret]"),
        ("Cookie: sid=", "Cookie: [redacted-secret]"),
        ("Cookie: sid=; failed after reconnect", "Cookie: [redacted-secret]"),
        ("Cookie: sid= failed after reconnect", "Cookie: [redacted-secret] after reconnect"),
        ('Set-Cookie: sid="" failed after reconnect', "Set-Cookie: [redacted-secret] failed after reconnect"),
        ("Cookie: sid=first-secret ordinary trailing prose", "Cookie: [redacted-secret] ordinary trailing prose"),
        ("Cookie: [redacted-secret] ordinary trailing prose", "Cookie: [redacted-secret] ordinary trailing prose"),
    ),
)
def test_diagnostic_cookie_empty_name_value_and_safe_context_audit(raw, expected):
    assert redact_diagnostic_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            'Authorization: Digest username="alice", realm="example", response="digest-secret" failed at /api/ping',
            "Authorization: [redacted-secret]",
            ("alice", "example", "digest-secret"),
        ),
        (
            "Authorization: AWS4-HMAC-SHA256 Credential=access-secret/20260806/us/test, "
            "SignedHeaders=host, Signature=signature-secret failed",
            "Authorization: [redacted-secret]",
            ("access-secret", "signature-secret"),
        ),
        (
            "Authorization: token first-secret second-secret",
            "Authorization: [redacted-secret]",
            ("first-secret", "second-secret"),
        ),
        (
            'Proxy-Authorization:\tDigest username="proxy-user", response="proxy-secret" failed\nnext safe line',
            "Proxy-Authorization:\t[redacted-secret]\nnext safe line",
            ("proxy-user", "proxy-secret"),
        ),
    ),
)
def test_diagnostic_unknown_authorization_schemes_fail_closed_through_the_current_line(
    raw,
    expected,
    secret_fragments,
):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize(
    "header",
    ("Authorization", "authorization", "Proxy-Authorization", "proxy-authorization"),
)
@pytest.mark.parametrize("separator", (":", ": ", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Digest", "AWS4-HMAC-SHA256", "token", "Custom-Scheme"))
@pytest.mark.parametrize("line_boundary", ("", "\nnext safe line"))
def test_diagnostic_unknown_authorization_scheme_matrix_removes_every_same_line_canary(
    header,
    separator,
    scheme,
    line_boundary,
):
    raw = f'{header}{separator}{scheme} first-secret key="second-secret" third-secret failed{line_boundary}'

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"{header}{separator}[redacted-secret]{line_boundary}"
    assert all(fragment not in redacted for fragment in ("first-secret", "second-secret", "third-secret"))


@pytest.mark.parametrize("header", ("Authorization", "Proxy-Authorization"))
@pytest.mark.parametrize("separator", (":", ": ", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Basic", "basic", "Bearer", "BEARER"))
def test_diagnostic_complete_authorization_schemes_preserve_trailing_context(header, separator, scheme):
    raw = f"{header}{separator}{scheme} canonical-secret failed at /api/ping after 503"

    assert redact_diagnostic_value(raw) == f"{header}{separator}[redacted-secret] failed at /api/ping after 503"


@pytest.mark.parametrize("header", ("Authorization", "Proxy-Authorization"))
@pytest.mark.parametrize("line_ending", ("\n", "\r\n"))
@pytest.mark.parametrize("horizontal_space", ("", " ", "\t", " \t"))
def test_diagnostic_empty_authorization_header_does_not_consume_the_next_line(
    header,
    line_ending,
    horizontal_space,
):
    raw = f"{header}:{horizontal_space}{line_ending}status=healthy"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    "raw",
    (
        "Authorization: [redacted-secret] failed at /api/ping",
        "Proxy-Authorization\t:\t[redacted-secret] failed after reconnect",
    ),
)
def test_diagnostic_redacted_authorization_headers_are_idempotent(raw):
    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize("key", ("Authorization", "Proxy-Authorization"))
def test_diagnostic_quoted_json_authorization_uses_the_shared_assignment_owner(key):
    raw = f'{{"{key}":"Digest first-secret second-secret"}}'

    redacted = redact_diagnostic_value(raw)

    assert redacted == f'{{"{key}":"[redacted-secret]"}}'
    assert "first-secret" not in redacted
    assert "second-secret" not in redacted


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            'authorization=Digest username="alice", response="digest-secret" failed at /api/ping',
            "authorization=[redacted-secret]",
            ("alice", "digest-secret"),
        ),
        (
            'proxy_authorization: Digest username="proxy-user", response="proxy-secret" failed',
            "proxy_authorization: [redacted-secret]",
            ("proxy-user", "proxy-secret"),
        ),
        (
            "proxy-authorization=AWS4-HMAC-SHA256 Credential=access-secret, "
            "Signature=signature-secret failed",
            "proxy-authorization=[redacted-secret]",
            ("access-secret", "signature-secret"),
        ),
        (
            'Authorization = Digest username="alice", response="digest-secret" failed',
            "Authorization = [redacted-secret]",
            ("alice", "digest-secret"),
        ),
    ),
)
def test_diagnostic_unquoted_authorization_assignments_fail_closed_through_the_current_line(
    raw,
    expected,
    secret_fragments,
):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize(
    "key",
    ("authorization", "Authorization", "proxy-authorization", "proxy_authorization", "ProxyAuthorization"),
)
@pytest.mark.parametrize("separator", ("=", " = ", ":", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Digest", "AWS4-HMAC-SHA256", "Custom-Scheme", "Basic", "Bearer"))
@pytest.mark.parametrize("line_boundary", ("", "\nnext safe line"))
def test_diagnostic_unquoted_authorization_assignment_matrix_removes_multi_token_values(
    key,
    separator,
    scheme,
    line_boundary,
):
    raw = f'{key}{separator}{scheme} first-secret parameter="second-secret" third-secret{line_boundary}'

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"{key}{separator}[redacted-secret]{line_boundary}"
    assert all(fragment not in redacted for fragment in ("first-secret", "second-secret", "third-secret"))


@pytest.mark.parametrize("key", ("Authorization", "proxy_authorization", "Proxy-Authorization"))
@pytest.mark.parametrize("separator", ("=", " = ", ":", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Basic", "basic", "Bearer", "BEARER"))
def test_diagnostic_canonical_authorization_assignments_preserve_known_failure_context(key, separator, scheme):
    raw = f"{key}{separator}{scheme} canonical-secret failed at /api/ping after 503"

    assert redact_diagnostic_value(raw) == f"{key}{separator}[redacted-secret] failed at /api/ping after 503"


@pytest.mark.parametrize("key", ("authorization", "proxy_authorization", "Proxy-Authorization"))
@pytest.mark.parametrize("separator", ("=", " = ", ":", "\t:\t"))
@pytest.mark.parametrize("quote", ('"', "'"))
def test_diagnostic_quoted_authorization_assignments_preserve_selected_delimiter(key, separator, quote):
    raw = f"{key}{separator}{quote}Digest first-secret second-secret{quote} failed after reconnect"

    assert redact_diagnostic_value(raw) == f"{key}{separator}{quote}[redacted-secret]{quote} failed after reconnect"


@pytest.mark.parametrize("key", ("authorization", "proxy_authorization", "Proxy-Authorization"))
@pytest.mark.parametrize("separator", ("=", " = ", ":", "\t:\t"))
@pytest.mark.parametrize("line_ending", ("\n", "\r\n"))
def test_diagnostic_empty_authorization_assignment_preserves_the_next_line(key, separator, line_ending):
    raw = f"{key}{separator}{line_ending}status=healthy"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    "raw",
    (
        "authorization=[redacted-secret] failed at /api/ping",
        "proxy_authorization : [redacted-secret] failed after reconnect",
        "Proxy-Authorization\t=\t[redacted-secret] failed",
    ),
)
def test_diagnostic_redacted_authorization_assignments_are_idempotent(raw):
    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    "raw",
    (
        "X_API_KEY=x-api-secret failed at /api/ping",
        "XAPIKEY: x-api-secret failed after reconnect",
        "client_secret=client-secret failed",
        "access_token: access-secret failed",
        "refresh_token=refresh-secret failed",
        "x_share_token: share-secret failed",
    ),
)
def test_diagnostic_every_structured_credential_name_is_also_owned_in_free_text(raw):
    redacted = redact_diagnostic_value(raw)

    assert "[redacted-secret]" in redacted
    assert all(fragment not in redacted for fragment in ("x-api-secret", "client-secret", "access-secret", "refresh-secret", "share-secret"))


EXACT_DIAGNOSTIC_CREDENTIAL_NAMES = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "proxy_authorization",
    "proxy-authorization",
    "proxyauthorization",
    "cookie",
    "set_cookie",
    "set-cookie",
    "setcookie",
    "bearer",
    "api_key",
    "api-key",
    "apikey",
    "x_api_key",
    "x-api-key",
    "xapikey",
    "client_secret",
    "client-secret",
    "clientsecret",
    "access_token",
    "access-token",
    "accesstoken",
    "refresh_token",
    "refresh-token",
    "refreshtoken",
    "share_token",
    "share-token",
    "sharetoken",
    "x_share_token",
    "x-share-token",
    "xsharetoken",
)

BENIGN_DIAGNOSTIC_NEAR_NAMES = (
    "tokenizer",
    "secretary",
    "password_validation",
    "authorization_status",
    "cookiejar",
    "bearer_count",
    "api_keyring",
    "x_api_keyring",
    "client_secrets",
    "access_tokens",
    "refresh_tokenizer",
    "share_tokenizer",
    "x_share_tokenizer",
)


@pytest.mark.parametrize("credential_name", EXACT_DIAGNOSTIC_CREDENTIAL_NAMES)
@pytest.mark.parametrize("casing", ("lower", "upper"))
def test_diagnostic_structured_credential_name_matrix_uses_exact_shared_grammar(credential_name, casing):
    rendered_name = credential_name if casing == "lower" else credential_name.upper()

    assert redact_diagnostic_value({rendered_name: "matrix-secret"}) == {
        rendered_name: "[redacted-share-token]"
    }


@pytest.mark.parametrize("credential_name", EXACT_DIAGNOSTIC_CREDENTIAL_NAMES)
@pytest.mark.parametrize("casing", ("lower", "upper"))
@pytest.mark.parametrize("separator", ("=", ":"))
@pytest.mark.parametrize("horizontal_space", ("", " ", "\t"))
@pytest.mark.parametrize("value_mode", ("unquoted", "double", "single", "unterminated-double", "unterminated-single"))
def test_diagnostic_free_text_credential_name_matrix_uses_exact_shared_grammar(
    credential_name,
    casing,
    separator,
    horizontal_space,
    value_mode,
):
    rendered_name = credential_name if casing == "lower" else credential_name.upper()
    values = {
        "unquoted": "matrix-secret",
        "double": '"matrix-secret"',
        "single": "'matrix-secret'",
        "unterminated-double": '"matrix-secret',
        "unterminated-single": "'matrix-secret",
    }
    value = values[value_mode]
    prefix = f"{rendered_name}{horizontal_space}{separator}{horizontal_space}"
    raw = f"{prefix}{value} failed at /api/ping"
    normalized_name = rendered_name.lower().replace("-", "").replace("_", "")
    is_authorization = normalized_name in {"authorization", "proxyauthorization"}
    is_cookie_header = rendered_name.lower() in {"cookie", "set-cookie"} and separator == ":"
    if is_cookie_header:
        expected = f"{prefix}[redacted-secret]"
    elif value_mode == "double":
        expected = f'{prefix}"[redacted-secret]" failed at /api/ping'
    elif value_mode == "single":
        expected = f"{prefix}'[redacted-secret]' failed at /api/ping"
    elif value_mode.startswith("unterminated") or is_authorization:
        expected = f"{prefix}[redacted-secret]"
    else:
        expected = f"{prefix}[redacted-secret] failed at /api/ping"

    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert "matrix-secret" not in redacted


@pytest.mark.parametrize("credential_name", EXACT_DIAGNOSTIC_CREDENTIAL_NAMES)
@pytest.mark.parametrize("separator", ("=", ":"))
@pytest.mark.parametrize("line_ending", ("\n", "\r\n"))
def test_diagnostic_empty_exact_credential_assignment_preserves_the_next_line(
    credential_name,
    separator,
    line_ending,
):
    raw = f"{credential_name}{separator}{line_ending}status=healthy"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize("near_name", BENIGN_DIAGNOSTIC_NEAR_NAMES)
@pytest.mark.parametrize("separator", ("=", ":"))
def test_diagnostic_benign_near_names_remain_untouched_in_structured_and_free_text_values(near_name, separator):
    structured = {near_name: "ordinary-value"}
    free_text = f"{near_name}{separator}ordinary-value failed at /api/ping"

    assert redact_diagnostic_value(structured) == structured
    assert redact_diagnostic_value(free_text) == free_text


@pytest.mark.parametrize("credential_name", EXACT_DIAGNOSTIC_CREDENTIAL_NAMES)
@pytest.mark.parametrize("separator", ("=", ":"))
def test_diagnostic_exact_credential_assignment_is_idempotent(credential_name, separator):
    raw = f"{credential_name}{separator}[redacted-secret] failed at /api/ping"

    assert redact_diagnostic_value(raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected", "secret_fragments"),
    (
        (
            "Authorization: Bearer first-secret; Signature=signature-secret failed at /api/ping",
            "Authorization: [redacted-secret]",
            ("first-secret", "signature-secret"),
        ),
        (
            "Proxy-Authorization: Basic first-secret; Credential=access-secret failed",
            "Proxy-Authorization: [redacted-secret]",
            ("first-secret", "access-secret"),
        ),
        (
            "authorization=Bearer first-secret; response=digest-secret failed",
            "authorization=[redacted-secret]",
            ("first-secret", "digest-secret"),
        ),
        (
            "proxy_authorization: Basic first-secret; nonce=nonce-secret failed",
            "proxy_authorization: [redacted-secret]",
            ("first-secret", "nonce-secret"),
        ),
    ),
)
def test_diagnostic_canonical_authorization_declines_when_residual_parameters_remain(
    raw,
    expected,
    secret_fragments,
):
    redacted = redact_diagnostic_value(raw)

    assert redacted == expected
    assert all(fragment not in redacted for fragment in secret_fragments)


@pytest.mark.parametrize(
    "key",
    ("Authorization", "authorization", "Proxy-Authorization", "proxy_authorization", "ProxyAuthorization"),
)
@pytest.mark.parametrize("separator", (":", ": ", "=", " = ", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Basic", "basic", "Bearer", "BEARER"))
@pytest.mark.parametrize("parameter_name", ("Signature", "Credential", "response", "nonce", "arbitrary"))
@pytest.mark.parametrize("residual_prefix", ("; ", "\t", " prose; ", "; malformed "))
@pytest.mark.parametrize("residual_count", (1, 2))
@pytest.mark.parametrize("line_boundary", ("", "\nnext safe line"))
def test_diagnostic_canonical_authorization_residual_parameter_matrix_fails_closed(
    key,
    separator,
    scheme,
    parameter_name,
    residual_prefix,
    residual_count,
    line_boundary,
):
    fragments = [f"residual-secret-{index}" for index in range(residual_count)]
    residuals = "; ".join(
        f"{parameter_name}{index}={fragment}" for index, fragment in enumerate(fragments)
    )
    raw = f"{key}{separator}{scheme} first-secret{residual_prefix}{residuals} failed{line_boundary}"

    redacted = redact_diagnostic_value(raw)

    assert redacted == f"{key}{separator}[redacted-secret]{line_boundary}"
    assert "first-secret" not in redacted
    assert all(fragment not in redacted for fragment in fragments)


@pytest.mark.parametrize("key", ("Authorization", "Proxy-Authorization", "proxy_authorization"))
@pytest.mark.parametrize("separator", (": ", "=", "\t:\t"))
@pytest.mark.parametrize("scheme", ("Basic", "Bearer"))
@pytest.mark.parametrize(
    "safe_context",
    ("; failed at /api/ping", "; malformed prose only", " failed after reconnect", " Cookie: sid=cookie-secret"),
)
def test_diagnostic_canonical_authorization_without_residual_assignment_preserves_safe_context(
    key,
    separator,
    scheme,
    safe_context,
):
    raw = f"{key}{separator}{scheme} canonical-secret{safe_context}"
    expected_context = "" if safe_context.startswith(" Cookie:") else safe_context

    assert redact_diagnostic_value(raw) == f"{key}{separator}[redacted-secret]{expected_context}"


@pytest.mark.parametrize("key", ("Authorization", "Proxy-Authorization", "proxy_authorization"))
def test_diagnostic_quoted_json_canonical_authorization_with_parameters_remains_valid(key):
    raw = f'{{"{key}":"Bearer first-secret; Signature=signature-secret"}}'

    assert redact_diagnostic_value(raw) == f'{{"{key}":"[redacted-secret]"}}'


def test_diagnostic_structured_keys_redact_only_credential_names():
    raw = {
        "tokenizer": "gpt2",
        "secretary": "alice",
        "password_validation": "failed",
        "token": "token-secret",
        "client_secret": "client-secret",
        "access_token": "access-secret",
        "Authorization": "Basic basic-secret",
    }

    assert redact_diagnostic_value(raw) == {
        "tokenizer": "gpt2",
        "secretary": "alice",
        "password_validation": "failed",
        "token": "[redacted-share-token]",
        "client_secret": "[redacted-share-token]",
        "access_token": "[redacted-share-token]",
        "Authorization": "[redacted-share-token]",
    }


def test_redacts_a_readonly_mapping_instead_of_returning_it_unchanged():
    """W2: validated payloads arrive as MappingProxyType; the neutral redactor must
    walk any Mapping (not only dict) and return a plain redacted dict."""
    src = MappingProxyType({"message": "Bearer admission-secret", "note": "safe"})
    out = redact_diagnostic_value(src)

    assert isinstance(out, dict)
    assert out["message"] == "Bearer [redacted-secret]"
    assert out["note"] == "safe"
    assert "admission-secret" not in str(out)


def test_redactor_is_idempotent_over_a_mapping():
    """W2: applying the redactor twice yields the same result (no double-marking)."""
    once = redact_diagnostic_value(MappingProxyType({"message": "Bearer admission-secret"}))
    twice = redact_diagnostic_value(once)
    assert once == twice


@pytest.mark.parametrize(
    "case",
    SHARED_FIXTURE_CASES,
    ids=[f"{case['category']}:{case['name']}" for case in SHARED_FIXTURE_CASES],
)
def test_shared_contract_fixture_matches_the_python_owner(case):
    """W2: the one neutral contract. The Python owner must reproduce every checked-in expected value,
    guarding against silent drift between the fixture and the redactor that generated it."""
    assert redact_diagnostic_value(case["input"]) == case["expected"]


@pytest.mark.parametrize(
    "case",
    SHARED_FIXTURE_CASES,
    ids=[f"{case['category']}:{case['name']}" for case in SHARED_FIXTURE_CASES],
)
def test_shared_contract_fixture_is_idempotent_in_python(case):
    once = redact_diagnostic_value(case["input"])
    assert redact_diagnostic_value(once) == once


def test_no_fixture_secret_fragment_survives_python_redaction():
    """W2 negative proof: zero fixture secrets anywhere in redacted output. Every credential fragment
    the fixture inputs embed must be absent from the serialized redacted value."""
    for case in SHARED_FIXTURE_CASES:
        serialized_input = json.dumps(case["input"], ensure_ascii=False)
        serialized_output = json.dumps(redact_diagnostic_value(case["input"]), ensure_ascii=False)
        for fragment in SHARED_FIXTURE_SECRET_FRAGMENTS:
            if fragment not in serialized_input:
                continue
            assert fragment not in serialized_output, (
                f"{case['category']}/{case['name']} leaked {fragment!r} in {serialized_output}"
            )
