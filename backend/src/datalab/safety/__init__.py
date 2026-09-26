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
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from datalab.config import Settings
from datalab.credentials import MissingCredential, oracle_password
from datalab.data.oracle import NotSyntheticDatabase, OracleDatabase
from datalab.safety import policy
from datalab.safety.canary import Canaries
from datalab.sessions.containers import SessionContainers, SessionPaths, docker
from datalab.sessions.tokens import SessionAccess, SessionKind, SessionTokens

Status = Literal["pass", "fail", "skip"]

PROMISE_HOST = "The AI can't touch anything else on your computer"
PROMISE_DATABASE = "The AI can't change or damage the research database"
PROMISE_NETWORK = "Sensitive data only goes to approved places"

_DATABASE_TIMEOUT = 30


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
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._canaries = canaries
        self._model_key = model_key
        # Tests swap in deliberately broken containers to prove checks can fail.
        self._containers = containers
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
        )
        token = self._tokens.issue(SessionAccess(session_id, kind, paths.oracle_results))
        paths.create()
        paths.codex_config.write_text("# safety probe\n")
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
        results.append(
            _result(
                "outside_dns_blocked",
                PROMISE_NETWORK,
                "Outside names don't resolve (no DNS leaks)",
                outside != 0 and gateway == 0,
                "Only the gateway resolves." if outside != 0 else "example.com resolved.",
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
        for label, body in _HOSTED_TOOL_PROBES:
            response = await probe.http(
                "POST", "http://gateway/v1/responses", token=True, body=body
            )
            if not (response.status == 403 and "datalab_refused" in response.body):
                not_refused.append(f"{label} (HTTP {response.status})")
        results.append(
            _result(
                "hosted_tools_refused",
                PROMISE_NETWORK,
                "Hosted tools and provider-side links are refused",
                not not_refused,
                "Not refused: " + ", ".join(not_refused)
                if not_refused
                else "Web search, remote MCP, code interpreter, and image links were refused.",
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
                content = path.read_text(errors="ignore")
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
            privileges = await asyncio.wait_for(
                asyncio.to_thread(database.session_privileges), _DATABASE_TIMEOUT
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

    def _known_secrets(self) -> list[str]:
        found = []
        with contextlib.suppress(MissingCredential):
            found.append(self._model_key())
        if self._settings.oracle is not None:
            with contextlib.suppress(MissingCredential):
                found.append(oracle_password(self._settings.oracle))
        return found


# Requests an agent might send straight to the gateway to get U-M's servers to
# reach outside on its behalf. The relay must refuse every one.
_BASE = {"model": "gpt-5.5", "input": "hello", "store": False}
_IMAGE_LINK = {"type": "input_image", "image_url": "https://example.com/a.png"}
_HOSTED_TOOL_PROBES = [
    ("web search", {**_BASE, "tools": [{"type": "web_search"}]}),
    ("remote MCP", {**_BASE, "tools": [{"type": "mcp", "server_url": "https://example.com"}]}),
    ("code interpreter", {**_BASE, "tools": [{"type": "code_interpreter"}]}),
    ("stored response", {**_BASE, "store": True}),
    (
        "image link",
        {**_BASE, "input": [{"type": "message", "role": "user", "content": [_IMAGE_LINK]}]},
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
        body: dict | None = None,
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
                _quote(json.dumps(body)),
            ]
        parts.append(_quote(url))
        _, output = await self.sh(" ".join(parts))
        text, _, code = output.rstrip().rpartition("\n")
        return _HttpResult(int(code) if code.isdigit() else 0, text)

    async def inspect(self) -> dict:
        return json.loads(await docker("inspect", self.containers.agent))[0]


def _result(check_id: str, promise: str, label: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(check_id, promise, label, "pass" if ok else "fail", detail)


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
