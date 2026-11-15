"""Shared, artifact-backed phase checkpoints. Labels are observations, not proof of thought."""

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path


class PhaseEvidence:
    def __init__(self, store, output, evidence_dir):
        self.store, self.output = store, Path(output)
        self.directory = Path(evidence_dir)
        self.directory.mkdir()
        self.records = []

    def capture(self, data):
        phase = data.get("phase")
        if phase not in {"draft", "check", "fix"}:
            raise ValueError("phase must be draft, check, or fix")
        findings = data.get("findings")
        if phase == "check" and (
            not isinstance(findings, list)
            or any(
                not isinstance(f, str) or not f.strip() or len(f) > 2000
                for f in findings
            )
        ):
            raise ValueError(
                "check requires findings: an array of strings; [] means no issues"
            )
        resolution = data.get("resolution")
        if phase == "fix" and (
            not isinstance(resolution, str)
            or not resolution.strip()
            or len(resolution) > 4000
        ):
            raise ValueError(
                "fix requires a resolution description (including a justified no-op)"
            )
        artifacts = {}
        if self.output.is_symlink():
            raise ValueError("output directory must not be a symlink")
        for name in ("blog.md", "meta.json"):
            path = self.output / name
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("phase evidence must be a regular file")
                    content = source.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("phase artifact exceeds 1 MiB")
                artifacts[name] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "text": content.decode("utf8", errors="replace"),
                }
            except FileNotFoundError:
                continue
        record = {
            "phase": phase,
            "offset_s": time.monotonic() - self.store.started,
            "artifacts": artifacts,
            "findings": findings,
            "resolution": resolution,
        }
        index = len(self.records) + 1
        path = self.directory / f"{index:03}-{phase}.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        self.records.append(record)
        self.store.add(
            {
                "run_id": self.store.run_id,
                "event_id": str(uuid.uuid4()),
                "kind": "phase",
                "name": phase,
                "start_offset_s": record["offset_s"],
                "duration_s": 0,
                "source": "artifact_checkpoint",
                "evidence_file": path.name,
            }
        )
        return {"checkpoint": index, "phase": phase}

    def result(self, final_hashes):
        names = [r["phase"] for r in self.records]
        issues = []
        if (
            not names
            or names[0] != "draft"
            or names[-1] != "fix"
            or not {"draft", "check", "fix"} <= set(names)
        ):
            issues.append(
                "need ordered draft, check, fix evidence ending at the final fix"
            )
        if any("blog.md" not in r["artifacts"] for r in self.records):
            issues.append("each phase needs an existing blog.md snapshot")
        if not issues:
            last_hash, checked = None, None
            for record in self.records:
                digest = {k: v["sha256"] for k, v in record["artifacts"].items()}
                if record["phase"] == "draft":
                    last_hash, checked = digest, None
                elif record["phase"] == "check":
                    if digest != last_hash:
                        issues.append("artifact changed before the check checkpoint")
                    checked = record
                else:
                    if checked is None:
                        issues.append("fix has no preceding check")
                    elif checked["findings"] and digest == {
                        k: v["sha256"] for k, v in checked["artifacts"].items()
                    }:
                        issues.append(
                            "check reported issues but fix did not change either artifact"
                        )
                    last_hash, checked = digest, None
            if last_hash != final_hashes:
                issues.append("final artifacts differ from the fix checkpoint")
        return {
            "status": "evidenced"
            if not issues
            else "missing_evidence"
            if not names
            else "inconsistent",
            "phases": names,
            "issues": issues,
            "count": len(names),
            "provenance": "Owner-declared phase labels with host-captured artifact snapshots; does not prove internal reasoning or correctness of the check.",
        }
