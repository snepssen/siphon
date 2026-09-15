"""Entry point for the zipapp build.

`python3 siphon.pyz https://…` fetches something; `python3 siphon.pyz` with a
command runs that command. One archive either way, so that the thing somebody
keeps is a file rather than a folder they have to remember the path to.
"""

import sys


def main():
    argv = sys.argv[1:]

    # Nothing at all opens the window. That is what somebody double-clicking
    # the archive means, and it is the only thing they can mean.
    if not argv:
        import app
        return app.main([])

    # A bare URL is the overwhelmingly common case on the command line, and
    # making people type `get` in front of it would be a tax on every use.
    if argv[0].startswith("http://") or argv[0].startswith("https://"):
        argv = ["get", *argv]

    import siphon
    return siphon.main(argv)


if __name__ == "__main__":
    sys.exit(main())
