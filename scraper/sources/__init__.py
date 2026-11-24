"""Source registry. To add a new *kind* of source, write a module with a Source subclass
and register it here. To add another site of an existing kind (e.g. another RSS feed),
only edit sources.yaml."""

from .base import RawPost, Source
from .nairaland import NairalandSource
from .rss import RssSource

SOURCE_TYPES: dict[str, type[Source]] = {
    "nairaland": NairalandSource,
    "rss": RssSource,
}


def build_source(config: dict) -> Source:
    try:
        cls = SOURCE_TYPES[config["type"]]
    except KeyError:
        raise ValueError(f"Unknown source type {config.get('type')!r} for {config.get('name')!r}")
    return cls(config)


__all__ = ["RawPost", "Source", "SOURCE_TYPES", "build_source"]
