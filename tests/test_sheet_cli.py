"""rpi-hwid-sheet end to end: a fake collector, the real ``rpi-hwid labels``
(run as rpi-hwid-sheet runs it, as a separate command), and a fake IPP
printer that records what it is sent."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from conftest import RAW
from rpi_hwid.sheet import cli, ipp, state
from test_sheet_ipp import FakePrinter


@pytest.fixture
def fake_rpi_hwid(tmp_path):
    """An rpi-hwid whose collect copies the fixtures' documents for the
    hosts asked for (and records its arguments), and which is the real one
    for everything else."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for name, raw in RAW.items():
        (fixtures / f"{name}.json").write_text(json.dumps(raw))
    script = tmp_path / "fake_rpi_hwid.py"
    script.write_text(textwrap.dedent(f"""\
        import json, shutil, sys
        from pathlib import Path
        args = sys.argv[1:]
        if args[0] in ("collect", "tasmota"):
            Path({str(tmp_path / "collect-args.json")!r}).write_text(json.dumps(args))
            out = Path(args[args.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            for a in args[1:]:
                f = Path({str(fixtures)!r}) / (a.rsplit("@", 1)[-1] + ".json")
                if f.exists():
                    shutil.copy(f, out / f.name)
            print("collected")
            sys.exit(0)
        from rpi_hwid.cli import main
        sys.exit(main(args))
        """))
    return f"{sys.executable} {script}"


class Printer:
    """A fake printer whose jobs finish as `end` says (job-state, sheets)."""

    def __init__(self, end=(ipp.JOB_COMPLETED, 1), printer_state=3, sources=("auto", "manual")):
        self.end = end
        self.next_id = 400
        self.fake = FakePrinter(self.answer)
        self.printer_state = printer_state
        self.sources = sources

    def answer(self, msg):
        if msg.code == ipp.GET_PRINTER_ATTRIBUTES:
            return 0, [(ipp.PRINTER_GROUP, {
                "printer-state": ipp.enum(self.printer_state),
                "printer-make-and-model": ipp.text("Fake MFC"),
                "media-source-supported": [ipp.keyword(s) for s in self.sources]})]
        if msg.code == ipp.PRINT_JOB:
            self.next_id += 1
            return 0, [(ipp.JOB_GROUP, {"job-id": ipp.integer(self.next_id),
                                        "job-state": ipp.enum(ipp.JOB_PENDING)})]
        state_, sheets = self.end
        return 0, [(ipp.JOB_GROUP, {"job-state": ipp.enum(state_),
                                    "job-impressions-completed": ipp.integer(sheets)})]

    @property
    def jobs(self):
        return [m for m in self.fake.requests if m.code == ipp.PRINT_JOB]


@pytest.fixture
def printer():
    made = []

    def make(**kw):
        p = Printer(**kw)
        made.append(p)
        return p

    yield make
    for p in made:
        p.fake.close()


@pytest.fixture
def run(tmp_path, fake_rpi_hwid, capsys):
    root = tmp_path / "state"

    def run(*argv, answer=None, tty=False):
        if answer is not None or tty:
            replies = iter([answer or ""])
            cli.ASK.update(input=lambda prompt: (print(prompt), next(replies))[1],
                           isatty=lambda: True)
        else:
            cli.ASK.update(input=None, isatty=lambda: False)
        rc = cli.main(["--state", str(root), "--rpi-hwid", fake_rpi_hwid, "--poll", "0.01",
                       *argv])
        out = capsys.readouterr()
        return rc, out.out, out.err

    run.root = root
    return run


def new_sheet(run, p):
    rc, out, err = run("new", "--printer", p.fake.uri)
    assert rc == 0, err
    return out.split()[0]


def prepare(run, sheet, *argv):
    rc, out, err = run("print", sheet, "--prepare", *argv)
    assert rc == 0, err
    plan = next(line.split()[-1] for line in out.splitlines()
                if line.startswith("rpi-hwid-sheet") and " commit " in line)
    return plan, out


# --- new, list, status ---------------------------------------------------------------


def test_new_starts_a_sheet_with_its_printer(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    s = state.Store(run.root).load(sid)
    assert s.printer == p.fake.uri
    assert s.slots == {}
    assert not s.marked


def test_new_without_a_printer_says_how_to_name_one(run, monkeypatch):
    monkeypatch.delenv("RPI_HWID_PRINTER", raising=False)
    rc, _, err = run("new")
    assert rc == 2
    assert "--printer" in err
    assert "RPI_HWID_PRINTER" in err


def test_list_and_status_show_what_is_free(run, printer):
    sid = new_sheet(run, printer())
    rc, out, _ = run("list")
    assert rc == 0
    assert sid in out
    assert "21 free" in out
    rc, out, _ = run("mark", sid, "1", "2", "5c", "--why", "used before the tool")
    assert rc == 0
    rc, out, _ = run("status", sid)
    assert rc == 0
    assert "18 free stickers, 3 free quarters" in out
    assert "used before the tool" in out


# --- prepare and commit (the mode for a Claude session) ----------------------------------


def test_prepare_shows_the_labels_and_their_slots_and_prints_nothing(run, printer, tmp_path):
    p = printer()
    sid = new_sheet(run, p)
    plan, out = prepare(run, sid, "pi3")
    assert "pi3/rpi/Pi 4 Model B 1 GB 10000000f1b7bb5a" in out
    assert "pi3/arty/arty-hoopoe" in out
    assert " 1 " in out
    assert "preview" in out
    assert p.jobs == []
    assert state.Store(run.root).load(sid).slots == {}
    plan_dir = run.root / "plans" / plan
    for f in ("plan.json", "pass.pdf", "preview.png", "meta.json"):
        assert (plan_dir / f).exists(), f
    # the first pass marks the sheet
    assert json.loads((plan_dir / "plan.json").read_text())["sheet"]["id"] == sid


def test_the_fresh_read_is_kept_with_the_collectors_arguments(run, printer, tmp_path):
    p = printer()
    sid = new_sheet(run, p)
    prepare(run, sid, "pi3", "--", "--fpga")
    args = json.loads((tmp_path / "collect-args.json").read_text())
    assert args[0] == "collect"
    assert "pi3" in args
    assert "--fpga" in args
    (read,) = [p for p in (run.root / "reads" / sid).iterdir() if p.is_dir()]
    assert (read / "pi3.json").exists()
    assert (read.parent / f"{read.name}.log").exists()


def test_commit_sends_to_the_manual_feed_and_records_the_slots(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, out, err = run("commit", plan)
    assert rc == 0, err
    (job,) = p.jobs
    assert job.attributes("job")["media-col"][0]["media-source"] == ["manual"]
    assert job.data.startswith(b"%PDF")
    assert "manual feed" in out
    s = state.Store(run.root).load(sid)
    assert s.slots["1"]["label"] == "pi3/rpi/Pi 4 Model B 1 GB 10000000f1b7bb5a"
    assert s.slots["2"]["host"] == "pi3"
    assert s.marked
    assert s.passes[0]["job"] == 401
    assert s.passes[0]["job_state"] == "completed"


def test_a_second_pass_fills_the_next_slots_without_marking_again(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    run("commit", prepare(run, sid, "pi3")[0])
    plan, _ = prepare(run, sid, "rpi4-tt")
    sent = json.loads((run.root / "plans" / plan / "plan.json").read_text())
    assert "sheet" not in sent
    assert [x["slot"] for x in sent["labels"]] == ["3", "4", "5"]
    assert run("commit", plan)[0] == 0
    assert sorted(state.Store(run.root).load(sid).slots, key=int) == ["1", "2", "3", "4", "5"]


def test_a_plan_made_before_the_sheet_changed_is_refused(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    first, _ = prepare(run, sid, "pi3")
    second, _ = prepare(run, sid, "rpi4-tt")
    assert run("commit", first)[0] == 0
    rc, _, err = run("commit", second)
    assert rc == 2
    assert "changed since" in err
    assert len(p.jobs) == 1


def test_a_plan_is_committed_once(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    assert run("commit", plan)[0] == 0
    rc, _, err = run("commit", plan)
    assert rc == 2
    assert "already" in err
    assert len(p.jobs) == 1


# --- asking at the terminal -------------------------------------------------------------


def test_print_asks_and_a_no_prints_nothing(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    rc, out, _ = run("print", sid, "pi3", answer="n")
    assert rc == 1
    assert "Print 2 labels on sheet" in out
    assert p.jobs == []
    assert state.Store(run.root).load(sid).slots == {}


def test_print_asks_and_a_yes_prints(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    rc, _, err = run("print", sid, "pi3", answer="y")
    assert rc == 0, err
    assert len(p.jobs) == 1
    assert len(state.Store(run.root).load(sid).slots) == 2


def test_print_with_no_terminal_to_ask_points_at_prepare(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    rc, _, err = run("print", sid, "pi3")
    assert rc == 2
    assert "--prepare" in err
    assert p.jobs == []


def test_print_new_starts_a_sheet_for_the_labels(run, printer):
    p = printer()
    rc, out, err = run("print", "new", "pi3", "--printer", p.fake.uri, "--prepare")
    assert rc == 0, err
    (sid,) = state.Store(run.root).ids()
    assert sid in out


# --- choosing labels ----------------------------------------------------------------------


def test_label_picks_some_of_a_hosts_labels(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3", "--label", "arty-hoopoe")
    sent = json.loads((run.root / "plans" / plan / "plan.json").read_text())
    assert [x["id"] for x in sent["labels"]] == ["pi3/arty/arty-hoopoe 0x0064f5483229085c"]


def test_a_label_that_matches_nothing_is_an_error(run, printer):
    sid = new_sheet(run, printer())
    rc, _, err = run("print", sid, "pi3", "--prepare", "--label", "no-such-thing")
    assert rc == 2
    assert "no-such-thing" in err


def test_a_host_the_read_got_nothing_for_is_an_error(run, printer):
    sid = new_sheet(run, printer())
    rc, _, err = run("print", sid, "pi3", "nowhere", "--prepare")
    assert rc == 2
    assert "the read has nothing for nowhere" in err
    assert run.root.joinpath("plans").exists() is False


def test_a_host_with_no_labels_in_the_data_is_an_error(run, printer, data_dir):
    sid = new_sheet(run, printer())
    rc, _, err = run("print", sid, "nowhere", "--data", str(data_dir), "--prepare")
    assert rc == 2
    assert "no labels for nowhere" in err


def test_a_user_at_host_is_matched_by_its_host(run, printer):
    sid = new_sheet(run, printer())
    plan, _ = prepare(run, sid, "pi@pi3")
    sent = json.loads((run.root / "plans" / plan / "plan.json").read_text())
    assert len(sent["labels"]) == 2


def test_existing_data_is_used_instead_of_collecting(run, printer, data_dir, tmp_path):
    sid = new_sheet(run, printer())
    plan, _ = prepare(run, sid, "--data", str(data_dir), "--only", "tt")
    sent = json.loads((run.root / "plans" / plan / "plan.json").read_text())
    assert len(sent["labels"]) == 3
    assert not (tmp_path / "collect-args.json").exists()


def test_a_full_sheet_says_so_and_prints_nothing(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    run("mark", sid, *[str(i) for i in range(1, 21)])
    rc, _, err = run("print", sid, "pi3", "--prepare")
    assert rc == 2
    assert "start a new sheet" in err


# --- the printer and the job ---------------------------------------------------------------


def test_a_stopped_printer_is_not_sent_the_job(run, printer):
    p = printer(printer_state=5)
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, _, err = run("commit", plan)
    assert rc == 2
    assert "stopped" in err
    assert p.jobs == []
    assert state.Store(run.root).load(sid).slots == {}


def test_a_printer_with_no_manual_feed_is_not_sent_the_job(run, printer):
    p = printer(sources=("auto", "main"))
    sid = new_sheet(run, p)
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 2
    assert "manual" in err
    assert p.jobs == []


def test_a_job_cancelled_before_printing_frees_its_slots(run, printer):
    p = printer(end=(ipp.JOB_CANCELED, 0))
    sid = new_sheet(run, p)
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 1
    assert "canceled" in err
    s = state.Store(run.root).load(sid)
    assert s.slots == {}
    assert not s.marked
    # and the plan can be tried again
    assert s.passes == []


def test_a_job_aborted_part_way_keeps_its_slots_used(run, printer):
    """Something may be on the paper: the slots stay used rather than risk
    printing over them."""
    p = printer(end=(ipp.JOB_ABORTED, 1))
    sid = new_sheet(run, p)
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 1
    assert "aborted" in err
    s = state.Store(run.root).load(sid)
    assert len(s.slots) == 2
    assert s.passes[0]["job_state"] == "aborted"


def test_a_job_that_does_not_finish_in_time_keeps_its_slots_used(run, printer):
    p = printer(end=(ipp.JOB_PROCESSING, 0))
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, _, err = run("commit", plan, "--wait", "0.05")
    assert rc == 1
    assert "still processing" in err
    s = state.Store(run.root).load(sid)
    assert len(s.slots) == 2
    assert s.passes[0]["job_state"] == "processing"
