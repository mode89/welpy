"""welpy: the Wayland compositor, plus the user-facing override hook."""

import functools
import sys


def override(target):
    """Replace a function with one receiving the previous version as its first
    argument, so user overrides chain. Use as ``@welpy.override(target)``."""
    if not callable(target):
        raise TypeError(
            f"welpy.override expects a function; got {type(target).__name__}")
    name = target.__name__
    home = sys.modules[target.__module__]
    return lambda fn: _install(home, name, target, fn)


def _install(module, name, target, fn):
    """Install `fn` at `module.name`, with `target` curried as first arg."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(target, *args, **kwargs)
    # Rewrite so a later @welpy.override(wrapper) routes here, not to fn's
    # original module/name.
    wrapper.__module__ = module.__name__
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    setattr(module, name, wrapper)
    return wrapper
