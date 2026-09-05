import json
import subprocess
import tempfile
from pathlib import Path


def extract_metadata(
    file_bytes: bytes,
    file_name: str
) -> dict:

    suffix = Path(file_name).suffix

    with tempfile.NamedTemporaryFile(
        suffix=suffix,
        delete=False
    ) as temp_file:

        temp_file.write(file_bytes)
        temp_path = temp_file.name

    try:

        result = subprocess.run(
            [
                "exiftool",
                "-json",
                temp_path
            ],
            capture_output=True,
            text=True,
            check=True
        )

        data = json.loads(result.stdout)

        if data:
            return data[0]

        return {}

    finally:

        Path(temp_path).unlink(
            missing_ok=True
        )