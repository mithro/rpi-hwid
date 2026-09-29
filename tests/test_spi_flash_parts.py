"""SPI NOR parts by JEDEC id, as the spiflash package names them."""

from __future__ import annotations

import pytest

from rpi_hwid import spi_flash_parts as db


@pytest.mark.parametrize(("jedec", "name", "vendor", "size"), [
    # every flash id met on the fleet, and what spiflash's tables make of it
    (0xC84016, "GD25Q32x", "GigaDevice", 4 << 20),        # GD25Q32, GD25Q32C
    (0x684016, "Bx25Q32xS", "Boya", 4 << 20),             # B.25Q32BS, BY25Q32CS/ES
    (0x204016, "XM25QH32x", "XMC", 4 << 20),              # XM25QH32C, XM25QH32D
    (0xC22017, "MX25L64xxx", "Macronix", 8 << 20),        # MX25L6405 ... MX25L6473F
    (0x20BA18, "MT25QL128/N25Q128xxx", "Micron", 16 << 20),
    (0x012018, "S25xx12xxx", "Spansion", 16 << 20),
    (0x010219, "S25Fx256S", "Spansion", 32 << 20),       # S25FL256S, S25FS256S
    (0x1F4216, "AT25xL321", "Atmel", 4 << 20),            # AT25SL321, AT25QL321
    (0xEF4016, "W25Q32xx", "Winbond", 4 << 20),
    (0xEF4017, "S25FL064K/W25Q64xx", "Winbond", 8 << 20),  # and Spansion's rebadge
    (0xEF4018, "W25Q128xx", "Winbond", 16 << 20),
    (0xC84014, "GD25Q80", "GigaDevice", 1 << 20),
    (0xC84017, "GD25Q64x", "GigaDevice", 8 << 20),
    (0xC84018, "GD25x12xx", "GigaDevice", 16 << 20),
    (0xC22019, "MX25L256x5x", "Macronix", 32 << 20),
    # an Atmel capacity byte is not log2 of the size; spiflash gives it
    (0x1F4701, "AT25DF321x", "Atmel", 4 << 20),
    # ids spiflash does not list: the ESP32 module's 0x464016
    # (mithro/spiflash#1), and one nobody has made
    (0x464016, None, None, None),
    (0x5A1018, None, None, None),
])
def test_a_jedec_id_looks_up_a_name_a_vendor_and_a_size(jedec, name, vendor, size):
    assert (db.name(jedec), db.vendor(jedec), db.size(jedec)) == (name, vendor, size)


def test_an_id_can_be_given_as_text_and_junk_is_no_id():
    assert db.name("0xc84016") == db.name("c84016") == db.name(0xC84016)
    for junk in ("junk", 0, "0x000000", 0x1000000):
        assert db.flashes(junk) == []
        assert db.name(junk) is None


def test_a_spi_nand_part_is_never_a_nor_flashs_name():
    """A SPI NAND id is two bytes, so spiflash finds the MX35LF2G14AC's c220
    inside the NeTV2's c22017 -- a 256 MiB NAND is not its flash."""
    import spiflash

    assert any(f.type == "nand" for f in spiflash.lookup("c22017"))
    assert [f.type for f in db.flashes(0xC22017)] == ["nor"]
    assert "MX35" not in (db.name(0xC22017) or "")


def test_an_extended_id_narrows_the_parts_where_a_source_keyed_on_it():
    # an FS-S answer rules out every FL-S entry that carries its own bytes
    assert db.name(0x010219, "0x4d0181") == "S25Fx256S"
    assert db.name(0x010219, "0x4d0180") == "S25FL256S"
    # bytes that are not bytes settle nothing
    assert db.name(0x010219, "junk") == db.name(0x010219)


@pytest.mark.parametrize(("names", "jedec", "parts"), [
    # flashrom's order-code padding: S25FL128S plus the package, grade and
    # model-number letters it leaves open
    (["S25FL128S......0", "S25FL128S......1"], 0x012018, {"S25FL128S"}),
    # variant tags: sector layouts, supply, interface modes
    (["S25FL128S_UL", "S25FL128S_US", "N25Q128_1_8V", "W25Q256JV_Q"], 0x012018,
     {"S25FL128S", "N25Q128", "W25Q256JV"}),
    (["S25FL127S-256KB", "S25FL127S-64KB", "W25Q64JV-.Q"], 0x012018,
     {"S25FL127S", "W25Q64JV"}),
    # ...but a "-" after a digit is the part's own: ST's M25P05-A
    (["M25P05", "M25P05-A"], 0x202010, {"M25P05", "M25P05-A"}),
    # Linux's model digit, where the name without it is also at the id
    (["S25FL128S", "S25FL128S0", "S25FL128S1"], 0x012018, {"S25FL128S"}),
    # ...and not otherwise: a digit that ends a name is usually its size
    (["S25SL12800", "S25SL12801"], 0x012018, {"S25SL12800", "S25SL12801"}),
    # a "." inside a name is a letter flashrom leaves open, kept for family()
    (["W25Q128.V", "N25Q128..3E"], 0x20BA18, {"W25Q128.V", "N25Q128..3E"}),
    # a name that is the id's own hex is the bare id, not a part
    (["MACRONIX-C22019", "MX25L25645G"], 0xC22019, {"MX25L25645G"}),
    (["MACRONIX-C22019"], 0xC22019, set()),
])
def test_names_are_cut_back_to_the_part_numbers_they_stand_for(names, jedec, parts):
    assert db.normalise(names, jedec) == parts


@pytest.mark.parametrize(("names", "family"), [
    # one part: its own name
    (["AT25DF321A"], "AT25DF321A"),
    # the letters the id cannot settle are written as x, one per letter:
    # Linux's generic "w25q32" and flashrom's W25Q32BV..JV
    (["W25Q32", "W25Q32BV", "W25Q32FV", "W25Q32JV"], "W25Q32xx"),
    (["GD25Q32", "GD25Q32C"], "GD25Q32x"),
    # flashrom's "." is already such a letter
    (["W25Q128", "W25Q128.V"], "W25Q128xx"),
    (["W25Q16.V"], "W25Q16xV"),
    (["MX25L25635F", "MX25L25645G"], "MX25L256x5x"),
    (["S25FL004A", "S25SL004A"], "S25xL004A"),
    # ...and matches any letter in the family's own: BoHong's and Boya's
    (["B.25Q32BS", "BY25Q32CS", "BY25Q32ES"], "Bx25Q32xS"),
    # two families at one id -- a second source's part, or a rebadge -- are
    # both named rather than merged into a row of x
    (["MT25QL128", "N25Q128A13"], "MT25QL128/N25Q128A13"),
    ([], None),
])
def test_a_family_name_writes_what_the_id_cannot_settle_as_x(names, family):
    assert db.family(names) == family


def test_a_manufacturer_byte_names_whose_it_mostly_is():
    # 0x68 is Boya's on a flash, though JEP106's first bank gives it to
    # another company: the byte comes without its bank's continuation codes
    assert db.manufacturer(0x68) == "Boya"
    assert db.manufacturer(0xEF) == "Winbond"
    assert db.manufacturer(0x5A) is None


def test_the_version_is_spiflashs():
    import spiflash

    assert db.version() == spiflash.__version__


def test_the_nor_filter_follows_spiflash_s_keyword():
    """spiflash renamed lookup's `type` to `flash_type` after 0.0.post11:
    the Debian package republishes its newest build, PyPI has the old one,
    and rpi-hwid must work with either."""
    def old(self, chip_id, *, type=None, method="jedec"):
        return []

    def new(self, chip_id, *, flash_type=None, method="jedec"):
        return []

    assert db.nor_only(old) == {"type": "nor"}
    assert db.nor_only(new) == {"flash_type": "nor"}
    # and the one installed is asked the way it answers
    assert db.flashes(0xC84016)
