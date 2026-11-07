"""EasyRemix launcher (this is the PyInstaller entry point)."""
import multiprocessing
import sys


def _main():
    if "--selftest" in sys.argv:
        from easyremix.selftest import run

        sys.exit(run())
    from easyremix.gui import main

    main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _main()
