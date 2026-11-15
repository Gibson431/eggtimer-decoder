"""User settings: validation, JSON persistence and precedence resolution.

Precedence (highest first): CLI options > EGGTIMER_* environment > saved file > defaults.
"""

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from .channels import CENTER_FREQ_HZ, get_channel

ENV_PREFIX = "EGGTIMER_"
MIN_FREQ_HZ, MAX_FREQ_HZ = 1e6, 6e9
MAX_PPM = 1000.0


class SettingsError(ValueError):
    """A setting has an invalid value."""


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class Settings:
    device: str = ""  # device selector; "" = first available (S10 defines other values)
    center_freq_hz: float = CENTER_FREQ_HZ
    channel: int = 0
    gain: float | None = None  # dB; None = automatic gain
    ppm: float = 0.0  # frequency correction
    log_file: str | None = None
    position_file: str | None = None
    verbose: bool = False
    quiet: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.device, str):
            raise SettingsError(f"device must be a string, got {self.device!r}")
        if not _is_num(self.center_freq_hz) or not MIN_FREQ_HZ <= self.center_freq_hz <= MAX_FREQ_HZ:
            raise SettingsError(
                f"center_freq_hz must be a number in {MIN_FREQ_HZ:g}..{MAX_FREQ_HZ:g} Hz, got {self.center_freq_hz!r}"
            )
        try:
            get_channel(self.channel)
        except ValueError as e:
            raise SettingsError(str(e)) from None
        if self.gain is not None and (not _is_num(self.gain) or not 0 <= self.gain <= 100):
            raise SettingsError(f"gain must be 0..100 dB or unset (auto), got {self.gain!r}")
        if not _is_num(self.ppm) or abs(self.ppm) > MAX_PPM:
            raise SettingsError(f"ppm must be a number within ±{MAX_PPM:g}, got {self.ppm!r}")
        for name in ("log_file", "position_file"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, str) or not v):
                raise SettingsError(f"{name} must be a non-empty path string or unset, got {v!r}")
        for name in ("verbose", "quiet"):
            if not isinstance(getattr(self, name), bool):
                raise SettingsError(f"{name} must be true or false, got {getattr(self, name)!r}")
        if self.verbose and self.quiet:
            raise SettingsError("verbose and quiet are mutually exclusive")


_FIELDS = {f.name for f in fields(Settings)}


def _parse_env_value(name: str, raw: str) -> Any:
    """Convert an environment string to the field's type (errors become SettingsError)."""
    s = raw.strip()
    try:
        if name in ("center_freq_hz", "ppm"):
            return float(s)
        if name == "channel":
            return int(s)
        if name == "gain":
            return None if s.lower() in ("", "auto", "none") else float(s)
        if name in ("verbose", "quiet"):
            if s.lower() in ("1", "true", "yes", "on"):
                return True
            if s.lower() in ("0", "false", "no", "off", ""):
                return False
            raise ValueError("expected true/false")
        if name in ("log_file", "position_file"):
            return s or None
    except ValueError:
        raise SettingsError(f"{ENV_PREFIX}{name.upper()}: cannot parse {raw!r}") from None
    return raw


def settings_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Extract EGGTIMER_<FIELD> variables (e.g. EGGTIMER_CHANNEL=3) as a partial dict."""
    out: dict[str, Any] = {}
    for name in _FIELDS:
        key = ENV_PREFIX + name.upper()
        if key in env:
            out[name] = _parse_env_value(name, env[key])
    return out


def resolve_settings(
    defaults: Settings | None = None,
    file_values: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    cli: Mapping[str, Any] | None = None,
) -> Settings:
    """Pure precedence merge: cli > env > file > defaults. Raises SettingsError on bad values.

    CLI entries whose value is None are treated as "not given" (argparse defaults of None don't
    mask lower layers), so the CLI cannot reset gain to auto; use the saved file or env for that.
    Unknown keys in any layer are rejected.
    """
    merged = asdict(defaults or Settings())
    layers = [file_values or {}, settings_from_env(env or {}), {k: v for k, v in (cli or {}).items() if v is not None}]
    for layer in layers:
        unknown = set(layer) - _FIELDS
        if unknown:
            raise SettingsError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        merged.update(layer)
    return Settings(**merged)


def default_settings_path(env: Mapping[str, str] | None = None) -> Path:
    """$XDG_CONFIG_HOME/eggtimer-decoder/settings.json (or ~/.config/... when unset)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".config"
    return root / "eggtimer-decoder" / "settings.json"


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings as JSON (creating parent dirs); returns the path written."""
    path = Path(path) if path else default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def load_settings_file(path: Path | None = None) -> tuple[dict[str, Any], str | None]:
    """Read the saved file as a raw dict. Returns (values, warning).

    A missing file gives ({}, None). An unreadable/corrupt/invalid file gives ({}, warning),
    so callers fall back to defaults. The file is validated as a whole, so one bad value
    discards the file rather than half-applying it.
    """
    path = Path(path) if path else default_settings_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}, None
    except (OSError, UnicodeDecodeError) as e:
        return {}, f"ignoring settings file {path}: {e}"
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise SettingsError("top level must be a JSON object")
        unknown = set(data) - _FIELDS
        if unknown:
            raise SettingsError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        Settings(**{**asdict(Settings()), **data})
    except ValueError as e:  # JSONDecodeError and SettingsError are ValueErrors
        return {}, f"ignoring settings file {path}: {e}"
    return data, None


def load_settings(
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
    cli: Mapping[str, Any] | None = None,
) -> tuple[Settings, str | None]:
    """Convenience: read the file and resolve with env and cli. Returns (settings, warning)."""
    values, warning = load_settings_file(path)
    env = os.environ if env is None else env
    return resolve_settings(Settings(), values, env, cli), warning
