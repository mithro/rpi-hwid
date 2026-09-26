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


# --- the sheet ----------------------------------------------------------------

SHEET = (
    "Name,Device ID,MAC Address,IP,Type,Connection,Site,Physical Location,Machine,"
    "Human Name,Mon?,Hardware,Controls,Notes / Comments\n"
    "Servers,,,10.X.90.00X,,,,,,,,,,\n"
    "ha,,,10.X.90.2,DHCP,,,,ha,,,,,\n"
    "ESP_E7041B,10012b44c5,C4:4F:33:E7:04:1B,10.X.90.14,DHCP,Wi-Fi 2.4G,,Tim's Bedroom,"
    "bridge-433-3,Bridge433,0,Sonoff RFBridge433 R2 V2.2 with Tasmota flashed,,\n"
    "tasmota-D7C0E8-0232,14139624 (ESP32-C3 rev.4),7C:2C:67:D7:C0:E8,10.X.91.29,DHCP,"
    "Wi-Fi 2.4G,Welland,Tim's Bedroom,au-plug-29,Athom AU Plug 29,1,Athom Plug V3,aux.gvc,\n"
    "tasmota-B0A9D0-2512,11577808 (ESP32-C3 rev.4),24:EC:4A:B0:A9:D0,10.X.91.9,DHCP,"
    "Wi-Fi 2.4G,Monarto,Monarto,au-plug-9,Athom AU Plug 9,1,Athom Plug V3,"
    "\"ten64.monarto.mithis.com\nstarlink.monarto.mithis.com\",\n"
    "tasmota-D7C108-0264,14139656 (ESP32-C3 rev.4),7C:2C:67:D7:C1:08,,DHCP,Wi-Fi 2.4G,"
    "Welland,,au-plug-41,Athom AU Plug 41,1,Athom Plug V3,,\n"
    "tasmota-EABB9E-7070,1000383b67,bc:dd:c2:ea:bb:9e,10.X.91.250,DHCP,Wi-Fi 2.4G,"
    "Welland,Tim's Bedroom,us-plug-1,SW1,,S31 - US Plug,ac,Air Conditioner\n"
)


def test_the_sheet_yields_every_tasmota_row_of_the_sites_asked_for():
    devs, skipped = tasmota.sheet_devices(SHEET, {"welland": 1})
    assert [d.host for d in devs] == ["bridge-433-3", "au-plug-29", "us-plug-1"]
    plug = devs[1]
    assert plug.name == "tasmota-D7C0E8-0232"
    assert plug.ip == "10.1.91.29"
    assert plug.mac == "7c:2c:67:d7:c0:e8"
    assert plug.hardware == "Athom Plug V3"
    assert plug.device_id == "14139624 (ESP32-C3 rev.4)"
    assert plug.controls == ("aux.gvc",)
    # a blank Site is the first site asked for
    assert devs[0].ip == "10.1.90.14"
    assert dict(skipped) == {
        "au-plug-9": "site Monarto was not asked for (--site monarto=OCTET)",
        "au-plug-41": "no IP address in the sheet",
    }


def test_a_second_site_is_reached_by_its_own_octet():
    devs, skipped = tasmota.sheet_devices(SHEET, {"welland": 1, "monarto": 2})
    nine = next(d for d in devs if d.host == "au-plug-9")
    assert nine.ip == "10.2.91.9"
    assert nine.controls == ("ten64.monarto.mithis.com", "starlink.monarto.mithis.com")
    assert "au-plug-9" not in dict(skipped)


def test_a_sheet_without_the_columns_is_an_error():
    with pytest.raises(ValueError, match="MAC Address"):
        tasmota.sheet_devices("a,b,c\n1,2,3\n", {"welland": 1})
