from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("doorman-agent")
except PackageNotFoundError:
    __version__ = "unknown"
