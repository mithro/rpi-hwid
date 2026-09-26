"""The Tasmota collector: read-only by construction, and what it reads."""

from __future__ import annotations

import pytest

from rpi_hwid import tasmota


@pytest.mark.parametrize("cmnd", [
    "Power", "Power Off", "Power 0", "Power1 1", "Restart 1", "Upgrade 1", "Reset 1",
    "Backlog Status 0; Power Off", "SetOption19 1", "Timer1", "Rule1 on", "WebServer 0",
    "Module 0", "Template {}", "Status 0;Power Off", "status 0 ", "Status 0\nPower Off",
    "Status 0%3BPower Off", "",
])
def test_a_command_that_is_not_read_only_is_refused(cmnd):
    """The allowlist is exact strings: any command that changes state, or a
    read-only one with anything added to it, never reaches a URL."""
    with pytest.raises(tasmota.NotReadOnlyError, match="refused"):
        tasmota.command_url("10.1.91.29", cmnd)


@pytest.mark.parametrize("cmnd", sorted(tasmota.READ_ONLY_COMMANDS))
def test_the_read_only_commands_make_a_url(cmnd):
    url = tasmota.command_url("10.1.91.29", cmnd)
    assert url.startswith("http://10.1.91.29/cm?cmnd=")
    assert ";" not in url
    assert " " not in url


def test_only_status_queries_and_bare_reads_are_allowed():
    """Every allowed command is a Status query or a bare Module/Template,
    which Tasmota answers without changing anything when given no value."""
    for cmnd in tasmota.READ_ONLY_COMMANDS:
        word, _, arg = cmnd.partition(" ")
        assert word in {"Status", "Module", "Template"}, cmnd
        if word != "Status":
            assert arg == "", cmnd


def test_a_page_that_is_not_read_only_is_refused():
    for path in ("/cm", "/u2", "/rt", "/cn", "/in?rst=1", "/?o=1", "/md", ""):
        with pytest.raises(tasmota.NotReadOnlyError, match="refused"):
            tasmota.page_url("10.1.91.29", path)
    assert tasmota.page_url("10.1.91.29", "/in") == "http://10.1.91.29/in"


def test_the_web_password_is_sent_but_never_in_a_refusal():
    url = tasmota.command_url("10.1.91.29", "Status 0", password="s3cret&x")
    assert "user=admin" in url
    assert "password=s3cret%26x" in url
    with pytest.raises(tasmota.NotReadOnlyError) as err:
        tasmota.command_url("10.1.91.29", "Power Off", password="s3cret&x")
    assert "s3cret" not in str(err.value)


def test_a_bad_address_is_refused():
    for ip in ("10.1.91.29/cm?cmnd=Power%20Off#", "evil.example.org", "10.1.91", ""):
        with pytest.raises(ValueError, match="address"):
            tasmota.command_url(ip, "Status 0")
