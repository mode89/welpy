"""welpy: the Wayland compositor, plus the user-facing override hook."""

import functools
import sys


def override(target):
    """Replace a function or method with one receiving the previous version as
    its first argument, so user overrides chain. Use as
    ``@welpy.override(target)``."""
    if not callable(target):
        raise TypeError(
            f"welpy.override expects a function; got {type(target).__name__}")
    qualname = target.__qualname__
    home = sys.modules[target.__module__]
    return lambda fn: _install(home, qualname, target, fn)


def _install(module, qualname, target, fn):
    """Install `fn` at `module`'s `qualname`, with `target` curried as first
    arg. A dotted `qualname` (e.g. ``Builder.compile``) patches a class
    attribute; a bare one patches the module."""
    *path, name = qualname.split(".")
    owner = functools.reduce(getattr, path, module)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(target, *args, **kwargs)
    # Rewrite so a later @welpy.override(wrapper) routes here, not to fn's
    # original module/qualname.
    wrapper.__module__ = module.__name__
    wrapper.__name__ = name
    wrapper.__qualname__ = qualname
    setattr(owner, name, wrapper)
    return wrapper
