# build script for whatsapp extensions

import os
import subprocess
from pathlib import Path


def main():
    # gobin = Path(os.getenv("HOME")) / "go" / "bin"
    # os.environ["PATH"] = str(gobin) + ":" + os.environ["PATH"]
    # subprocess.run(["go", "install", "github.com/go-python/gopy@latest"], check=True)
    # subprocess.run(
    #     ["go", "install", "golang.org/x/tools/cmd/goimports@latest"], check=True
    # )
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


if __name__ == "__main__":
    main()
