# build script for whatsapp extensions

import subprocess
import traceback
import warnings
from pathlib import Path


def main():
    try:
        subprocess.run(
            [
                "gopy",
                "build",
                "-output=generated",
                "-no-make=true",
                ".",
            ],
            cwd=Path(".") / "slidge" / "plugins" / "whatsapp",
            check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        traceback.print_exc()
        warnings.warn(
            f"Could not build whatsapp-related libs. "
            f"You can use the other packages, but not whatsapp."
        )


if __name__ == "__main__":
    main()
