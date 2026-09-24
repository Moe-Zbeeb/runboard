import sys

from .client import Run, sync

__all__ = ["init", "log", "finish", "Run", "sync", "run"]
__version__ = "0.2.0"

run = None
_orig_excepthook = sys.excepthook


def _excepthook(exc_type, exc, tb):
    if run is not None and not issubclass(exc_type, KeyboardInterrupt):
        run.finish("crashed")
    elif run is not None:
        run.finish("killed")
    _orig_excepthook(exc_type, exc, tb)


def init(project="default", name=None, config=None, **kwargs):
    global run
    if run is not None:
        run.finish()
    run = Run(project=project, name=name, config=config, **kwargs)
    sys.excepthook = _excepthook
    return run


def log(data, step=None):
    if run is None:
        raise RuntimeError("call runboard.init() first")
    run.log(data, step=step)


def finish(status="finished"):
    global run
    if run is not None:
        run.finish(status)
        run = None
