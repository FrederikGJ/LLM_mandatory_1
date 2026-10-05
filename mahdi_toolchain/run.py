"""Cross-platform entry point. Run from mahdi_toolchain in IntelliJ's terminal."""

import sys

if __name__ == "__main__":
    if sys.version_info < (3, 12):  # noqa: UP036 - check before importing dependencies
        print("Brug Python 3.12 eller nyere til denne toolchain.")
        raise SystemExit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "prepare-backend":
        from mh_toolchain.bootstrap import main
    else:
        try:
            from mh_toolchain.cli import main
        except ModuleNotFoundError as error:
            print(f"Mangler Python-pakke: {error.name}. Installér requirements.txt i din .venv.")
            raise SystemExit(1) from error
    raise SystemExit(main())
