"""SiteMantle core — website crawling, development director and evidence-first search auditing."""

__version__ = "1.0.0"
__all__ = ["Config", "Crawler", "__version__"]


def __getattr__(name):
    if name == "Config":
        from .network import Config

        return Config
    if name == "Crawler":
        from .engine import Crawler

        return Crawler
    raise AttributeError(name)
