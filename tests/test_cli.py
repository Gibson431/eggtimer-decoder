import json
import os
import sys
import types
import time

import pytest

from eggtimer_decoder.cli import main, parse_freq
from eggtimer_decoder.sim_source import SimSource


class PacedSim(SimSource):
    def __init__(self, *a, **kw):
        super().__init__(*a, realtime=False, **kw)

    def read(self, n):
        out = super().read(n)
        time.sleep(0.004)
        return out


def sim_factory(s):
    return PacedSim(channel=s.channel, center_freq=s.center_freq_hz)


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("EGGTIMER_"):
            monkeypatch.delenv(k)
    p = tmp_path / "settings.json"
    monkeypatch.setenv("EGGTIMER_CONFIG", str(p))
    return p


def test_run_sim_channel2_prints_packets(capsys, tmp_path):
    log, pos = tmp_path / "log.jsonl", tmp_path / "pos.json"
    rc = main(
        [
            "run",
            "--device",
            "sim",
            "--channel",
            "2",
            "--duration",
            "3",
            "--log-file",
            str(log),
            "--position-file",
            str(pos),
        ],
        source_factory=sim_factory,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "frame len=" in out
    assert "$GNGGA,014049.000,3520.8200,N,11748.4800,W,1,08" in out
    assert "=> Position(" in out
    types = {json.loads(line)["type"] for line in log.read_text().splitlines()}
    assert {"frame", "nmea", "position"} <= types
    assert json.loads(pos.read_text())["satellites"] == 8


def test_options_without_command_mean_run_and_quiet(capsys):
    rc = main(["--device", "sim", "-q", "--duration", "3"], source_factory=sim_factory)
    out = capsys.readouterr().out
    assert rc == 0 and "$GNGGA" in out and "frame len=" not in out


def test_run_error_exit_code(capsys):
    def bad(s):
        raise RuntimeError("boom")

    assert main(["run", "--device", "x", "--duration", "5"], source_factory=bad) == 1
    assert "boom" in capsys.readouterr().out


def test_devices_none(capsys, monkeypatch):
    from eggtimer_decoder import sdr

    monkeypatch.setattr(
        sdr, "discover", lambda *a, **k: []
    )  # independent of attached hardware
    assert main(["devices"]) == 0
    assert "no SDR devices found" in capsys.readouterr().out


def test_devices_lists_found_device(capsys, monkeypatch):
    from eggtimer_decoder import sdr

    d = sdr.DeviceInfo("rtl=0", "rtl:0", "rtl=0", (0.0, 1766e6), (250e3, 1.92e6))
    monkeypatch.setattr(sdr, "discover", lambda *a, **k: [d])
    assert main(["devices"]) == 0
    assert "rtl=0\trtl:0\t0-1766 MHz" in capsys.readouterr().out


def test_channels_table(capsys):
    assert main(["channels"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") >= 5 and "-15000" in out


@pytest.mark.parametrize(
    "cmd",
    [
        [],
        ["run"],
        ["devices"],
        ["channels"],
        ["settings"],
        ["settings", "show"],
        ["settings", "set"],
        ["settings", "reset"],
    ],
)
def test_help(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        main(cmd + ["--help"])
    assert e.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_no_args_without_gui(capsys, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "eggtimer_decoder.gui", None
    )  # simulate "not importable"
    assert main([]) == 0
    assert "GUI is not available yet" in capsys.readouterr().out


def test_settings_round_trip(capsys, cfg):
    assert main(["settings", "show"]) == 0
    assert '"channel": 0' in capsys.readouterr().out
    assert main(["settings", "set", "channel=2", "center_freq=920.5M", "gain=20"]) == 0
    saved = json.loads(cfg.read_text())
    assert (
        saved["channel"] == 2
        and saved["center_freq_hz"] == 920.5e6
        and saved["gain"] == 20
    )
    capsys.readouterr()
    main(["settings", "show"])
    assert '"channel": 2' in capsys.readouterr().out
    assert main(["settings", "set", "gain=auto"]) == 0
    assert json.loads(cfg.read_text())["gain"] is None
    assert main(["settings", "reset"]) == 0
    assert not cfg.exists()


def test_settings_set_invalid(capsys, cfg):
    assert main(["settings", "set", "channel=99"]) == 2
    assert main(["settings", "set", "bogus=1"]) == 2
    assert main(["settings", "set", "nonsense"]) == 2
    assert not cfg.exists()


def test_saved_settings_used_by_run(capsys):
    main(["settings", "set", "device=sim", "channel=2"])
    seen = []

    def factory(s):
        seen.append(s)
        return sim_factory(s)

    assert main(["run", "--duration", "0.5"], source_factory=factory) == 0
    assert seen[0].device == "sim" and seen[0].channel == 2
    main(["run", "--channel", "1", "--duration", "0.5"], source_factory=factory)
    assert seen[1].channel == 1


@pytest.mark.parametrize(
    "text,hz",
    [
        ("921e6", 921e6),
        ("921M", 921e6),
        ("920.99MHz", 920.99e6),
        ("433k", 433e3),
        ("0.921G", 921e6),
        ("921", 921e6),
        ("921000000", 921e6),
    ],
)
def test_parse_freq(text, hz):
    assert parse_freq(text) == pytest.approx(hz)


def test_parse_freq_bad():
    with pytest.raises(ValueError):
        parse_freq("abc")


def test_no_args_launches_gui(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "eggtimer_decoder.gui", types.SimpleNamespace(main=lambda: 7)
    )
    assert main([]) == 7
