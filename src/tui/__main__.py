"""`python -m src.tui` entrypoint -- mirrors `python -m src.cli` and
`python -m src.launch_wrapper`."""
from .app import BridgeApp


def main() -> None:
    BridgeApp().run()


if __name__ == "__main__":
    main()
