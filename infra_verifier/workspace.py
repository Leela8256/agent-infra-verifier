"""Before/after audit of a fresh working tree; deliberately not a sandbox."""

import hashlib
import os
import stat
from pathlib import Path


def snapshot(root):
    result, errors = {}, []

    def visit(directory):
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            errors.append(str(error))
            return
        for entry in entries:
            path = Path(entry.path)
            name = path.relative_to(root).as_posix()
            # Inspect output itself so replacing it with a symlink is detected.
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    result[name] = {"type": "symlink", "target": os.readlink(path)}
                elif stat.S_ISDIR(info.st_mode):
                    result[name] = {"type": "directory"}
                    if name != "output":
                        visit(path)
                elif stat.S_ISREG(info.st_mode):
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(65536), b""):
                            digest.update(chunk)
                    result[name] = {"type": "file", "sha256": digest.hexdigest()}
                else:
                    result[name] = {"type": "special", "mode": info.st_mode}
            except OSError as error:
                errors.append(f"{name}: {error}")

    visit(root)
    return {"entries": result, "errors": errors}


def audit(before, after):
    first, last = before["entries"], after["entries"]
    changes = [
        {
            "path": name,
            "change": "created"
            if name not in first
            else "deleted"
            if name not in last
            else "modified",
        }
        for name in sorted(first.keys() | last.keys())
        if first.get(name) != last.get(name)
    ]
    return {
        "method": "fresh_working_directory_before_after",
        "changes_outside_output": changes,
        "errors": before["errors"] + after["errors"],
        "limitations": "Only persistent changes within the working directory are visible. Absolute/parent-path writes elsewhere and create-then-delete writes are not detected. Symlinks are not followed. This is not filesystem containment.",
    }
