"""Runs model-written Python for the run_python tool (executed as a subprocess).

usage: python sandbox_runner.py <code-file>

A guard rail, not a security boundary: network access is blocked inside Python, the environment is scrubbed by the
caller, and the working directory is the chat's own folder. Matplotlib figures are saved as PNG files so the UI can
show them.
"""
import os
import socket
import sys


def _block_network() -> None:
    def refuse(*_a, **_k):
        raise OSError("Network access is disabled in the code sandbox.")

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse


_user_saved = False
_auto_n = 0


def _setup_matplotlib() -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
    except Exception:
        return

    orig_savefig = Figure.savefig

    def savefig(self, *a, **k):
        global _user_saved
        _user_saved = True
        return orig_savefig(self, *a, **k)

    Figure.savefig = savefig
    plt.show = lambda *a, **k: _save_open_figures(False)


def _save_open_figures(auto_only_if_unsaved: bool = True) -> None:
    global _auto_n
    if auto_only_if_unsaved and _user_saved:
        return
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    for num in plt.get_fignums():
        _auto_n += 1
        name = f"figure_{_auto_n}.png"
        while os.path.exists(name):
            _auto_n += 1
            name = f"figure_{_auto_n}.png"
        plt.figure(num).savefig(name, dpi=120, bbox_inches="tight")
        plt.close(num)


def main() -> int:
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    _block_network()
    _setup_matplotlib()
    scope = {"__name__": "__main__", "__file__": "main.py"}
    try:
        exec(compile(source, "main.py", "exec"), scope)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        _save_open_figures()
        return code
    except BaseException:
        import traceback

        traceback.print_exc()
        _save_open_figures()
        return 1
    _save_open_figures()
    return 0


if __name__ == "__main__":
    sys.exit(main())
