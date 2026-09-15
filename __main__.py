"""Entry point for the zipapp build.

`python3 siphon.pyz https://…` fetches something; `python3 siphon.pyz` with a
command runs that command. One archive either way, so that the thing somebody
keeps is a file rather than a folder they have to remember the path to.
"""

import sys


def main():
    import siphon
    # A bare URL is the overwhelmingly common case, and making people type
    # `get` in front of it would be a small tax on every single use.
    argv = sys.argv[1:]
    if argv and (argv[0].startswith("http://") or argv[0].startswith("https://")):
        argv = ["get", *argv]
    return siphon.main(argv)


if __name__ == "__main__":
    sys.exit(main())
