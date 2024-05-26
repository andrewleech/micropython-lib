"""
Provide subset of CPython api-compatible importlib module.
"""
import sys


def import_module(name, package=None):
    # https://docs.python.org/3/library/importlib.html#importlib.import_module
    if package:
        raise NotImplementedError()
    mods = name.split(".")
    mod = __import__(name)
    while len(mods) > 1:
        mod = getattr(mod, mods.pop(1))
    return mod


def reload(module):
    """
    https://docs.python.org/3/library/importlib.html#importlib.reload
    """
    fullname = module.__name__
    if sys.modules.get(fullname) is not module:
        raise ImportError("module %s not in sys.modules" % fullname)
    sys.modules.pop(fullname)
    newmod = import_module(fullname)
    try:
        # Update parent frame object
        pfglobals = sys._getframe(1).f_globals
        for name, obj in list(pfglobals.items()):
            if obj is module:
                pfglobals[name] = newmod
    except AttributeError:
        # Can't update parent frame without sys._getframe
        pass
    return newmod
