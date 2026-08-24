"""Tailnet address detection.

The bind address is the dashboard's only access control, so a wrong answer
here either hides the dashboard from the user or exposes health data on an
interface they did not ask for. Both directions are worth pinning down.
"""

import subprocess

import pytest

from fitbit_pipeline import net


class FakeRun:
    """Stands in for subprocess.run, replaying canned output per argv[0]."""

    def __init__(self, outputs: dict[str, str], fail: set[str] | None = None):
        self.outputs = outputs
        self.fail = fail or set()
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        name = argv[0].rsplit("/", 1)[-1]
        if name in self.fail:
            raise OSError("not found")
        out = self.outputs.get(name)
        if out is None:
            return subprocess.CompletedProcess(argv, 1, "", "no such command")
        return subprocess.CompletedProcess(argv, 0, out, "")


def test_cli_address_is_preferred(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda n: "/usr/bin/tailscale")
    monkeypatch.setattr(net.subprocess, "run", FakeRun({"tailscale": "100.101.102.103\n"}))
    assert net.tailscale_ipv4() == "100.101.102.103"


def test_ipv6_only_output_falls_through_to_interfaces(monkeypatch):
    """`tailscale ip -4` can still print something that is not a v4 tailnet address."""
    monkeypatch.setattr(net.shutil, "which", lambda n: "/usr/bin/tailscale")
    monkeypatch.setattr(
        net.subprocess,
        "run",
        FakeRun({"tailscale": "fd7a:115c:a1e0::1\n", "ip": "3: tailscale0 inet 100.90.80.70/32 scope global\n"}),
    )
    assert net.tailscale_ipv4() == "100.90.80.70"


def test_interface_scan_ignores_non_tailnet_addresses(monkeypatch):
    """A LAN address must never be mistaken for the tailnet."""
    monkeypatch.setattr(net.shutil, "which", lambda n: None)
    monkeypatch.setattr(
        net.subprocess,
        "run",
        FakeRun({"ip": "2: eth0 inet 192.168.1.50/24\n3: docker0 inet 172.17.0.1/16\n"}),
    )
    with pytest.raises(net.TailscaleError):
        net.tailscale_ipv4()


def test_addresses_just_outside_the_cgnat_range_are_rejected():
    assert net._is_tailnet("100.64.0.1")
    assert net._is_tailnet("100.127.255.254")
    assert not net._is_tailnet("100.63.255.255")
    assert not net._is_tailnet("100.128.0.1")
    assert not net._is_tailnet("100.200.1.1")  # plausible looking, still not tailnet
    assert not net._is_tailnet("not-an-ip")


def test_error_names_the_remedy(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda n: None)
    monkeypatch.setattr(net.subprocess, "run", FakeRun({}, fail={"ip", "ifconfig"}))
    with pytest.raises(net.TailscaleError) as excinfo:
        net.tailscale_ipv4()
    assert "tailscale up" in str(excinfo.value)
