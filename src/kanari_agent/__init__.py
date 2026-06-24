from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("kanari-agent")
except PackageNotFoundError:
    __version__ = "unknown"
