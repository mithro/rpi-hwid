"""The stdlib IPP client rpi-hwid-sheet prints with (rpi_hwid.sheet.ipp):
its encoding checked byte for byte against RFC 8010, and a print job sent
to a fake printer that decodes what it is sent."""

from __future__ import annotations

import http.server
import struct
import threading

import pytest

from rpi_hwid.sheet import ipp

# --- encoding --------------------------------------------------------------------


def test_a_plain_attribute_is_tag_name_and_value():
    got = ipp.encode_attribute("sides", ipp.keyword("one-sided"))
    assert got == b"\x44" + struct.pack(">H", 5) + b"sides" + struct.pack(">H", 9) + b"one-sided"


def test_an_integer_is_four_bytes_big_endian():
    got = ipp.encode_attribute("job-id", ipp.integer(311))
    assert got == b"\x21\x00\x06job-id\x00\x04" + struct.pack(">i", 311)


def test_further_values_of_one_attribute_have_no_name():
    got = ipp.encode_attribute("requested-attributes",
                               [ipp.keyword("job-state"), ipp.keyword("job-id")])
    assert got == (b"\x44\x00\x14requested-attributes\x00\x09job-state"
                   b"\x44\x00\x00\x00\x06job-id")


def test_a_collection_is_begin_members_end():
    """RFC 8010 3.1.6: begCollection with the name, then each member as a
    memberAttrName carrying its name followed by its value with no name,
    then endCollection."""
    got = ipp.encode_attribute("media-col", ipp.collection({
        "media-source": ipp.keyword("manual"),
        "media-size": ipp.collection({"x-dimension": ipp.integer(21000)}),
    }))
    want = (b"\x34\x00\x09media-col\x00\x00"
            b"\x4a\x00\x00\x00\x0cmedia-source"
            b"\x44\x00\x00\x00\x06manual"
            b"\x4a\x00\x00\x00\x0amedia-size"
            b"\x34\x00\x00\x00\x00"
            b"\x4a\x00\x00\x00\x0bx-dimension"
            b"\x21\x00\x00\x00\x04" + struct.pack(">i", 21000)
            + b"\x37\x00\x00\x00\x00"
            b"\x37\x00\x00\x00\x00")
    assert got == want


def test_a_request_round_trips_through_the_decoder():
    req = ipp.encode_request(ipp.PRINT_JOB, 7, {
        "attributes-charset": ipp.charset("utf-8"),
        "attributes-natural-language": ipp.language("en"),
        "printer-uri": ipp.uri("ipp://p/ipp/print"),
    }, {
        "media-col": ipp.collection({"media-source": ipp.keyword("manual"),
                                     "media-type": ipp.keyword("labels")}),
        "copies": ipp.integer(1),
    }, data=b"%PDF-")
    msg = ipp.decode(req, request=True)
    assert msg.code == ipp.PRINT_JOB
    assert msg.request_id == 7
    assert msg.data == b"%PDF-"
    op, job = msg.groups
    assert op[0] == ipp.OPERATION_GROUP
    assert op[1]["printer-uri"] == ["ipp://p/ipp/print"]
    assert job[0] == ipp.JOB_GROUP
    assert job[1]["media-col"] == [{"media-source": ["manual"], "media-type": ["labels"]}]
    assert job[1]["copies"] == [1]


def test_the_decoder_reads_out_of_band_values_and_enums():
    raw = (struct.pack(">BBHI", 2, 0, 0x0000, 9) + b"\x04"
           + ipp.encode_attribute("printer-state", ipp.enum(3))
           + b"\x13\x00\x0bprinter-geo\x00\x00"      # no-value
           + b"\x03")
    msg = ipp.decode(raw)
    assert msg.code == 0
    assert msg.attributes("printer") == {"printer-state": [3], "printer-geo": [None]}


def test_a_truncated_message_is_an_error_not_garbage():
    with pytest.raises(ipp.IppError, match="truncated"):
        ipp.decode(b"\x02\x00\x00\x00\x00\x00\x00\x01\x01\x44\x00\x05sid")


# --- talking to a printer ----------------------------------------------------------


class FakePrinter:
    """An IPP printer on localhost that records each request it decodes and
    answers from `answer(msg)`."""

    def __init__(self, answer):
        self.requests: list[ipp.Message] = []
        printer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                msg = ipp.decode(body, request=True)
                printer.requests.append(msg)
                reply = answer(msg)
                if reply is None:            # hang up: the request was taken, no answer
                    self.close_connection = True
                    return
                status, groups = reply
                out = ipp.encode_response(status, msg.request_id, groups)
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.uri = f"ipp://127.0.0.1:{self.server.server_address[1]}/ipp/print"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def printer():
    made = []

    def make(answer):
        p = FakePrinter(answer)
        made.append(p)
        return p

    yield make
    for p in made:
        p.close()


def ok(group, attrs):
    return 0, [(group, attrs)]


def test_print_job_sends_the_pdf_to_the_manual_feed_at_full_size(printer):
    p = printer(lambda msg: ok(ipp.JOB_GROUP, {"job-id": ipp.integer(312),
                                               "job-state": ipp.enum(3)}))
    job = ipp.Printer(p.uri).print_job(b"%PDF-1.4 sheet", "sheet K7QX pass 1", user="tim")
    assert job.id == 312
    (msg,) = p.requests
    assert msg.code == ipp.PRINT_JOB
    assert msg.data == b"%PDF-1.4 sheet"
    op = msg.attributes("operation")
    assert op["printer-uri"] == [p.uri]
    assert op["document-format"] == ["application/pdf"]
    assert op["job-name"] == ["sheet K7QX pass 1"]
    assert op["requesting-user-name"] == ["tim"]
    j = msg.attributes("job")
    (col,) = j["media-col"]
    # the manual feed slot, never the cassette under it
    assert col["media-source"] == ["manual"]
    assert col["media-type"] == ["labels"]
    assert col["media-size"] == [{"x-dimension": [21000], "y-dimension": [29700]}]
    assert j["print-scaling"] == ["none"]
    assert j["print-color-mode"] == ["color"]
    assert j["sides"] == ["one-sided"]


def test_a_refused_job_is_an_error_with_the_printers_reason(printer):
    p = printer(lambda msg: (0x040B, [(ipp.OPERATION_GROUP, {
        "status-message": ipp.text("media-source manual not ready")})]))
    with pytest.raises(ipp.IppError, match="media-source manual not ready"):
        ipp.Printer(p.uri).print_job(b"%PDF", "x")


def test_ignored_attributes_are_reported_not_silent(printer):
    """successful-ok-ignored-or-substituted-attributes: the job prints,
    and what the printer ignored is named."""
    p = printer(lambda msg: (0x0001, [
        (ipp.JOB_GROUP, {"job-id": ipp.integer(5), "job-state": ipp.enum(3)}),
        (ipp.UNSUPPORTED_GROUP, {"print-scaling": ipp.keyword("none")})]))
    job = ipp.Printer(p.uri).print_job(b"%PDF", "x")
    assert job.ignored == ["print-scaling"]


def test_the_printer_and_job_are_asked_their_state(printer):
    def answer(msg):
        if msg.code == ipp.GET_PRINTER_ATTRIBUTES:
            return ok(ipp.PRINTER_GROUP, {
                "printer-state": ipp.enum(3),
                "printer-make-and-model": ipp.text("Brother MFC-L3760CDW series"),
                "media-source-supported": [ipp.keyword("auto"), ipp.keyword("manual")]})
        return ok(ipp.JOB_GROUP, {"job-state": ipp.enum(9),
                                  "job-impressions-completed": ipp.integer(1)})
    p = printer(answer)
    pr = ipp.Printer(p.uri)
    attrs = pr.attributes(["printer-state", "media-source-supported"])
    assert attrs["printer-state"] == [3]
    assert attrs["media-source-supported"] == ["auto", "manual"]
    assert p.requests[0].attributes("operation")["requested-attributes"] == [
        "printer-state", "media-source-supported"]
    js = pr.job(312)
    assert js["job-state"] == [9]
    assert p.requests[1].attributes("operation")["job-id"] == [312]


def test_an_unreachable_printer_is_an_error_naming_it():
    with pytest.raises(ipp.IppError, match=r"127\.0\.0\.1:9"):
        ipp.Printer("ipp://127.0.0.1:9/ipp/print", timeout=2).attributes(["printer-state"])


SHEET_REQUEST_ON_THE_WIRE = bytes.fromhex(
    "020000020000000101470012617474726962757465732d6368617273657400057574662d3848001b"
    "617474726962757465732d6e61747572616c2d6c616e67756167650002656e45000b7072696e7465"
    "722d757269001d6970703a2f2f3132372e302e302e313a3633312f6970702f7072696e7442001472"
    "657175657374696e672d757365722d6e616d65000374696d4200086a6f622d6e616d650009736865"
    "6574206a6f6249000f646f63756d656e742d666f726d6174000f6170706c69636174696f6e2f7064"
    "66023400096d656469612d636f6c00004a0000000c6d656469612d736f7572636544000000066d61"
    "6e75616c4a0000000a6d656469612d7479706544000000066c6162656c734a0000000a6d65646961"
    "2d73697a6534000000004a0000000b782d64696d656e73696f6e2100000004000052084a0000000b"
    "792d64696d656e73696f6e2100000004000074043700000000370000000044000d7072696e742d73"
    "63616c696e6700046e6f6e654400107072696e742d636f6c6f722d6d6f64650005636f6c6f724400"
    "05736964657300096f6e652d736964656403255044462d78")


def test_print_job_puts_exactly_the_same_bytes_on_the_wire(monkeypatch):
    """The sheet tool's request, byte for byte, as it was before print_document
    was split out of print_job (captured 2026-11-22)."""
    sent = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return ipp.encode_response(0, 1, [(ipp.JOB_GROUP, {
                "job-id": ipp.integer(1), "job-state": ipp.enum(3)})])

    monkeypatch.setattr(ipp.urllib.request, "urlopen",
                        lambda req, timeout=0: (sent.append(req.data), Reply())[1])
    ipp.Printer("ipp://127.0.0.1:631/ipp/print").print_job(b"%PDF-x", "sheet job", "tim")
    assert sent == [SHEET_REQUEST_ON_THE_WIRE]


def test_a_resolution_is_two_ints_and_a_unit_byte():
    """RFC 8010 3.9: resolution is SIGNED-INTEGER x, SIGNED-INTEGER y,
    SIGNED-BYTE units (3 = dots per inch)."""
    got = ipp.encode_attribute("printer-resolution", ipp.resolution(300, 600))
    assert got == (b"\x32\x00\x12printer-resolution\x00\x09"
                   + struct.pack(">iib", 300, 600, 3))


def test_print_document_sends_the_attributes_it_is_given(printer):
    p = printer(lambda msg: ok(ipp.JOB_GROUP, {"job-id": ipp.integer(5),
                                               "job-state": ipp.enum(3)}))
    job = ipp.Printer(p.uri).print_document(b"%PDF-roll", "rpi-hwid labels",
                                            {"print-scaling": ipp.keyword("none")})
    assert job.id == 5
    (msg,) = p.requests
    assert msg.data == b"%PDF-roll"
    assert msg.attributes("job") == {"print-scaling": ["none"]}
    assert msg.attributes("operation")["requesting-user-name"] == ["rpi-hwid"]
