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

    def __init__(self, end=(ipp.JOB_COMPLETED, 1), printer_state=3, sources=("auto", "manual"),
                 print_reply="ok", on_job=None):
        self.end = end
        self.next_id = 400
        # "ok", "hangup" (taken, never answered) or a refusal status
        self.print_reply = print_reply
        # called on each Get-Job-Attributes: another session acting meanwhile
        self.on_job = on_job
        self.fake = FakePrinter(self.answer)
        self.printer_state = printer_state
        self.sources = sources

    def answer(self, msg):
        if msg.code == ipp.GET_PRINTER_ATTRIBUTES:
            return 0, [(ipp.PRINTER_GROUP, {
                "printer-state": ipp.enum(self.printer_state),
                # a job stopped is one waiting for its sheet, as the Brother's are
                "printer-state-reasons": ipp.keyword(
                    "media-needed-error" if self.end[0] == ipp.JOB_STOPPED else "none"),
                "printer-make-and-model": ipp.text("Fake MFC"),
                "media-source-supported": [ipp.keyword(s) for s in self.sources]})]
        if msg.code == ipp.PRINT_JOB:
            if self.print_reply == "hangup":
                return None
            if self.print_reply != "ok":
                return self.print_reply, [(ipp.OPERATION_GROUP, {
                    "status-message": ipp.text("client-error-document-format-error")})]
            self.next_id += 1
            return 0, [(ipp.JOB_GROUP, {"job-id": ipp.integer(self.next_id),
                                        "job-state": ipp.enum(ipp.JOB_PENDING)})]
        forgotten = getattr(self, "forgotten", False)  # a printer that no longer knows the job
        if forgotten:
            return (0x0406 if forgotten is True else forgotten), [(ipp.OPERATION_GROUP, {
                "status-message": ipp.text("client-error-not-found")})]
        if self.on_job:
            self.on_job()
        state_, sheets = self.end
        attrs = {"job-state": ipp.enum(state_)}
        if sheets is not None:              # None: the printer does not say
            attrs["job-impressions-completed"] = ipp.integer(sheets)
        return 0, [(ipp.JOB_GROUP, attrs)]

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


def test_follow_picks_up_a_job_that_outlasted_the_wait(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, out, _ = run("commit", plan, "--wait", "0.05")
    assert rc == 1
    # the printer said why: it wants the sheet in the manual feed
    assert "waiting for sheet" in out
    p.end = (ipp.JOB_COMPLETED, 1)
    rc, out, err = run("follow", sid)
    assert rc == 0, err
    s = state.Store(run.root).load(sid)
    assert s.passes[0]["job_state"] == "completed"
    assert len(s.slots) == 2


def test_follow_frees_the_slots_of_a_job_cancelled_while_waiting(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    run("commit", plan, "--wait", "0.05")
    p.end = (ipp.JOB_CANCELED, 0)
    rc, _, err = run("follow", sid)
    assert rc == 1
    assert "canceled" in err
    assert state.Store(run.root).load(sid).slots == {}
    # and its plan can go again
    p.end = (ipp.JOB_COMPLETED, 1)
    assert run("commit", plan)[0] == 0


def test_follow_with_nothing_outstanding_says_so(run, printer):
    sid = new_sheet(run, printer())
    rc, _, err = run("follow", sid)
    assert rc == 2
    assert "nothing" in err


# --- never printing twice in one slot ----------------------------------------------------


def test_a_refused_job_frees_its_slots_and_its_plan(run, printer):
    p = printer(print_reply=0x040A)
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, _, err = run("commit", plan)
    assert rc == 2
    assert "refused" in err
    assert state.Store(run.root).load(sid).slots == {}
    p.print_reply = "ok"
    assert run("commit", plan)[0] == 0


def test_a_job_sent_but_never_answered_keeps_its_slots(run, printer):
    """The printer may have it: the slots stay used, and the plan cannot be
    sent again to print over them."""
    p = printer(print_reply="hangup")
    sid = new_sheet(run, p)
    plan, _ = prepare(run, sid, "pi3")
    rc, _, err = run("commit", plan)
    assert rc == 2
    assert "may have" in err
    s = state.Store(run.root).load(sid)
    assert len(s.slots) == 2
    assert s.passes[0]["job_state"] == "unknown"
    p.print_reply = "ok"
    rc, _, err = run("commit", plan)
    assert rc == 2
    assert "already" in err


def test_a_cancelled_job_that_does_not_say_what_it_printed_keeps_its_slots(run, printer):
    p = printer(end=(ipp.JOB_CANCELED, None))
    sid = new_sheet(run, p)
    rc, _, _ = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 1
    assert len(state.Store(run.root).load(sid).slots) == 2


def test_waiting_does_not_undo_what_another_session_did_meanwhile(run, printer):
    """Another session marks a slot while this one waits for its sheet: the
    end of the wait records the job without losing the mark."""
    store = {}

    def meanwhile():
        if "done" not in store:
            store["done"] = True
            s = state.Store(run.root).load(store["sid"])
            s.mark(["20"], why="another session", at="x")
            state.Store(run.root).save(s)

    p = printer(on_job=meanwhile)
    sid = store["sid"] = new_sheet(run, p)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 0
    s = state.Store(run.root).load(sid)
    assert s.slots["20"]["why"] == "another session"
    assert s.passes[0]["job_state"] == "completed"


def test_a_cancelled_job_with_a_later_pass_keeps_its_slots(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    run("commit", prepare(run, sid, "pi3")[0], "--wait", "0.05")
    # a second pass goes on while the first still waits
    p.end = (ipp.JOB_COMPLETED, 1)
    s = state.Store(run.root).load(sid)
    s.record_pass([("x", "9", "h", "t")], [], at="x", host="h", user="u", job=999,
                  job_state="completed", data="d", marked=False)
    state.Store(run.root).save(s)
    p.end = (ipp.JOB_CANCELED, 0)
    rc, _, err = run("follow", sid)
    assert rc == 1
    assert "pass 1" in err
    assert len(state.Store(run.root).load(sid).slots) == 3


# --- a pass a person says never printed (unprint) ------------------------------------------


def stalled(run, p, sid, host="pi3"):
    """Commit a pass whose job is still waiting for its sheet when --wait runs out."""
    plan, _ = prepare(run, sid, host)
    assert run("commit", plan, "--wait", "0.05")[0] == 1
    return plan


def test_unprint_frees_the_slots_of_a_job_the_printer_forgot(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    plan = stalled(run, p, sid)
    p.forgotten = True
    rc, _, err = run("follow", sid)
    assert rc == 2
    assert "no longer says how job" in err
    assert f"rpi-hwid-sheet unprint {sid} 1" in err  # it names both ways out
    assert f"rpi-hwid-sheet printed {sid} 1" in err
    assert len(state.Store(run.root).load(sid).slots) == 2  # and frees nothing by itself

    rc, out, err = run("unprint", sid, "1", "--why", "Tim: the sheet never went in")
    assert rc == 0, err
    assert "slots 1, 2 free again" in out
    assert "21 free stickers" in out
    s = state.Store(run.root).load(sid)
    assert s.slots == {}
    assert not s.marked  # the margins were that pass's too: the next pass prints them
    (only,) = s.passes  # the pass is kept: the record is the evidence
    assert only["job_state"] == "not-printed"
    assert only["not_printed"]["why"] == "Tim: the sheet never went in"
    assert only["not_printed"]["job_state"] == "processing-stopped"  # what it was before
    assert only["not_printed"]["slots"] == ["1", "2"]
    assert only["not_printed"]["user"]
    assert only["not_printed"]["at"]

    # nothing is outstanding any more, and the old plan is refused: the sheet has changed
    assert run("follow", sid)[0] == 2
    p.forgotten, p.end = False, (ipp.JOB_COMPLETED, 1)
    assert run("commit", plan)[0] != 0
    # a fresh plan prints in the freed slots, as pass 2 (numbers are not reused), margins and all
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 0, err
    s = state.Store(run.root).load(sid)
    assert sorted(s.slots) == ["1", "2"]
    assert {v["pass"] for v in s.slots.values()} == {2}
    assert s.marked


def test_unprint_takes_back_a_pass_that_is_not_the_last(run, printer):
    """2026-10-05, sheet 7MRC: passes 4 and 5 jammed on plain paper, pass 6 printed."""
    p = printer(end=(ipp.JOB_COMPLETED, 1))
    sid = new_sheet(run, p)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 0  # pass 1: slots 1, 2
    p.end = (ipp.JOB_STOPPED, 0)
    stalled(run, p, sid)                                         # pass 2: slots 3, 4, never printed
    p.end = (ipp.JOB_COMPLETED, 1)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 0  # pass 3: slots 5, 6

    rc, out, err = run("unprint", sid, "2", "--why", "three empty labels before the two printed")
    assert rc == 0, err
    assert "slots 3, 4 free again" in out
    s = state.Store(run.root).load(sid)
    assert sorted(s.slots, key=int) == ["1", "2", "5", "6"]
    assert s.marked  # pass 1 printed the margins
    assert [x["job_state"] for x in s.passes] == ["completed", "not-printed", "completed"]
    # the next pass is 4 and fills the gap first
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 0, err
    s = state.Store(run.root).load(sid)
    assert {k for k, v in s.slots.items() if v["pass"] == 4} == {"3", "4"}


def test_unprint_refuses_a_pass_the_printer_said_it_printed(run, printer):
    p = printer(end=(ipp.JOB_COMPLETED, 1))
    sid = new_sheet(run, p)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 0
    rc, _, err = run("unprint", sid, "1", "--why", "it looks blank")
    assert rc == 2
    assert "printed: the printer reported its job" in err
    assert len(state.Store(run.root).load(sid).slots) == 2


def test_unprint_is_said_once_and_only_of_a_pass_there_is(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    rc, _, err = run("unprint", sid, "7", "--why", "x")
    assert rc == 2
    assert "has no pass 7" in err
    assert run("unprint", sid, "1", "--why", "blank")[0] == 0
    rc, _, err = run("unprint", sid, "1", "--why", "blank")
    assert rc == 2
    assert "already recorded as not printed" in err
    with pytest.raises(SystemExit):  # the reason is not optional
        run("unprint", sid, "1")


def test_status_shows_a_pass_recorded_as_not_printed_and_why(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    run("unprint", sid, "1", "--why", "Tim: plain paper was in the slot")
    rc, out, _ = run("status", sid)
    assert rc == 0
    assert " not-printed, data " in out
    assert "Tim: plain paper was in the slot" in out
    assert "slots 1, 2 freed; the job was processing-stopped" in out
    assert "21 free stickers" in out


def test_printed_records_a_pass_the_printer_forgot_as_on_the_sheet(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    p.forgotten = True
    rc, out, err = run("printed", sid, "1", "--why", "Tim: the two labels which just printed")
    assert rc == 0, err
    assert "pass 1 recorded as printed" in out
    assert "19 free stickers" in out
    s = state.Store(run.root).load(sid)
    assert len(s.slots) == 2  # still used
    assert s.marked
    (only,) = s.passes
    assert only["job_state"] == "completed"
    assert only["printed"]["why"] == "Tim: the two labels which just printed"
    assert only["printed"]["job_state"] == "processing-stopped"
    assert run("follow", sid)[0] == 2  # nothing outstanding: the printer is not asked again
    rc, out, _ = run("status", sid)
    assert "printed, said" in out
    assert "Tim: the two labels which just printed" in out
    # said once; and a printed pass cannot then be unprinted, nor an unprinted one printed
    assert run("printed", sid, "1", "--why", "again")[0] == 2
    rc, _, err = run("unprint", sid, "1", "--why", "no")
    assert rc == 2
    assert "printed" in err


def test_printed_refuses_a_pass_recorded_as_not_printed(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    assert run("unprint", sid, "1", "--why", "blank")[0] == 0
    rc, _, err = run("printed", sid, "1", "--why", "it is there after all")
    assert rc == 2
    assert "already recorded as not-printed" in err
    assert run("printed", sid, "9", "--why", "x")[0] == 2


def test_a_cancelled_pass_after_an_unprinted_first_pass_leaves_the_margins_to_print(run, printer):
    """Pass 1 printed the margins by the record, a person says it printed
    nothing, pass 2 is then cancelled by the printer: the margins are still
    owed, whatever pass 1's own entry says."""
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    assert run("unprint", sid, "1", "--why", "blank")[0] == 0
    p.end = (ipp.JOB_CANCELED, 0)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 1
    s = state.Store(run.root).load(sid)
    assert [x["job_state"] for x in s.passes] == ["not-printed"]
    assert s.slots == {}
    assert not s.marked
    p.end = (ipp.JOB_COMPLETED, 1)
    assert run("commit", prepare(run, sid, "pi3")[0])[0] == 0
    assert state.Store(run.root).load(sid).marked


def test_the_printers_later_word_does_not_overrule_a_persons(run, printer):
    """follow is still waiting on a job when a person unprints its pass; the
    job then ends "completed". The slots were freed on the person's word
    and stay free: marking the pass completed would hide that."""
    store = state.Store(run.root)
    did = []

    def unprint_meanwhile():
        if not did:
            did.append(1)
            with store.lock(sid):
                s = store.load(sid)
                s.unprint(1, "blank", "2026-10-05T12:00:00+10:30", "tim")
                store.save(s)

    p = printer(end=(ipp.JOB_COMPLETED, 1), on_job=unprint_meanwhile)
    sid = new_sheet(run, p)
    rc, _, err = run("commit", prepare(run, sid, "pi3")[0])
    assert rc == 1
    assert "recorded as not-printed by a person meanwhile" in err
    s = store.load(sid)
    assert s.passes[0]["job_state"] == "not-printed"
    assert s.slots == {}


def test_only_a_job_the_printer_forgot_gets_the_hint(run, printer):
    p = printer(end=(ipp.JOB_STOPPED, 0))
    sid = new_sheet(run, p)
    stalled(run, p, sid)
    p.forgotten = 0x0403  # client-error-forbidden: a refusal that says nothing about the job
    rc, _, err = run("follow", sid)
    assert rc != 0
    assert "status 0x0403" in err
    assert "no longer says" not in err
    p.forgotten = 0x0404  # what the Brother answers for a job it has dropped
    rc, _, err = run("follow", sid)
    assert rc == 2
    assert "no longer says how job" in err


# --- more than one sticker of a label (--copies) -------------------------------------------


def test_copies_gives_a_label_a_sticker_each_in_one_pass(run, printer):
    p = printer()
    sid = new_sheet(run, p)
    plan, out = prepare(run, sid, "pi3", "--copies", "/rpi/=2")
    rows = [line.split(None, 1) for line in out.splitlines()
            if line.startswith("  ") and line.split()[0].isdigit()]
    assert [slot for slot, _ in rows] == ["1", "2", "3"]
    assert rows[0][1] == rows[1][1]  # the Pi's label twice, then the other label once
    assert "/rpi/" in rows[0][1]
    assert "/rpi/" not in rows[2][1]
    made = json.loads((run.root / "plans" / plan / "plan.json").read_text())
    assert made["copies"] is True
    assert [x["slot"] for x in made["labels"]] == ["1", "2", "3"]
    assert (run.root / "plans" / plan / "pass.pdf").read_bytes().startswith(b"%PDF")
    rc, _, err = run("commit", plan)
    assert rc == 0, err
    s = state.Store(run.root).load(sid)
    assert sorted(s.slots, key=int) == ["1", "2", "3"]
    assert s.slots["1"]["label"] == s.slots["2"]["label"] != s.slots["3"]["label"]
    assert len(s.passes[0]["labels"]) == 3
    # the next pass starts after all three
    _, out = prepare(run, sid, "pi3", "--label", "/rpi/")
    assert any(line.split()[0] == "4" for line in out.splitlines() if "/rpi/" in line)


def test_without_copies_a_plan_does_not_say_copies(run, printer):
    sid = new_sheet(run, printer())
    plan, _ = prepare(run, sid, "pi3")
    assert "copies" not in json.loads((run.root / "plans" / plan / "plan.json").read_text())


def test_copies_must_name_a_label_and_a_number(run, printer):
    sid = new_sheet(run, printer())
    cases = (("/rpi/", "give TEXT=N"), ("/rpi/=0", "give TEXT=N"), ("/rpi/=two", "give TEXT=N"),
             ("=2", "give TEXT=N"), ("/nothing/=2", "matches none of"))
    for bad, why in cases:
        rc, _, err = run("print", sid, "pi3", "--prepare", "--copies", bad)
        assert rc == 2, bad
        assert why in err, bad
    assert state.Store(run.root).load(sid).slots == {}


def test_copies_that_do_not_fit_say_the_sheet_is_full(run, printer):
    sid = new_sheet(run, printer())
    rc, _, err = run("print", sid, "pi3", "--prepare", "--copies", "/rpi/=21")
    assert rc == 2
    assert "start a new sheet" in err
