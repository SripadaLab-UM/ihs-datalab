"""A stand-in for GitHub's Releases API and its file downloads, for the update tests.

Nothing here reaches the network: every request goes to `FakeGitHub.handler`
through httpx's MockTransport, and files are made in memory.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from typing import Any

import httpx

REPO = "SripadaLab-UM/ihs-datalab"
API = "https://api.github.com"
STORAGE = "https://release-assets.githubusercontent.com"

AGENT = "ghcr.io/sripadalab-um/datalab-agent@sha256:" + "a" * 64
GATEWAY = "docker.io/library/nginx@sha256:" + "b" * 64
PROXY = "docker.io/ubuntu/squid@sha256:" + "c" * 64


def wheel_bytes(
    version: str, *, agent: str = AGENT, gateway: str = GATEWAY, proxy: str = PROXY
) -> bytes:
    """A small zip laid out like the DataLab package, with what the image check reads."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr("datalab/__init__.py", f'__version__ = "{version}"\n')
        package.writestr("datalab/release.json", json.dumps({"agent_image": agent}))
        package.writestr(
            "datalab/sessions/containers.py",
            f'GATEWAY_IMAGE = "{gateway}"\nPROXY_IMAGE = "{proxy}"\n',
        )
    return out.getvalue()


def images_bytes(agent: str = AGENT, gateway: str = GATEWAY, proxy: str = PROXY) -> bytes:
    return json.dumps({"agent": agent, "gateway": gateway, "proxy": proxy}, indent=2).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sums_for(files: dict[str, bytes]) -> bytes:
    return "".join(f"{sha256(data)}  {name}\n" for name, data in files.items()).encode()


class FakeGitHub:
    def __init__(self) -> None:
        self.releases: list[dict[str, Any]] = []
        self.files: dict[int, bytes] = {}
        self.requests: list[httpx.Request] = []
        # Answers every request instead, when set (offline, rate-limited...).
        self.answer: Callable[[httpx.Request], httpx.Response] | None = None
        # Where asset downloads redirect to.
        self.storage = STORAGE
        self._next_id = 1000

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def release(
        self,
        tag: str,
        *,
        version: str | None = None,
        prerelease: bool = False,
        draft: bool = False,
        notes: str = "What's new.",
        files: dict[str, bytes] | None = None,
        sums: bytes | None = None,
        omit: tuple[str, ...] = (),
        digests: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Add a release with the files an update needs (or `files`), and its SHA256SUMS."""
        version = version or tag.removeprefix("v").replace("-alpha.", "a").replace("-rc.", "rc")
        if files is None:
            files = {
                f"datalab-{version}-py3-none-any.whl": wheel_bytes(version),
                "constraints.txt": b"httpx==0.28.1\n",
                "images.json": images_bytes(),
                "install-macos.sh": b"#!/bin/sh\n",
            }
        files = {k: v for k, v in files.items() if k not in omit}
        if "SHA256SUMS" not in omit:
            files["SHA256SUMS"] = sums if sums is not None else sums_for(files)
        assets = []
        for name, data in files.items():
            self._next_id += 1
            self.files[self._next_id] = data
            digest = (digests or {}).get(name, f"sha256:{sha256(data)}")
            assets.append(
                {
                    "name": name,
                    "url": f"{API}/repos/{REPO}/releases/assets/{self._next_id}",
                    "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
                    "size": len(data),
                    "digest": digest,
                }
            )
        release = {
            "tag_name": tag,
            "name": f"DataLab {tag}",
            "draft": draft,
            "prerelease": prerelease,
            "body": notes,
            "published_at": "2026-09-27T12:00:00Z",
            "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
            "assets": assets,
        }
        self.releases.insert(0, release)
        return release

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answer is not None:
            return self.answer(request)
        url = request.url
        if str(url).startswith(f"{API}/repos/{REPO}/releases/assets/"):
            asset_id = int(url.path.rsplit("/", 1)[1])
            return httpx.Response(302, headers={"location": f"{self.storage}/{asset_id}?sig=x"})
        if url.path == f"/repos/{REPO}/releases" and url.host == "api.github.com":
            return httpx.Response(200, json=self.releases)
        if str(url).startswith(STORAGE):
            asset_id = int(url.path.strip("/"))
            return httpx.Response(200, content=self.files[asset_id])
        return httpx.Response(404, json={"message": "Not Found"})
