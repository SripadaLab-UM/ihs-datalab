from datalab.safety import policy
from datalab.safety.canary import Canaries
from datalab.web import CONTENT_SECURITY_POLICY


def test_datalabs_own_policy_keeps_the_promise():
    assert policy.problems(CONTENT_SECURITY_POLICY) == []


STRICT = {
    "default-src": "'self'",
    "script-src": "'self'",
    "connect-src": "'self'",
    "object-src": "'none'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",
}


def build(**changes: str) -> str:
    directives = {**STRICT, **{k.replace("_", "-"): v for k, v in changes.items()}}
    return "; ".join(f"{name} {sources}" for name, sources in directives.items())


def test_permissive_policies_are_caught():
    assert policy.problems("") == ["no security policy is sent"]
    assert policy.problems(build()) == []
    cases = {
        "connect-src allows https://example.org": build(connect_src="'self' https://example.org"),
        "connect-src allows *": build(connect_src="*"),
        "img-src allows https:": build(img_src="https:"),
        "script-src allows 'unsafe-eval'": build(script_src="'self' 'unsafe-eval'"),
        "frame-src allows https://evil.test": build(frame_src="https://evil.test"),
        "default-src allows *": build(default_src="'self' *"),
    }
    for problem, header in cases.items():
        assert problem in policy.problems(header), header


def test_a_repeated_directive_is_flagged():
    # Browsers ignore the later copy, but a repeat is never meant: say so.
    assert policy.problems(build() + "; script-src 'unsafe-eval'") == [
        "script-src appears more than once"
    ]


def test_canaries_report_only_their_own_hits():
    canaries = Canaries()
    hit = canaries.new("hit")
    missed = canaries.new("missed")
    canaries.record(hit.rsplit("/", 1)[1])
    canaries.record("not-issued")
    assert canaries.hits([hit, missed]) == ["hit"]


def test_more_specific_and_unknown_directives_are_judged():
    # These override script-src, style-src, and frame-src in a browser.
    cases = {
        "script-src-elem allows https://evil.example": build(
            script_src_elem="'self' https://evil.example"
        ),
        "script-src-attr allows 'unsafe-inline'": build(script_src_attr="'unsafe-inline'"),
        "style-src-elem allows https:": build(style_src_elem="https:"),
        "child-src allows *": build(child_src="*"),
        "manifest-src allows https://evil.example": build(manifest_src="https://evil.example"),
        "report-uri isn't a directive this check knows is safe": build(
            report_uri="https://evil.example/r"
        ),
        "navigate-to isn't a directive this check knows is safe": build(navigate_to="*"),
    }
    for expected, header in cases.items():
        assert expected in policy.problems(header), header
    assert policy.problems(build(sandbox="", upgrade_insecure_requests="")) == []


def test_names_only_python_would_read_can_hide_nothing():
    base = build()
    kelvin = chr(0x212A)  # lowercases to "k" in Python, not in a browser
    # A browser skips the first (not a valid name to it) and enforces the second.
    cases = {
        "connect-src allows *": "connect-src\xa0'self'; connect-src *; " + base,
        "worker-src allows *": f"wor{kelvin}er-src 'self'; worker-src *; " + base,
        "connect-src appears more than once": "connect-src 'self'; connect-src *; " + base,
    }
    for expected, header in cases.items():
        assert expected in policy.problems(header), header
    missing = "; ".join(p for p in build().split("; ") if not p.startswith("form-action"))
    assert "form-action is missing" in policy.problems(missing)
