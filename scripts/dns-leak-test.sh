#!/usr/bin/env bash
# Proves that no DNS lookup leaves a data session, not even one that fails.
#
# A failed lookup can still carry data out in the name it asks for, so
# "the name didn't resolve" isn't enough. This watches every network
# interface on the computer (Docker's bridges and the real network) while the
# Safety check runs, then looks for the check's one-off lookup marker. Any
# sign of it on the wire is a leak.
#
# So that a broken capture can't pass, a control lookup is made from this
# computer itself afterwards, and it must show up in the capture.
#
# Linux only (CI). Needs tcpdump and sudo. Run from backend/, with the same
# environment as `datalab safety-check`.
set -euo pipefail

capture=$(mktemp)
capture_log=$(mktemp)
report=$(mktemp)
tcpdump=""
cleanup() {
  if [ -n "$tcpdump" ]; then sudo kill "$tcpdump" 2>/dev/null || true; fi
  rm -f "$capture" "$capture_log" "$report"
}
trap cleanup EXIT

fail() {
  echo "DNS leak test FAILED: $*" >&2
  exit 1
}

# Plain DNS, mDNS and LLMNR, on every interface, packets printed as text.
# (DNS over TLS is encrypted, so a name can't be seen in it; the Safety check
# itself shows a data session can't open any outside connection.)
sudo tcpdump -i any -n -l -s 0 -A 'port 53 or port 5353 or port 5355' \
  >"$capture" 2>"$capture_log" &
tcpdump=$!
for _ in $(seq 1 50); do
  grep -q "listening on" "$capture_log" && break
  sleep 0.2
done
grep -q "listening on" "$capture_log" || fail "tcpdump didn't start: $(cat "$capture_log")"

uv run datalab safety-check --strict | tee "$report"

sudo kill -0 "$tcpdump" 2>/dev/null || fail "tcpdump stopped during the check: $(cat "$capture_log")"
control="dnsctl-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
getent hosts "$control.example.com" >/dev/null || true
sleep 2
sudo kill "$tcpdump" 2>/dev/null || true
wait "$tcpdump" 2>/dev/null || true
tcpdump=""

marker=$(grep -o 'dnsleak-[0-9a-f]\{12\}' "$report" | head -1 || true)
[ -n "$marker" ] || fail "the Safety check didn't report its lookup marker."
grep -qi "$control" "$capture" || fail "the capture didn't see a lookup from this computer, so it can't be trusted."
if grep -qi "$marker" "$capture"; then
  grep -i -B2 "$marker" "$capture" >&2
  fail "$marker left a data session."
fi
echo "DNS leak test passed: $marker never appeared on any interface (and the control lookup did)."
