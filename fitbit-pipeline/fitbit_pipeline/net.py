"""Finding the machine's Tailscale address.

The dashboard has no authentication, so the bind address is the only access
control it has. Binding the tailnet address specifically, rather than
0.0.0.0, keeps it off the local LAN and off any other interface: only
devices on the tailnet can reach it.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess

# Tailscale hands out addresses from the CGNAT range.
TAILNET = ipaddress.ip_network("100.64.0.0/10")

# macOS installs the CLI inside the app bundle and does not always add it to PATH.
CLI_CANDIDATES = (
    "tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)


class TailscaleError(RuntimeError):
    """Raised when the tailnet address cannot be determined."""


def _run(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _is_tailnet(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr) in TAILNET
    except ValueError:
        return False


def from_cli() -> str | None:
    """Ask the Tailscale CLI directly. The reliable path when it is installed."""
    for name in CLI_CANDIDATES:
        path = shutil.which(name) or (name if name.startswith("/") else None)
        if not path:
            continue
        out = _run([path, "ip", "-4"])
        if not out:
            continue
        for line in out.split():
            if _is_tailnet(line):
                return line
    return None


def from_interfaces() -> str | None:
    """Fall back to reading interface addresses, for hosts without the CLI on PATH."""
    for argv in (["ip", "-4", "-o", "addr"], ["ifconfig"]):
        out = _run(argv)
        if not out:
            continue
        for candidate in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", out):
            if _is_tailnet(candidate):
                return candidate
    return None


def tailscale_ipv4() -> str:
    """The machine's tailnet IPv4 address.

    Raises TailscaleError with the remedy rather than silently falling back to
    a wider bind address, because a silent fallback here would mean exposing
    health data on an interface the user did not ask for.
    """
    found = from_cli() or from_interfaces()
    if found:
        return found
    raise TailscaleError(
        "No Tailscale IPv4 address found on this machine.\n"
        "  - Is Tailscale installed and running? Try: tailscale status\n"
        "  - Is this machine logged in to the tailnet? Try: tailscale up\n"
        "Looked for the tailscale CLI, then for an interface address in "
        "100.64.0.0/10."
    )
