"""Labels for the software-defined radios ``rpi_hwid.sdr`` finds.

One design for every radio, in the FPGA label's idiom: the identity's QR
top left, the maker's mark over the radio's name beside it, and under the
name the radio's chips and its FPGA, each in a captioned row ("radio",
"FPGA"). The identity itself runs along the foot. What makes a radio a
radio is drawn rather than written: a frequency-coverage bar on a log scale
(100 kHz to 10 GHz) with the receive range solid and the transmit range
hatched under it, and one antenna glyph per channel, in a group per
direction captioned with its count ("RX x2", "TX x2"). A receive antenna is
solid with an arrow coming into it, a transmit antenna open with waves
leaving it -- solid and open, as the two bars are. A bracket joins channels
that share a clock, as a KrakenSDR's five do.

Every fact on a label is either what the radio said of itself (a Pluto's
IIO context gives its ranges, rates, variant and reference clock) or what
its datasheet says of the model the probe identified. The record carries
which: ``SdrLabel.provenance`` names, for each fact, "read: ..." or the
page it was taken from. Nothing on these labels is a guess, and a radio
that cannot be identified that far is refused, not printed vaguely.

Identity is the rule the FPGA labels keep: only a value that belongs to
this unit and does not change. Where the unit has one -- a Pluto's serial,
which is its QSPI flash's unique id -- it is the QR code and the foot.
Where the hardware carries a serial that every unit shares (a KrakenSDR's
channels are 1000-1004 on every KrakenSDR; an RTL2832U dongle ships as
00000001), the foot prints it marked "not unique" and the QR's place says
so, because a code there would claim an identity the sticker does not
have. A radio whose unique id exists but was not read is refused, naming
the host and the command that reads it. An FPGA board is keyed on its
die's Device DNA; neither FPGA radio lets the host read one (see
DNA_NOT_READABLE), so each is keyed on its flash, as an FPGA board with no
DNA is, and its foot says the DNA was not readable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from reportlab.lib.colors import HexColor, black, white

from rpi_hwid import labels as lb

# --- what each model is, and where that was read -----------------------------
#
# Each fact is (value, source). A source is the page the value was quoted
# from; the probe's own readings override these wherever it has them.

KRAKEN_DOCS = ("https://github.com/krakenrf/krakensdr_docs/wiki/01.-Product-Overview "
               "(\"Radio Tuner: 5x R820T2\", \"ADC Bit Depth: 8-bits\", \"Frequency "
               "Range: 24 MHz -1766 MHz\", \"Maximum Channel Bandwidth: 2.56 MHz\", "
               "\"Oscillator Stability: 1PPM\", \"1x single clock source for all "
               "RTL-SDRs\", \"1x noise source\")")
KRAKEN_SERIALS = ("https://github.com/krakenrf/heimdall_daq_fw util/kerberos_eeprom_init.sh "
                  "(serial=$((i+1000))) and every shipped daq_chain_config.ini "
                  "(ctr_channel_serial_no = 1000)")
RTL_V3_DOCS = ("https://www.rtl-sdr.com/wp-content/uploads/2018/02/RTL-SDR-Blog-V3-"
               "Datasheet.pdf (\"Bandwidth: Up to 2.4 MHz stable\", \"ADC: RTL2832U "
               "8-bits\", \"Frequency Range: 500 kHz - 1766 MHz (500 kHz - 24 MHz in "
               "direct sampling mode)\", \"The V3 uses a 1PPM TCXO\", \"R820T2\")")
PLUTO_DOCS = ("https://wiki.analog.com/university/tools/pluto/devs/specs (\"Integrated "
              "12-bit DACs (Tx) and ADCs (Rx)\", \"1 Transmit, 1 Receive channel\", "
              "\"Xilinx Zynq XC7Z010-1CLG225C\")")
XSDR_DOCS = ("https://www.crowdsupply.com/wavelet-lab/xsdr (\"LMS7002M\", \"AMD Artix-7 "
             "XC7A50T\", \"30 MHz - 3.8 GHz tuning range\", \"0.1 MSPS - 122.88 MSPS "
             "(SISO)\", \"2x2 MIMO RX / TX channels\", \"Clock stability: 0.5 PPM\")")
PLUTO_PORTS = ("a Rev.B brings out one RX and one TX, on its two SMAs; Rev.C adds two "
               "u.FL for the second pair "
               "(https://wiki.analog.com/university/tools/pluto/hacking/hardware)")
XSDR_PORTS = ("https://github.com/wavelet-lab/docs source/hardware/xsdr.rst (\"2 receive "
              "and 2 transmit channels\"; its pinout, xSDR_pinout_ed.png, names the four "
              "MHF4 ports RXA, RXB, TXA, TXB)")
LMS7002M_DOCS = ("https://limemicro.com/technology/lms7002m/ (\"12-bit ADCs and DACs\")")


@dataclass(frozen=True)
class Model:
    """A radio model as its maker documents it."""

    maker: str
    mark: str | None          # artwork file, or None: the maker's name in type
    title: str
    radio: str                # the radio's chips: the transceiver, or tuner + demodulator
    rx: tuple[tuple[int, int], ...]        # receive bands, Hz
    rx_aux: tuple[tuple[int, int], ...]    # a secondary receive path (direct sampling)
    tx: tuple[tuple[int, int], ...]
    rx_channels: int
    tx_channels: int
    rate: str                 # the widest the radio streams, as its maker says it
    adc_bits: int
    clock: str | None
    coherent: bool = False
    noise_source: bool = False
    fpga: str | None = None   # the FPGA die, where the radio has one
    sources: tuple[tuple[str, str], ...] = ()


MODELS = {
    "krakensdr": Model(
        maker="KrakenRF", mark="krakenrf.png", title="KrakenSDR",
        radio="5 × R820T2 + RTL2832U",
        rx=((24_000_000, 1_766_000_000),), rx_aux=(), tx=(), rx_channels=5, tx_channels=0,
        rate="2.56 MHz", adc_bits=8, clock="1 ppm, one clock for all five",
        coherent=True, noise_source=True,
        sources=(("model", "read: five RTL2832U on one hub, serials 1000-1004; "
                  + KRAKEN_SERIALS), ("tuner, range, bandwidth, ADC, clock, noise source",
                                      KRAKEN_DOCS))),
    "rtl-sdr-blog-v3": Model(
        maker="RTL-SDR Blog", mark=None, title="RTL-SDR V3", radio="R820T2 + RTL2832U",
        rx=((24_000_000, 1_766_000_000),), rx_aux=((500_000, 24_000_000),), tx=(),
        rx_channels=1, tx_channels=0, rate="2.4 MHz", adc_bits=8, clock="TCXO 1 ppm",
        sources=(("tuner, range, direct sampling, bandwidth, ADC, clock", RTL_V3_DOCS),)),
    "pluto": Model(
        maker="Analog Devices", mark="analog-devices.svg", title="ADALM-Pluto",
        radio="AD9363", rx=(), rx_aux=(), tx=(), rx_channels=1, tx_channels=1,
        rate="", adc_bits=12, clock=None, fpga="XC7Z010",
        sources=(("channels, ADC, SoC", PLUTO_DOCS),)),
    "xsdr": Model(
        maker="Wavelet Lab", mark="wavelet-lab.png", title="XSDR", radio="LMS7002M",
        rx=((30_000_000, 3_800_000_000),), rx_aux=(), tx=((30_000_000, 3_800_000_000),),
        rx_channels=2, tx_channels=2, rate="122.88 MS/s", adc_bits=12,
        clock="0.5 ppm", fpga="XC7A50T",
        sources=(("chip, FPGA, range, rate, channels, clock", XSDR_DOCS),
                 ("ADC", LMS7002M_DOCS))),
}

# Serials a unit is shipped with that every other unit shares, and why.
SHARED_SERIALS = {
    "00000001": "the RTL2832U EEPROM's factory default",
}

# What reads a radio's unique identifier, for the refusal when it is missing.
IDENT_READ_WITH = {
    "usdr": ("the XSDR's configuration flash ESN (the AT25SL321's secured OTP) with "
             "`rpi-hwid collect --sdr-open HOST`, which needs the card free: nothing "
             "holding /dev/usdr0"),
}


# The 7-series IDCODEs a usdr card's image names, revision bits masked.
# 0362c093 is the XC7A50T (UG470 Table 1-1); usdr_flash refuses an image
# whose DEVID is not the die's ("FPGA Devid mismatch"), so the golden
# image's DEVID is the die.
XILINX_DEVID = {0x362C093: "XC7A50T", 0x362D093: "XC7A35T", 0x3631093: "XC7A100T"}


class RadioNotIdentifiedError(Exception):
    """A radio reached the label generator known only by its bus id: what it
    is, and so what it can do, was never read. Printing a guess would put a
    frequency range on a sticker that nobody measured."""


@dataclass(frozen=True)
class SdrLabel:
    """What an SDR's label prints."""

    kind: str
    host: str
    maker: str
    mark: str | None
    title: str
    radio: str                        # "AD9363", "R820T2 + RTL2832U": captioned "radio"
    fpga: str | None                  # "XC7Z010": captioned "FPGA"; None where there is none
    variant: str | None               # the board revision, beside the title: "Rev.B"
    rx: tuple[tuple[int, int], ...]
    rx_aux: tuple[tuple[int, int], ...]
    tx: tuple[tuple[int, int], ...]
    rx_channels: int
    tx_channels: int
    facts: tuple[str, ...]            # the two short lines beside the glyphs
    coherent: bool = False
    noise_source: bool = False
    ident: str | None = None          # this unit's own identifier: the QR and the foot
    ident_caption: str = "serial"
    shared: str | None = None         # a serial every unit carries, printed as such
    shared_caption: str = ""
    provenance: tuple[tuple[str, str], ...] = ()
    # the configuration flash, as an FPGA label prints it
    flash: str | None = None           # "Atmel AT25SL321  ·  4 MiB"
    flash_uid: str | None = None
    # why an FPGA radio is keyed on its flash and not on its die, as the foot
    # says it: see DNA_NOT_READABLE
    dna_note: str | None = None


def mhz(hz: int) -> str:
    """325000000 -> '325 MHz', 3800000000 -> '3.8 GHz', 500000 -> '500 kHz'."""
    for unit, scale in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if hz >= scale:
            return f"{hz / scale:.4g} {unit}"
    return f"{hz} Hz"


def rate_text(hz: int) -> str:
    return f"{hz / 1e6:.4g} MS/s"


def model_key(r) -> str | None:
    """Which entry of MODELS a probed radio is, or None."""
    if r.kind == "krakensdr":
        return "krakensdr"
    if r.kind == "pluto":
        return "pluto"
    if r.kind == "usdr" and r.usdr_family == "m2_lm7_1" and r.usdr_hwid:
        # xsdr_ctrl.h: XSDR_DEV = 0x30 in HWID bits 23:16
        return "xsdr" if (int(r.usdr_hwid, 16) >> 16) & 0xFF == 0x30 else None
    if r.kind == "rtl-sdr" and r.rtl_model == "rtl-sdr-blog-v3":
        return "rtl-sdr-blog-v3"
    return None


def sdr_records(docs):
    """One record per radio across all documents."""
    out = []
    for host in sorted(docs):
        for r in docs[host].summary.sdr:
            if r.usdr_error:
                raise RadioNotIdentifiedError(f"{host}: the {r.kind} card at "
                                              f"{r.pcie_id}: {r.usdr_error}.")
            key = model_key(r)
            if key is None:
                raise RadioNotIdentifiedError(
                    f"{host}: a {r.kind} radio ({r.vidpid or r.pcie_id}) is known only by "
                    "its bus id, so its label would carry capabilities nobody read. "
                    + NOT_IDENTIFIED.get(r.kind, ""))
            out.append(record(host, key, r))
    return out


NOT_IDENTIFIED = {
    "usdr": ("Its HWID register says which LMS7002M card it is (0x30 in bits 23:16 "
             "is an XSDR): read it with `rpi-hwid collect --sdr-open HOST` (the card "
             "must be free: nothing holding /dev/usdr0)."),
    "rtl-sdr": ("An RTL2832U's EEPROM strings are the same on most dongles; its tuner and "
                "its hardware (a Blog V3's HF path into the Q branch) are read with "
                "`rpi-hwid collect --sdr-open HOST` while nothing holds the dongle (stop "
                "readsb or OpenWebRX for it, and start it again after)."),
}


# Why neither FPGA radio is keyed on its die's Device DNA, as an FPGA board is.
# A 7-series die gives its DNA to two readers only: the JTAG FUSE_DNA
# instruction, and a DNA_PORT primitive in the fabric (UG470, "Device DNA").
# Measured on the fleet's two, 2026-09-27:
DNA_NOT_READABLE = {
    "pluto": (
        "not read: the Zynq's PS has no path to the PL's DNA -- only JTAG or a "
        "DNA_PORT in the fabric reach it -- and the stock Pluto gateware "
        "(analogdevicesinc/hdl projects/pluto, and the whole library) instantiates no "
        "DNA_PORT; the Pluto's JTAG is on its board pads, not on USB. A temporary "
        "DNA_PORT design bit-banged over EMIO GPIO (built with openXC7, loaded to SRAM "
        "through the Pluto's fpga_manager and proven live by its EMIO loopback) read "
        "all zeros, and a pattern shifted in on DIN never reached DOUT, so that open "
        "toolchain's DNA_PORT connection on the xc7z010 is not trusted; a DNA read "
        "needs the same design built by Vivado"),
    "xsdr": (
        "not read: the usdr gateware has no DNA register (no DNA_PORT in "
        "wavelet-lab/usdr-fpga, no DNA read in usdr-lib), and the card's JTAG is on "
        "M.2 E-key pins 17/19/21/23, which the host's M.2 adapter does not carry (the "
        "Wavelet Lab B+M-to-E adapter joins them to its M-key side only through "
        "resistors R1/R5/R6/R7) -- no JTAG cable can reach it in place. Nor can gateware "
        "be loaded into SRAM without JTAG: over PCIe the card can only be reflashed"),
}
DNA_NOTE = "Device DNA not readable"


def record(host, key, r):
    m = MODELS[key]
    prov = list(m.sources)
    rx, tx, rx_aux = m.rx, m.tx, m.rx_aux
    rx_ch, tx_ch, bits, rate = m.rx_channels, m.tx_channels, m.adc_bits, m.rate
    radio, clock, fpga = m.radio, m.clock, m.fpga
    variant = ident = shared = flash = flash_uid = dna_note = None
    ident_caption, shared_caption = "serial", ""
    if key == "pluto":
        # the radio's own word, over the datasheet's, wherever it gave one
        if r.rx_lo_hz:
            rx = (r.rx_lo_hz,)
            prov.append(("RX range", f"read: {r.iio_uri} ad9361-phy altvoltage0 "
                                     "frequency_available"))
        if r.tx_lo_hz:
            tx = (r.tx_lo_hz,)
            prov.append(("TX range", f"read: {r.iio_uri} ad9361-phy altvoltage1 "
                                     "frequency_available"))
        if r.rx_rate_hz:
            rate = rate_text(r.rx_rate_hz[1])
            prov.append(("rate", f"read: {r.iio_uri} voltage0 sampling_frequency_available"))
        if r.rx_channels:
            rx_ch, tx_ch = r.rx_channels, r.tx_channels or 0
            prov.append(("channels", f"read: {r.iio_uri} cf-ad9361-lpc scan elements "
                                     "(I and Q, two to a channel). The AD9363 itself is "
                                     "2 x 2; " + PLUTO_PORTS))
        if r.adc_bits:
            bits = r.adc_bits
            prov.append(("ADC", f"read: {r.iio_uri} cf-ad9361-lpc format"))
        if r.rf_chip:
            radio = r.rf_chip.upper().rstrip("A") if r.rf_chip.lower().startswith(
                "ad936") else r.rf_chip
            prov.append(("radio", f"read: {r.iio_uri} context ad9361-phy,model = "
                                  f"{r.rf_chip}"))
        if r.xo_hz:
            clock = f"{r.xo_hz / 1e6:g} MHz ref"
            prov.append(("clock", f"read: {r.iio_uri} context ad9361-phy,xo_correction"))
        if r.hw_model and "Rev." in r.hw_model:
            variant = "Rev." + r.hw_model.split("Rev.", 1)[1].split()[0]
        # "Analog Devices PlutoSDR Rev.B (Z7010-AD9363A)": the die, as it says
        die = re.search(r"\(Z(70\d\d)-", r.hw_model or "")
        if die:
            fpga = "XC7Z" + die.group(1)[1:]
            prov.append(("FPGA", f"read: {r.iio_uri} context hw_model = {r.hw_model}"))
        ident = r.hw_serial or r.usb_serial
        ident_caption = "serial  (QSPI flash unique id)"
        flash_uid = r.flash_uid
        prov.append(("identity", "read: USB iSerial and IIO hw_serial, which ADI's "
                                 "board/pluto/S23udc both set from the kernel's "
                                 "SPI-NOR-UniqueID"))
    elif key == "krakensdr":
        if r.tuner:
            prov.append(("tuner, read", f"read: rtl_eeprom on all five channels: {r.tuner} "
                                        "(librtlsdr's name for R820T and R820T2 alike: the "
                                        "two answer the same chip id)"))
        shared = "\u2013".join((r.channel_serials[0], r.channel_serials[-1]))
        shared_caption = "channel serials  \u00b7  the same on every KrakenSDR: not unique"
    elif key == "rtl-sdr-blog-v3":
        prov.append(("model", "read: tuner Rafael Micro R820T (rtl_eeprom), and the "
                              "direct-sampling Q branch carrying signal where the I branch "
                              "does not, the V3's hardware HF path (" + RTL_V3_DOCS[:77] +
                              ")"))
        if r.usb_serial in SHARED_SERIALS:
            shared = r.usb_serial
            shared_caption = ("USB serial  \u00b7  " + SHARED_SERIALS[r.usb_serial]
                              + ": not unique")
        else:
            ident, ident_caption = r.usb_serial, "USB serial  (EEPROM)"
    elif key == "xsdr":
        # keyed on the flash uid, as an FPGA board with no die id is
        ident = flash_uid = r.flash_uid if r.flash_uid_state == "read" else None
        ident_caption = "flash uid"
        flash = lb.flash_text(lb.flash_from_jedec(r.flash_jedec))
        if flash:
            prov.append(("flash", f"read: JEDEC {r.flash_jedec} through usdr's espi core, "
                                  "decoded by rpi_hwid.labels.flash_from_jedec"))
        if ident:
            prov.append(("identity", "read: the configuration flash's secured-OTP ESN, "
                                     f"{r.flash_uid_note}; Renesas DS-AT25SL321-112 Rev. K "
                                     "8.41 and Table 17 (\"128-bit ESN (Electrical Serial "
                                     "Number)\")"))
        prov.append(("model", f"read: HWID {r.usdr_hwid} (bits 23:16 = 0x30, XSDR_DEV in "
                              "usdr-lib src/lib/device/m2_lm7_1/xsdr_ctrl.h)"))
        prov.append(("channels", XSDR_PORTS))
        if r.fpga_devid:
            part = XILINX_DEVID.get(int(r.fpga_devid, 16) & 0x0FFFFFFF)
            if part:
                fpga = part
                prov.append(("FPGA", f"read: the golden image's DEVID {r.fpga_devid}, "
                                     "which usdr_flash checks against the die"))
    if key in DNA_NOT_READABLE:
        dna_note = DNA_NOTE
        prov.append(("Device DNA", DNA_NOT_READABLE[key]))
    facts = ("  \u00b7  ".join(x for x in (rate, f"{bits}-bit") if x), clock or "")
    if flash:
        # the flash row takes the second line, so the clock joins the first
        facts = ("  \u00b7  ".join(x for x in (rate, f"{bits}-bit", clock) if x), "")
    return SdrLabel(
        kind=key, host=host, maker=m.maker, mark=m.mark, title=m.title, radio=radio,
        fpga=fpga, variant=variant, rx=tuple(rx),
        rx_aux=tuple(rx_aux), tx=tuple(tx), rx_channels=rx_ch, tx_channels=tx_ch,
        facts=facts, coherent=m.coherent, noise_source=m.noise_source, ident=ident,
        ident_caption=ident_caption, shared=shared, shared_caption=shared_caption,
        provenance=tuple(prov), flash=flash, flash_uid=flash_uid, dna_note=dna_note)


def unreadable(r: SdrLabel) -> str | None:
    """Why this label may not be printed, or None."""
    if r.ident or r.shared:
        return None
    return (f"{r.host}: the {r.title} has a unique identifier and it was never read, so "
            "its label would carry nothing that identifies it. Read "
            + IDENT_READ_WITH.get(r.kind, "it") + " and collect again.")


# --- drawing -------------------------------------------------------------------

AXIS_LO, AXIS_HI = 1e5, 1e10          # 100 kHz to 10 GHz, five decades
AXIS_TICKS = ((1e5, "100k"), (1e6, "1M"), (1e7, "10M"), (1e8, "100M"), (1e9, "1G"),
              (1e10, "10G"))
LIGHT = HexColor("#9a9a9a")


def axis_x(x0: float, w: float, hz: float) -> float:
    return x0 + w * (math.log10(hz) - math.log10(AXIS_LO)) / (
        math.log10(AXIS_HI) - math.log10(AXIS_LO))


def hatch(lab, x, y, w, h, pitch):
    """A box of diagonal hatching under a keyline: transmit, as against the
    solid receive bar, so the two read apart in monochrome."""
    c = lab.c
    px, py = lab.pt(x, y + h)
    c.saveState()
    p = c.beginPath()
    p.rect(px, py, w, h)
    c.clipPath(p, stroke=0, fill=0)
    c.setLineWidth(0.45)
    c.setStrokeColor(black)
    k = -h
    while k < w:
        c.line(px + k, py, px + k + h, py + h)
        k += pitch
    c.restoreState()
    c.setLineWidth(0.5)
    c.rect(px, py, w, h, stroke=1, fill=0)


def coverage(lab, x0, y, w, r):
    """The frequency-coverage band: RX solid, the auxiliary receive path
    (direct sampling) grey, TX hatched, over a log axis with decade ticks.
    Returns the height used."""
    c = lab.c
    bar = 1.7 * lb.mm
    gap = 0.5 * lb.mm
    rows = [("RX", r.rx, r.rx_aux)] + ([("TX", r.tx, ())] if r.tx else [])
    cap_w = 3.6 * lb.mm
    ax0, aw = x0 + cap_w, w - cap_w
    for i, (cap, bands, aux) in enumerate(rows):
        by = y + i * (bar + gap)
        lb.Label.text(lab, x0, by + (bar - 5 * 0.72) / 2, cap, lb.SANS_BOLD, 5, color=lb.GREY)
        for lo, hi in aux:
            px, py = lab.pt(axis_x(ax0, aw, lo), by + bar)
            c.setFillColor(LIGHT)
            aw_ = axis_x(ax0, aw, hi) - axis_x(ax0, aw, lo)
            c.rect(px, py, aw_, bar, stroke=0, fill=1)
            # what the grey is: the direct-sampling path, where it fits
            atext = "HF direct sampling"
            if lab.width(atext, lb.SANS_BOLD, 4.5) <= aw_ - 1 * lb.mm:
                lb.Label.text(lab, axis_x(ax0, aw, lo) + aw_ / 2,
                              by + (bar - 4.5 * 0.72) / 2, atext, lb.SANS_BOLD, 4.5,
                              align="centre", color=white)
        for lo, hi in bands:
            x1, x2 = axis_x(ax0, aw, lo), axis_x(ax0, aw, hi)
            if cap == "RX":
                px, py = lab.pt(x1, by + bar)
                c.setFillColor(black)
                c.rect(px, py, x2 - x1, bar, stroke=0, fill=1)
            else:
                hatch(lab, x1, by, x2 - x1, bar, 0.9 * lb.mm)
            # the range in words, beside the bar where there is room, else inside it
            text = f"{mhz(lo)} – {mhz(hi)}"
            size = 5
            tw = lab.width(text, lb.SANS, size)
            # left of the bar only where no auxiliary band sits there
            left_end = min([x1] + [axis_x(ax0, aw, lo) for lo, _hi in aux])
            if ax0 + aw - x2 >= tw + 0.8 * lb.mm:
                lb.Label.text(lab, x2 + 0.8 * lb.mm, by + (bar - size * 0.72) / 2, text,
                             lb.SANS, size)
            elif left_end - ax0 >= tw + 0.8 * lb.mm:
                lb.Label.text(lab, left_end - 0.8 * lb.mm, by + (bar - size * 0.72) / 2,
                             text, lb.SANS, size, align="right")
            elif cap == "RX":
                lb.Label.text(lab, (x1 + x2) / 2, by + (bar - size * 0.72) / 2, text,
                             lb.SANS_BOLD, size, align="centre", color=white)
        c.setFillColor(black)
    # the axis: a hairline under the bars, decade ticks and their names
    ay = y + len(rows) * (bar + gap)
    px, py = lab.pt(ax0, ay)
    c.setStrokeColor(lb.GREY)
    c.setLineWidth(0.4)
    c.line(px, py, px + aw, py)
    for hz, name in AXIS_TICKS:
        tx = axis_x(ax0, aw, hz)
        tpx, tpy = lab.pt(tx, ay)
        c.line(tpx, tpy, tpx, tpy - 0.6 * lb.mm)
        align = "left" if hz == AXIS_LO else "right" if hz == AXIS_HI else "centre"
        lb.Label.text(lab, tx, ay + 0.8 * lb.mm, name, lb.SANS, 4.5, align=align, color=lb.GREY)
    c.setStrokeColor(black)
    return ay + 0.8 * lb.mm + 4.5 * 0.72 - y


GLYPH_W = 0.8             # an antenna glyph's width, in glyph heights


def antenna(lab, x, y, size, rx):
    """One channel: an antenna (a triangle on a mast) on its connector.

    Receive and transmit differ three ways, so that no one of them has to
    carry the distinction alone and all three survive a mono laser: a
    receive antenna is solid black and a transmit one open, as the RX bar is
    solid and the TX bar hatched; a receive antenna has an arrow coming
    down into it, a transmit one has waves radiating from its tip; and each
    group is captioned "RX" or "TX" under it (see channels). `size` is the
    glyph's height; it is GLYPH_W * size wide."""
    c = lab.c
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineCap(1)
    c.setLineJoin(1)
    lw = size * 0.075
    c.setLineWidth(lw)
    mx, top = lab.pt(x + size * 0.24, y + size * 0.2)
    arm = size * 0.2
    foot = lab.pt(0, y + size * 0.52)[1]          # the triangle's point, on the mast
    body = lab.pt(0, y + size * 0.76)[1]          # the top of the connector
    p = c.beginPath()
    p.moveTo(mx - arm, top)
    p.lineTo(mx + arm, top)
    p.lineTo(mx, foot)
    p.close()
    c.drawPath(p, stroke=1, fill=1 if rx else 0)
    c.line(mx, foot, mx, body)
    # the connector: a nut over a barrel, solid for RX and open for TX
    nut_w, nut_h = size * 0.36, size * 0.12
    bar_w = size * 0.2
    base = lab.pt(0, y + size)[1]
    c.rect(mx - nut_w / 2, body - nut_h, nut_w, nut_h, stroke=0 if rx else 1,
           fill=1 if rx else 0)
    c.rect(mx - bar_w / 2, base, bar_w, body - nut_h - base, stroke=0 if rx else 1,
           fill=1 if rx else 0)
    ax = mx + size * 0.42
    if rx:
        # an arrow coming down, into the antenna
        a_top, a_bot = lab.pt(0, y)[1], lab.pt(0, y + size * 0.6)[1]
        head = size * 0.18
        c.setLineWidth(size * 0.08)
        c.line(ax, a_top, ax, a_bot + head * 0.6)
        p = c.beginPath()
        p.moveTo(ax, a_bot)
        p.lineTo(ax - head * 0.6, a_bot + head)
        p.lineTo(ax + head * 0.6, a_bot + head)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    else:
        # waves radiating from the antenna's tip
        c.setLineWidth(size * 0.07)
        cy = top - size * 0.04
        for r in (size * 0.26, size * 0.42):
            c.arc(mx + arm * 0.2 - r, cy - r, mx + arm * 0.2 + r, cy + r, -45, 90)
    c.setLineCap(0)
    c.setLineJoin(0)
    c.setLineWidth(1)
    return size * GLYPH_W


def noise_glyph(lab, x, y, size):
    """The calibration noise source: a boxed zig-zag."""
    c = lab.c
    px, py = lab.pt(x, y + size)
    w = size * 0.7
    c.setStrokeColor(black)
    c.setLineWidth(size * 0.07)
    c.rect(px, py + size * 0.2, w, size * 0.6, stroke=1, fill=0)
    pts = [(0.1, 0.5), (0.22, 0.72), (0.34, 0.3), (0.46, 0.68), (0.58, 0.36), (0.7, 0.62),
           (0.82, 0.45), (0.9, 0.5)]
    p = c.beginPath()
    for i, (fx, fy) in enumerate(pts):
        (p.moveTo if i == 0 else p.lineTo)(px + w * fx, py + size * 0.2 + size * 0.6 * fy)
    c.drawPath(p, stroke=1, fill=0)
    c.setLineWidth(1)
    return w


def channel_groups(r):
    """[(direction, count)], receive first; a direction with no channels has
    no group."""
    return [(d, n) for d, n in (("RX", r.rx_channels), ("TX", r.tx_channels)) if n]


def group_caption(direction, n):
    return f"{direction} \u00d7{n}"


GROUP_CAPTION = 5.5       # points: the "RX x2" under each group


def channels(lab, x, y, size, r):
    """A group of glyphs per direction, receive then transmit, each
    captioned under it with its direction and count, then the noise source.
    Channels that share one clock (a KrakenSDR's five) have their caption
    set in a bracket that spans them. Returns the width."""
    c = lab.c
    at = x
    pitch = size * GLYPH_W + 0.5 * lb.mm
    cap_y = y + size + 0.5 * lb.mm
    for gi, (direction, n) in enumerate(channel_groups(r)):
        if gi:
            at += 1.6 * lb.mm                     # the gap between the groups
        first = at
        for _ in range(n):
            antenna(lab, at, y, size, direction == "RX")
            at += pitch
        last = at - pitch + size * GLYPH_W        # the group's right edge
        caption = group_caption(direction, n)
        mid = (first + last) / 2
        tw = lab.width(caption, lb.SANS_BOLD, GROUP_CAPTION)
        lb.Label.text(lab, mid, cap_y, caption, lb.SANS_BOLD, GROUP_CAPTION, align="centre")
        if direction == "RX" and r.coherent and n > 1:
            # the bracket: ticks up at each end, its bar broken by the caption
            by = cap_y + GROUP_CAPTION * 0.72 / 2
            x1, x2 = first + size * 0.04, last - size * 0.04
            gap = tw / 2 + 0.6 * lb.mm
            c.setLineWidth(0.5)
            for a, b in ((x1, mid - gap), (mid + gap, x2)):
                pa, pb = lab.pt(a, by), lab.pt(b, by)
                c.line(pa[0], pa[1], pb[0], pb[1])
            for xe in (x1, x2):
                pa, pb = lab.pt(xe, by), lab.pt(xe, by - 0.6 * lb.mm)
                c.line(pa[0], pa[1], pb[0], pb[1])
            c.setLineWidth(1)
        at = max(at, mid + tw / 2 + 0.5 * lb.mm)
    if r.noise_source:
        at += 0.3 * lb.mm
        at += noise_glyph(lab, at, y, size) + 0.5 * lb.mm
    return at - x


def maker_mark(lab, r, x, y, h):
    path = lb.artwork(r.mark) if r.mark else None
    if path:
        box_w = h * 6
        lb.mark_in_box(lab, path, x, y, box_w, h, align="left")
        aspect = lb.mark_aspect(path)
        w = min(box_w, h / aspect)
        if aspect > 0.6:
            # a symbol with no wordmark: the maker's name beside it
            size = h * 0.55 / 0.72
            lab.text(x + w + 1.2 * lb.mm, y + (h - size * 0.72) / 2, r.maker, lb.SANS_BOLD,
                     size, color=lb.GREY)
            w += 1.2 * lb.mm + lab.width(r.maker, lb.SANS_BOLD, size)
        return w
    size = 2.4 * lb.mm / 0.72
    lab.text(x, y + h - 2.4 * lb.mm, r.maker, lb.SANS, size, color=lb.GREY)
    return lab.width(r.maker, lb.SANS, size)


def draw_sdr(lab, r):
    """The SDR label. Left: the identity's QR, or a square saying the unit
    has none. Right: the maker's mark, the radio's name with its board
    revision, then the radio's chips and its FPGA as captioned rows. Across:
    the coverage band. Then the channel groups, RX then TX, beside the rate,
    the ADC and the clock. Foot: the identity -- on an FPGA radio, saying
    that its Device DNA could not be read -- or the shared serial marked as
    not unique."""
    pad = lb.PAD
    qr = 13 * lb.mm
    if r.ident:
        lab.qr(pad, pad, qr, r.ident)
    else:
        lab.no_code(pad, pad, qr, "not unique")
    x = pad + qr + 2.5 * lb.mm
    col_w = lb.LABEL_W - pad - x
    mark_h = 4.2 * lb.mm
    maker_mark(lab, r, x, pad, mark_h)
    y = pad + mark_h + 0.7 * lb.mm
    title_size = 14
    variant_w = (lab.width(r.variant, lb.SANS, 7) + 1.5 * lb.mm) if r.variant else 0
    title_size = lab.fitted_size(r.title, lb.SANS_BOLD, title_size, col_w - variant_w)
    lab.fit(x, y, r.title, lb.SANS_BOLD, title_size, col_w - variant_w)
    if r.variant:
        # the board revision, beside the name, on the name's baseline
        tx = x + lab.width(r.title, lb.SANS_BOLD, title_size) + 1.5 * lb.mm
        lab.text(tx, y + (title_size - 7) * 0.72, r.variant, lb.SANS, 7, color=lb.GREY)
    y += title_size * 0.72 + 1.0 * lb.mm
    # the chips, each captioned as the FPGA label captions its rows: the
    # captions right-aligned in one column, the values in the next
    rows = [("radio", r.radio)] + ([("FPGA", r.fpga)] if r.fpga else [])
    cap_w = max(lab.width(cap, lb.SANS, lb.CAPTION) for cap, _v in rows)
    x_val = x + cap_w + lab.CAPTION_GAP
    for cap, val in rows:
        lab.captioned(x, x_val, y, cap, val, lb.SANS_BOLD, 7, lb.LABEL_W - pad - x_val,
                      min_size=5)
        y += 7 * 0.72 + 0.75 * lb.mm

    y = pad + qr + 1.3 * lb.mm
    y += coverage(lab, pad, y, lb.LABEL_W - 2 * pad, r) + 1.0 * lb.mm

    glyph = 4.2 * lb.mm
    used = channels(lab, pad + 0.3 * lb.mm, y, glyph, r)
    fx = pad + used + 2 * lb.mm
    fw = lb.LABEL_W - pad - fx
    lab.fit(fx, y + 0.2 * lb.mm, r.facts[0], lb.SANS_BOLD, 7, fw, min_size=5)
    y2 = y + 0.2 * lb.mm + 7 * 0.72 + 1.0 * lb.mm
    if r.flash:
        # the FPGA label's flash row: the part and its size set flush right,
        # the grey caption after them
        cap_x = lb.LABEL_W - pad - lab.width("flash", lb.SANS, lb.CAPTION)
        lab.captioned_after(fx, cap_x, y2, "flash", r.flash, lb.SANS, 6.5, min_size=5)
    else:
        lab.fit(fx, y2, r.facts[1], lb.SANS, 6.5, fw, min_size=5)

    # the foot: the identifier, or the shared serial and why it identifies nothing
    size = 8
    foot = lb.LABEL_H - pad - size * 0.72
    cap_y = foot - 0.7 * lb.mm - lb.CAPTION * 0.72
    if r.ident:
        caption = r.ident_caption + (f"  \u00b7  {r.dna_note}" if r.dna_note else "")
        lab.fit(pad, cap_y, caption, lb.SANS, lb.CAPTION, lb.LABEL_W - 2 * pad,
                min_size=4.5, color=lb.GREY)
        lab.fit(pad, foot, r.ident, lb.MONO, size, lb.LABEL_W - 2 * pad, min_size=5.5)
    else:
        lab.fit(pad, cap_y, r.shared_caption, lb.SANS, lb.CAPTION, lb.LABEL_W - 2 * pad,
                min_size=4.5, color=lb.NOTE)
        lab.fit(pad, foot, r.shared or "", lb.MONO, size, lb.LABEL_W - 2 * pad)
