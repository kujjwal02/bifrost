# Contributing

ferry uses Python 3.10 or newer and POSIX shell scripts. Runtime dependencies
come from the Python standard library.

Run the checks from the repository root:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -v
uvx ruff check .
shellcheck -s sh ferry watch.sh remote-open.sh
```

Linux runs the remote script tests. macOS runs the other tests. CI covers
Python 3.10 through 3.14 on Linux and Python 3.12 on macOS.

To test the plugin in herdr, link your checkout:

```sh
herdr plugin link "$PWD"
```

Keep configuration and state outside the checkout. Report security bugs through
the private channel in [SECURITY.md](SECURITY.md).
