import pytest

from eggtimer_decoder.channels import CENTER_FREQ_HZ
from eggtimer_decoder.settings import (
    Settings,
    SettingsError,
    default_settings_path,
    load_settings,
    load_settings_file,
    resolve_settings,
    save_settings,
)


def test_defaults():
    s = Settings()
    assert s.center_freq_hz == CENTER_FREQ_HZ == 921e6
    assert s.channel == 0 and s.gain is None


def test_round_trip(tmp_path):
    s = Settings(device="rtl=1", center_freq_hz=920.5e6, channel=4, gain=29.7, ppm=-12.5,
                 log_file="a.jsonl", position_file="pos.json", verbose=True)
    p = save_settings(s, tmp_path / "sub" / "s.json")
    values, warn = load_settings_file(p)
    assert warn is None
    assert resolve_settings(Settings(), values) == s


@pytest.mark.parametrize("kw,msg", [
    ({"channel": 9}, "channel"),
    ({"channel": True}, "channel"),
    ({"center_freq_hz": 0}, "center_freq_hz"),
    ({"center_freq_hz": float("nan")}, "center_freq_hz"),
    ({"gain": -1}, "gain"),
    ({"ppm": 5000}, "ppm"),
    ({"log_file": ""}, "log_file"),
    ({"verbose": True, "quiet": True}, "mutually exclusive"),
    ({"device": 3}, "device"),
])
def test_invalid_rejected(kw, msg):
    with pytest.raises(SettingsError, match=msg):
        Settings(**kw)
    assert issubclass(SettingsError, ValueError)


def test_missing_file(tmp_path):
    assert load_settings_file(tmp_path / "nope.json") == ({}, None)
    s, w = load_settings(tmp_path / "nope.json", env={})
    assert s == Settings() and w is None


@pytest.mark.parametrize("content", ["{not json", "[1]", '{"channel": 42}', '{"bogus": 1}'])
def test_corrupt_file_falls_back(tmp_path, content):
    p = tmp_path / "s.json"
    p.write_text(content)
    s, w = load_settings(p, env={})
    assert s == Settings() and "ignoring settings file" in w


def test_binary_garbage_falls_back(tmp_path):
    p = tmp_path / "s.json"
    p.write_bytes(b"\x00\xff\xfe")
    s, w = load_settings(p, env={})
    assert s == Settings() and w


def test_precedence():
    d = Settings()
    f = {"channel": 1, "ppm": 1.0, "gain": 10.0, "device": "file"}
    e = {"EGGTIMER_CHANNEL": "2", "EGGTIMER_PPM": "2", "EGGTIMER_DEVICE": "env"}
    c = {"channel": 3, "gain": None, "device": None}
    s = resolve_settings(d, f, e, c)
    assert s.channel == 3  # cli
    assert s.ppm == 2.0 and s.device == "env"  # env over file
    assert s.gain == 10.0  # file over default, None cli ignored
    assert resolve_settings(d, {}, {}, {}) == d


def test_env_parsing_and_errors():
    s = resolve_settings(env={"EGGTIMER_GAIN": "auto", "EGGTIMER_VERBOSE": "yes",
                              "EGGTIMER_CENTER_FREQ_HZ": "9.2e8"}, file_values={"gain": 5})
    assert s.gain is None and s.verbose and s.center_freq_hz == 920e6
    with pytest.raises(SettingsError, match="EGGTIMER_CHANNEL"):
        resolve_settings(env={"EGGTIMER_CHANNEL": "x"})
    with pytest.raises(SettingsError, match="channel"):
        resolve_settings(cli={"channel": 11})
    with pytest.raises(SettingsError, match="unknown"):
        resolve_settings(cli={"nope": 1})


def test_default_path_xdg(tmp_path, monkeypatch):
    assert default_settings_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "eggtimer-decoder" / "settings.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_settings_path({}) == tmp_path / ".config" / "eggtimer-decoder" / "settings.json"
