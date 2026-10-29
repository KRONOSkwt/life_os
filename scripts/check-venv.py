"""Fail before installation or startup if the project interpreter is not isolated."""

import site
import sys
from pathlib import Path


def main() -> None:
    expected = Path(sys.argv[1]).resolve()
    config = expected / "pyvenv.cfg"
    isolated = (
        sys.version_info >= (3, 12)
        and sys.prefix != sys.base_prefix
        and Path(sys.prefix).resolve() == expected
        and site.ENABLE_USER_SITE is False
        and config.is_file()
    )
    if isolated:
        values = {}
        for line in config.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip().lower()] = value.strip().lower()
        isolated = values.get("include-system-site-packages") == "false"
    if not isolated:
        raise SystemExit("Se requiere .venv aislado con Python 3.12 o posterior; no se instalaron dependencias.")
    print(f"OK: entorno virtual aislado, Python {sys.version.split()[0]}.")


if __name__ == "__main__":
    main()
