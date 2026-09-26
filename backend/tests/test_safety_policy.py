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


def test_only_the_first_copy_of_a_directive_counts():
    # Browsers ignore a repeated directive, so a later, looser copy does nothing.
    assert policy.problems(build() + "; script-src 'unsafe-eval'") == []


def test_canaries_report_only_their_own_hits():
    canaries = Canaries()
    hit = canaries.new("hit")
    missed = canaries.new("missed")
    canaries.record(hit.rsplit("/", 1)[1])
    canaries.record("not-issued")
    assert canaries.hits([hit, missed]) == ["hit"]
