from .diff import describe_changes
from .impact import dry_run
from .resolve import Resolved, resolve
from .settings import MatchingSettings
from .store import ConfigError, ConfigStore

__all__ = ["ConfigError", "ConfigStore", "MatchingSettings", "Resolved", "describe_changes", "dry_run", "resolve"]
