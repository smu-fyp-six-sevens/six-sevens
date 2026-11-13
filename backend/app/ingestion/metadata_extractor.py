import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# exiftool always reports basic file details (FileType, FileSize, MIMEType...)
# for any file it can read, so an empty result means it didn't run, not that
# the file has no metadata. A failure is stored under this key instead of as
# {}, so the metadata check can say it couldn't run rather than reporting a
# file with nothing in it.
EXTRACTION_ERROR_KEY = "MetadataExtractionError"
# A crafted file can hang exiftool; a worker thread must not wait on it for the whole push deadline.
EXIFTOOL_TIMEOUT_SECONDS = 30


def _failure(file_name: str, reason: str) -> dict:
    logger.error("Metadata extraction failed for %s: %s", file_name, reason)
    return {EXTRACTION_ERROR_KEY: reason}


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
            check=True,
            timeout=EXIFTOOL_TIMEOUT_SECONDS,
        )

        data = json.loads(result.stdout)

        if data:
            return data[0]

        return _failure(file_name, "exiftool returned no output")

    except FileNotFoundError:
        return _failure(file_name, "exiftool is not installed on the server that processed this document")

    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[:200] or f"exit code {exc.returncode}"
        return _failure(file_name, f"exiftool failed ({detail})")

    except subprocess.TimeoutExpired:
        return _failure(file_name, f"exiftool timed out after {EXIFTOOL_TIMEOUT_SECONDS} seconds")

    except json.JSONDecodeError:
        return _failure(file_name, "exiftool returned output that couldn't be parsed")

    finally:

        Path(temp_path).unlink(
            missing_ok=True
        )
