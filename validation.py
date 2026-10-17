"""Input validation and parsing helpers shared by the CLI and the engine.

Every value that comes from the user is validated here, so the rest of the
code base can assume it is dealing with clean data.
"""

import os
import re
import shlex
import time
from datetime import datetime, timedelta


class ValidationError(ValueError):
    """Raised when user supplied input is invalid."""


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DURATION_RE = re.compile(r"^(\d+)\s*([smhd]?)$")
UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)
MAX_COMMAND_LENGTH = 4096


def validate_name(name):
    """Job names: 1-64 chars, letters/digits/underscore/dot/dash, no spaces."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValidationError(
            "invalid job name %r: use 1-64 letters, digits, '_', '.' or '-' "
            "(must start with a letter or digit)" % (name,))
    return name


def validate_command(command):
    """A command is any non-empty shell command line without NUL bytes."""
    if command is None or not str(command).strip():
        raise ValidationError("command must not be empty")
    command = str(command).strip()
    if "\x00" in command:
        raise ValidationError("command must not contain NUL bytes")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValidationError("command is too long (max %d characters)" % MAX_COMMAND_LENGTH)
    return command


def validate_script(path):
    """Register a script file: must exist and be executable.  Returns a shell-safe command."""
    if not path or not str(path).strip():
        raise ValidationError("script path must not be empty")
    full = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(full):
        raise ValidationError("script not found: %s" % full)
    if not os.access(full, os.X_OK):
        raise ValidationError("script is not executable (run: chmod +x %s)" % full)
    return shlex.quote(full)


def validate_int(value, label, lo, hi):
    """Parse an integer and check it lies in [lo, hi]."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError("%s must be an integer, got %r" % (label, value))
    if not lo <= number <= hi:
        raise ValidationError("%s must be between %d and %d, got %d" % (label, lo, hi, number))
    return number


def parse_duration(text, label="duration", minimum=0, maximum=30 * 86400):
    """Parse '90', '45s', '5m', '2h' or '1d' into whole seconds."""
    match = DURATION_RE.match(str(text).strip().lower())
    if not match:
        raise ValidationError("%s: invalid duration %r (examples: 30, 45s, 5m, 2h, 1d)" % (label, text))
    seconds = int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    if seconds < minimum:
        raise ValidationError("%s must be at least %d second(s)" % (label, minimum))
    if seconds > maximum:
        raise ValidationError("%s is too large (max %d seconds)" % (label, maximum))
    return seconds


def parse_datetime(text, now=None):
    """Parse 'YYYY-MM-DD HH:MM[:SS]' or 'HH:MM' (next occurrence) into an epoch timestamp."""
    now = time.time() if now is None else now
    text = str(text).strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        clock = datetime.strptime(text, "%H:%M")
    except ValueError:
        raise ValidationError(
            "invalid date/time %r (use 'YYYY-MM-DD HH:MM[:SS]' or 'HH:MM')" % text)
    today = datetime.fromtimestamp(now).replace(
        hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    if today.timestamp() <= now:
        today += timedelta(days=1)
    return today.timestamp()
