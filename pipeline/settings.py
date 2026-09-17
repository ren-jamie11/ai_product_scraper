"""
Model and reasoning-effort choices, editable from the UI.

`config.py` holds the defaults and is edited by hand; this module holds the few
knobs worth changing without reopening a file — which model and how much thinking
to spend on tagging, the same pair for grouping, and whether clusters are grouped
into themes automatically.

Deliberately narrow. The API accepts reasoning efforts from `none` through `max`,
and OpenAI sells more models than these, but a settings panel that offers every
possibility is a panel you have to reason about every time you open it. Three
models and three efforts cover the real trade (cheap and fast, versus careful),
and anything outside the whitelist is rejected rather than quietly coerced — a
typo that silently downgrades your model is worse than an error message.

Precedence is `settings.json` when a key is set, `config.py` otherwise. Env vars
still reach `config.py` as before, so they remain the way to pin a value for a
single run without touching the saved settings.
"""

from __future__ import annotations

import config
from pipeline import storage

# Only the luna/terra/sol family. Base `gpt-5.6` is left off on purpose: it prices
# the same as sol and having two near-identical top options invites second-guessing.
MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")

# `none`, `minimal`, `xhigh` and `max` are all real API values, but the useful
# span for this pipeline sits inside these three.
EFFORTS = ("low", "medium", "high")

# Each setting maps to the config attribute it overrides, and to the whitelist its
# value must appear in. Adding a knob means adding a row here and nothing else.
# A boolean setting lists (True, False) and is checked by type as well as value,
# because `1 in (True, False)` is true in Python and a hand-edited 1 is not a
# choice anyone made.
KEYS = {
    "tag_model":       ("OPENAI_TAG_MODEL", MODELS),
    "tag_reasoning":   ("OPENAI_TAG_REASONING", EFFORTS),
    "group_model":     ("OPENAI_GROUP_MODEL", MODELS),
    "group_reasoning": ("OPENAI_GROUP_REASONING", EFFORTS),
    "auto_themes":     ("AUTO_THEMES", (True, False)),
}


class SettingsError(Exception):
    """A rejected setting. The message is shown to the user verbatim."""


def _path():
    return storage.data_root() / "settings.json"


def _valid(value, allowed) -> bool:
    return type(value) is type(allowed[0]) and value in allowed


def read() -> dict:
    """The saved overrides, with anything unrecognised dropped.

    Values are validated on the way out as well as in, so a settings.json edited
    by hand into a bad state degrades to the config default instead of sending an
    invalid model to the API.
    """
    saved = storage.read_json(_path(), {}) or {}
    clean = {}
    for key, (_, allowed) in KEYS.items():
        value = saved.get(key)
        if _valid(value, allowed):
            clean[key] = value
    return clean


def resolve(key: str):
    """The value in force for one setting: saved override, else config default."""
    attribute, _ = KEYS[key]
    saved = read()
    return saved[key] if key in saved else getattr(config, attribute)


def effective() -> dict:
    """Every setting's current value, for the UI and for the run logs."""
    return {key: resolve(key) for key in KEYS}


def write(patch: dict) -> dict:
    """Apply a patch and return the new effective settings.

    Rejects the whole patch if any value is unknown rather than applying the good
    half — a partly-applied settings save is hard to notice and harder to explain.
    """
    if not isinstance(patch, dict):
        raise SettingsError("Settings must be sent as an object.")

    for key, value in patch.items():
        if key not in KEYS:
            raise SettingsError(f'"{key}" is not a setting this app has.')
        allowed = KEYS[key][1]
        if not _valid(value, allowed):
            if isinstance(allowed[0], bool):
                raise SettingsError(f"{key} must be true or false.")
            raise SettingsError(
                f'"{value}" is not a valid {key.replace("_", " ")}. '
                f"Choose one of: {', '.join(allowed)}."
            )

    storage.write_json(_path(), {**read(), **patch})
    return effective()


def options() -> dict:
    """What the Settings panel draws its dropdowns from."""
    return {"models": list(MODELS), "efforts": list(EFFORTS)}
