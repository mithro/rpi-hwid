"""The Tasmota collector: read-only by construction, and what it reads."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from rpi_hwid import tasmota
from rpi_hwid.model import ProbeDocument

# Four devices read through the allowlist on 2026-09-26: an Athom Plug V3
# (ESP32-C3, Tasmota 13.1), a Sonoff S31 (ESP8266), an Athom IR remote
# (ESP8266, no relay) and a bare ESP32-C3 SuperMini (Tasmota 15, the
# generic module). "in" is the Information page's table string.
DEVICES = json.loads((Path(__file__).parent / "tasmota_devices.json").read_text())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A test must never reach a real device: these plugs power things."""
    def refuse(*a, **kw):
        raise AssertionError(f"a test tried to open a URL: {a!r}")
    monkeypatch.setattr(tasmota.urllib.request, "urlopen", refuse)


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


# --- reading a device ---------------------------------------------------------


def _dev(host, **kw):
    d = DEVICES[host]
    mac = d["status"]["StatusNET"]["Mac"]
    base = {"host": host, "name": "tasmota-" + mac.replace(":", "")[6:], "ip": d["ip"],
            "mac": tasmota.normalise_mac(mac), "site": "Welland"}
    base.update(kw)
    return tasmota.SheetDevice(**base)


class FakeDevice:
    """Answers the allowlisted reads from a captured device, and records
    every URL it was asked for."""

    def __init__(self, host, broken=(), warn=False):
        self.d = DEVICES[host]
        self.broken, self.warn = set(broken), warn
        self.urls: list[str] = []

    def __call__(self, url, timeout, headers):
        self.urls.append(url)
        u = urllib.parse.urlsplit(url)
        what = (urllib.parse.parse_qs(u.query)["cmnd"][0] if u.path == "/cm" else u.path)
        if what in self.broken:
            raise urllib.error.URLError(OSError(113, "No route to host"))
        if self.warn:
            return b'{"WARNING":"Need user=<username>&password=<password>"}'
        body = {"Status 0": self.d["status"], "Module": self.d["module"],
                "Template": self.d["template"]}.get(what)
        if body is not None:
            return json.dumps(body).encode()
        assert what == "/in", what
        return ("<html><body><script>function i(){var s,o=\"" + self.d["in"]
                + "\";}</script></body></html>").encode()


def _read(host, **kw):
    fake = FakeDevice(host, **{k: kw.pop(k) for k in ("broken", "warn") if k in kw})
    return tasmota.read_device(_dev(host, **kw), fetch=fake), fake


def test_a_plug_is_read_with_four_allowlisted_requests_and_no_more():
    r, fake = _read("au-plug-29")
    assert r.ok, r.error
    assert len(fake.urls) == 4
    for url in fake.urls:
        u = urllib.parse.urlsplit(url)
        if u.path == "/cm":
            assert urllib.parse.parse_qs(u.query)["cmnd"][0] in tasmota.READ_ONLY_COMMANDS
        else:
            assert u.path in tasmota.READ_ONLY_PAGES


def test_the_athom_plug_document():
    r, _ = _read("au-plug-29")
    t = r.doc.evidence["verdict"]["tasmota"]
    assert t["mac"] == "7c:2c:67:d7:c0:e8"
    assert t["model"] == "Athom Plug V3"
    assert t["module"] == {"id": 0, "name": "Athom Plug V3"}
    assert t["template_name"] == "Athom Plug V3"
    assert t["generic"] is False
    assert t["hardware"] == "ESP32-C3 rev.4"
    assert t["chip"] == "ESP32-C3"
    assert t["chip_revision"] == "rev.4"
    # the web UI's ESP Chip Id, read from the device, and what it is: the
    # MAC's low 24 bits
    assert t["esp_chip_id"] == 14139624
    assert t["esp_chip_id"] == 0xD7C0E8
    assert t["flash_chip_id"] == "164020"
    assert t["flash_jedec"] == "204016"
    assert t["flash_size_kb"] == 4096
    assert t["relays"] == 1
    assert t["read_errors"] == {}
    assert t["sheet"]["name"] == "tasmota-D7C0E8"
    assert t["sheet"]["ip"] == "10.1.91.29"


def test_a_document_loads_as_a_probe_document_and_is_not_a_board():
    r, _ = _read("au-plug-29")
    doc = ProbeDocument.from_json(r.host, r.doc.to_json())
    assert doc.summary.model == "Athom Plug V3"
    assert [(m.kind, m.mac) for m in doc.summary.macs] == [("wlan", "7c:2c:67:d7:c0:e8")]
    from rpi_hwid import labels
    assert labels.board_record(doc) is None


def test_the_sonoff_s31_is_named_by_its_module_not_the_generic_template():
    r, _ = _read("us-plug-1")
    t = r.doc.evidence["verdict"]["tasmota"]
    assert t["model"] == "Sonoff S31"
    assert t["module"] == {"id": 41, "name": "Sonoff S31"}
    assert t["chip"] == "ESP8266EX"
    assert t["chip_revision"] is None
    assert t["esp_chip_id"] == 0xEABB9E
    assert t["flash_jedec"] == "ef4016"
    assert t["relays"] == 1


def test_the_ir_remote_has_no_relay_and_a_2_mb_flash():
    r, _ = _read("ir-ac-remote")
    t = r.doc.evidence["verdict"]["tasmota"]
    assert t["model"] == "Athom_IR_Remote"
    assert t["relays"] == 0
    assert t["flash_size_kb"] == 2048
    assert t["flash_jedec"] == "a14015"


def test_a_bare_module_is_marked_generic():
    r, _ = _read("esp32-433mhz-cc1101-blue")
    t = r.doc.evidence["verdict"]["tasmota"]
    assert t["generic"] is True
    assert t["chip"] == "ESP32-C3"
    assert t["chip_revision"] == "v0.4"


def test_a_device_that_does_not_answer_is_asked_once():
    r, fake = _read("au-plug-29", broken={"Status 0"})
    assert not r.ok
    assert len(fake.urls) == 1
    assert "10.1.91.29" in r.error
    assert "No route to host" in r.error


def test_a_later_read_failing_is_recorded_not_fatal():
    r, _ = _read("au-plug-29", broken={"/in"})
    assert r.ok
    t = r.doc.evidence["verdict"]["tasmota"]
    assert t["esp_chip_id"] is None
    assert "/in" in t["read_errors"]["info"]


def test_a_device_that_wants_a_password_says_how_to_give_it():
    r, _ = _read("au-plug-29", warn=True)
    assert not r.ok
    assert tasmota.ENV_PASSWORD in r.error


def test_a_device_answering_with_another_mac_is_refused():
    """The sheet's address is answered by a different device: its document
    would be filed under the wrong host."""
    r, _ = _read("au-plug-29", mac="7c:2c:67:00:00:01")
    assert not r.ok
    assert "7c:2c:67:d7:c0:e8" in r.error
    assert "7c:2c:67:00:00:01" in r.error


def test_the_password_goes_in_the_request_and_nowhere_in_the_document():
    fake = FakeDevice("au-plug-29")
    seen = []

    def fetch(url, timeout, headers):
        seen.append(headers)
        return fake(url, timeout, headers)

    r = tasmota.read_device(_dev("au-plug-29"), fetch=fetch, password="hunter2x")
    assert r.ok
    assert "hunter2x" in fake.urls[0]
    assert all("Authorization" in h for h in seen)
    assert "hunter2x" not in r.doc.to_json()


def test_collect_writes_one_document_per_answering_device(tmp_path):
    fakes = {h: FakeDevice(h, broken={"Status 0"} if h == "ir-ac-remote" else ())
             for h in DEVICES}
    by_ip = {DEVICES[h]["ip"]: f for h, f in fakes.items()}

    def fetch(url, timeout, headers):
        return by_ip[urllib.parse.urlsplit(url).hostname](url, timeout, headers)

    results = tasmota.collect([_dev(h) for h in DEVICES], tmp_path, fetch=fetch, workers=2)
    assert [r.host for r in results] == sorted(DEVICES)
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == sorted(
        h for h in DEVICES if h != "ir-ac-remote")
    assert not next(r for r in results if r.host == "ir-ac-remote").ok


def _sheet_for_fixtures():
    head = ("Name,Device ID,MAC Address,IP,Type,Connection,Site,Physical Location,Machine,"
            "Human Name,Mon?,Hardware,Controls,Notes / Comments\n")
    rows = []
    for host, d in DEVICES.items():
        mac = d["status"]["StatusNET"]["Mac"]
        ip = d["ip"].replace("10.1.", "10.X.", 1)
        rows.append(f"tasmota-{mac.replace(':', '')[6:]}-0001,,{mac},{ip},DHCP,Wi-Fi 2.4G,"
                    f"Welland,,{host},,1,,,\n")
    rows.append("tasmota-B0A9D0-2512,,24:EC:4A:B0:A9:D0,10.X.91.9,DHCP,Wi-Fi 2.4G,Monarto,,"
                "au-plug-9,,1,Athom Plug V3,,\n")
    return head + "".join(rows)


def test_the_command_reads_the_sheet_and_reports_every_device(tmp_path, monkeypatch, capsys):
    from rpi_hwid.cli import main as cli_main

    sheet = tmp_path / "iot.csv"
    sheet.write_text(_sheet_for_fixtures())
    fakes = {DEVICES[h]["ip"]: FakeDevice(h, broken={"Status 0"} if h == "ir-ac-remote"
                                          else ()) for h in DEVICES}
    monkeypatch.setattr(tasmota, "urllib_fetch", lambda url, t, h: fakes[
        urllib.parse.urlsplit(url).hostname](url, t, h))
    monkeypatch.delenv(tasmota.ENV_PASSWORD, raising=False)
    out = tmp_path / "data"
    rc = cli_main(["tasmota", "--sheet", str(sheet), "--site", "welland=1", "--out", str(out)])
    text = capsys.readouterr().out
    assert rc == 1                      # one device did not answer
    assert "au-plug-29: Athom Plug V3 7c:2c:67:d7:c0:e8" in text
    assert "ir-ac-remote: FAILED (no answer to Status 0 from 10.1.90.15" in text
    assert "au-plug-9: SKIPPED (site Monarto was not asked for" in text
    assert "3 of 4 device(s) written to" in text
    assert len(list(out.glob("*.json"))) == 3


def test_the_command_can_be_limited_to_some_hosts(tmp_path, monkeypatch, capsys):
    from rpi_hwid.cli import main as cli_main

    sheet = tmp_path / "iot.csv"
    sheet.write_text(_sheet_for_fixtures())
    fake = FakeDevice("us-plug-1")
    monkeypatch.setattr(tasmota, "urllib_fetch", fake)
    rc = cli_main(["tasmota", "--sheet", str(sheet), "--site", "welland=1",
                   "--out", str(tmp_path / "d"), "us-plug-1"])
    assert rc == 0
    assert "1 of 1 device(s) written" in capsys.readouterr().out
    assert {urllib.parse.urlsplit(u).hostname for u in fake.urls} == {"10.1.91.250"}
    with pytest.raises(SystemExit):
        cli_main(["tasmota", "--sheet", str(sheet), "--site", "welland=1",
                  "--out", str(tmp_path / "d"), "no-such-plug"])


def test_the_sheet_url_can_come_from_gdoc2netcfg_config(tmp_path):
    cfg = tmp_path / "gdoc2netcfg.toml"
    cfg.write_text('[site]\nname = "welland"\nsite_octet = 1\n'
                   '[sheets]\niot = "https://docs.google.com/x/pub?output=csv"\n')
    assert tasmota.gdoc2netcfg_source(cfg) == (
        "https://docs.google.com/x/pub?output=csv", {"welland": 1})
