#!/usr/bin/env python3
"""Transactional workflow bookkeeping. Python 3.9+, macOS/Linux, no dependencies."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util

# Load the sibling helper even when another process imports this CLI by file path.
_trace_spec = importlib.util.spec_from_file_location("hotl_traceability", Path(__file__).with_name("traceability.py"))
_trace = importlib.util.module_from_spec(_trace_spec)
_trace_spec.loader.exec_module(_trace)
_dashboard_spec = importlib.util.spec_from_file_location("hotl_dashboard", Path(__file__).with_name("dashboard.py"))
_dashboard = importlib.util.module_from_spec(_dashboard_spec)
_dashboard_spec.loader.exec_module(_dashboard)


FRAMEWORK = "human-on-the-loop-GPT"
PHASES = ("hearing", "requirements", "awaiting_approval", "specification",
          "design", "development", "done")
AUTONOMOUS = {"specification", "design", "development", "done"}
KINDS = {"approval", "change", "bug", "question", "instruction", "stop", "resume"}
TASK = re.compile(r"^- \[([ x>\-])\] (T-[1-9][0-9]*) (.+)$")


class WorkflowError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def atomic_write(path, data):
    require(not path.is_symlink(), "Refusing to replace a symlink: " + str(path))
    fd, temporary = tempfile.mkstemp(prefix=".hotl-tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        require(self.root.is_dir(), "Project directory does not exist")
        self.docs = self.root / "docs"
        require(not self.docs.is_symlink(), "docs must not be a symlink")
        self.path = self.docs / "hotl.state.json"

    def read(self):
        require(self.path.is_file() and not self.path.is_symlink(),
                "No managed state; initialize explicitly for a new project")
        state = json.loads(self.path.read_text())
        require(state.get("framework") == FRAMEWORK and state.get("version") == 1,
                "Unsupported state format; original HOTL states require explicit migration")
        require(state.get("phase") in PHASES and type(state.get("revision")) is int,
                "Invalid phase or revision")
        require(all(isinstance(state.get(key), list) for key in
                    ("events", "inputs", "reviews", "writers")), "Invalid state collections")
        return state

    @contextmanager
    def lock(self):
        self.docs.mkdir(exist_ok=True)
        lock = self.docs / ".hotl.lock"
        require(not lock.is_symlink(), "Lock must not be a symlink")
        with lock.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkflowError("Another workflow command is running; retry after it finishes")
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def event(self, state, kind, text, related=None):
        ident = "E-" + str(len(state["events"]) + 1)
        state["events"].append(dict(id=ident, at=now(), kind=kind,
                                    text=text, related=related))
        return ident

    def log_bytes(self, state):
        lines = ["# AgentTrail", "", "<!-- Generated from hotl.state.json; revision %s. Do not edit. -->" % state["revision"], ""]
        for entry in state["events"]:
            lines += ["## %s [%s] %s" % (entry["id"], entry["kind"], entry["at"]), ""]
            if entry["related"]:
                lines += ["Related: " + entry["related"], ""]
            lines += [entry["text"], ""]
        return "\n".join(lines).encode()

    def questions(self, state):
        # 方針：人に判断してもらう項目だけを集める。AIが処理すべき未処理入力は混ぜない。
        rows = copy.deepcopy(state.get("questions", []))
        for row in rows:
            row["stale"] = any(self.question_target_hash(path) != sha for path, sha in row["targets"].items())
        if state.get("presented"):
            presented = state["presented"]
            rows.insert(0, dict(id="approval:" + presented["sha256"][:12], kind="requirements_approval",
                title="提示した要件の承認", reason=presented["summary"], related=["docs/requirements.md"],
                status="open", blocking=True, recommendation="提示した内容と差分を確認してください",
                options=[], stale=not self.question_target_hash("docs/requirements.md") == presented["sha256"],
                targets={"docs/requirements.md": presented["sha256"]}))
        return rows

    def question_target_hash(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.root):
            return None
        return digest(path.read_bytes())

    def questions_bytes(self, state):
        def safe(value):
            return str(value).replace("|", "\\|").replace("\n", "<br>")
        lines = ["# あなたの確認待ち", "", "<!-- Generated from hotl.state.json. Do not edit. -->", "",
                 "この一覧は自動生成です。回答はチャットで項目IDとともに伝えてください。未回答を承認として扱いません。", "",
                 "|ID|種類|確認内容・理由|おすすめ・選択肢|関連|状態|", "|---|---|---|---|---|---|"]
        active = [q for q in self.questions(state) if q["status"] == "open"]
        for q in active:
            status = "対象が変更済み・再提示が必要" if q["stale"] else "回答待ち"
            status += "（関連作業を保留）" if q["blocking"] else "（他の作業は続行可）"
            lines.append("|" + "|".join(map(safe, [q["id"], {"requirements_approval":"要件承認", "decision":"方針・選択", "permission":"実行許可", "visual_check":"見た目の確認"}[q["kind"]], q["title"] + "：" + q["reason"],
                q["recommendation"] + " / " + "・".join(q["options"]), ", ".join(q["related"] + list(q["targets"])), status])) + "|")
        if not active:
            lines += ["", "現在、登録された確認待ちはありません。"]
        lines += ["", "## 回答・取り下げ済み", ""]
        for q in self.questions(state):
            if q["status"] != "open":
                lines.append("- %s：%s — %s" % (safe(q["id"]), {"accepted":"承諾済み", "declined":"見送り", "answered":"回答済み", "withdrawn":"取り下げ"}[q["status"]], safe(q.get("answer", q.get("detail", "")))))
        return ("\n".join(lines) + "\n").encode()

    def project_questions(self, state):
        path = self.docs / "user-checks.md"
        require(not path.exists() or b"<!-- Generated from hotl.state.json. Do not edit. -->" in path.read_bytes(),
                "Existing user-checks.md is not a generated file; preserve it and choose an explicit migration")
        atomic_write(path, self.questions_bytes(state))

    def save(self, state):
        state["revision"] += 1
        state["updated_at"] = now()
        atomic_write(self.path, json_bytes(state))
        # The canonical commit is complete. A failed projection is repairable with sync.
        try:
            atomic_write(self.docs / "log.md", self.log_bytes(state))
            self.project_questions(state)
        except (OSError, WorkflowError) as exc:
            print("State saved; generated projections need sync: " + str(exc), file=sys.stderr)

    def document_hash(self, filename="requirements.md"):
        path = self.docs / filename
        require(path.is_file() and not path.is_symlink(), "Missing regular document: " + filename)
        data = path.read_bytes()
        require(data.strip(), "Empty document: " + filename)
        return digest(data)

    def approval_valid(self, state):
        if not state["approval"]:
            return False
        try:
            return state["approval"]["sha256"] == self.document_hash()
        except WorkflowError:
            return False

    def reset(self, state, reason, phase="requirements"):
        self.event(state, "approval-reset", reason + "\nPrevious: " + json.dumps(state["approval"], ensure_ascii=False))
        state.update(approval=None, presented=None, phase=phase, reviews=[])
        state.pop("review_baseline", None)

    def tasks(self):
        path = self.docs / "tasks.md"
        require(path.is_file() and not path.is_symlink(), "Missing docs/tasks.md")
        rows = []
        for line in path.read_text().splitlines():
            if line.startswith("- ["):
                match = TASK.fullmatch(line)
                require(match is not None, "Invalid task row: " + line)
                rows.append(match.groups())
        require(rows, "No tasks")
        require(len({row[1] for row in rows}) == len(rows), "Duplicate task IDs")
        return rows

    def snapshot_files(self):
        def git(*args):
            result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True)
            require(result.returncode == 0, "Git failed: " + result.stderr.decode(errors="replace"))
            return result.stdout
        require(Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip()).resolve() == self.root,
                "Verification requires an independent project Git repository")
        paths = set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")) - {b""}
        paths.update(os.fsencode("docs/" + name) for name in ("requirements.md", "spec.md", "design.md", "tasks.md"))
        output = {}
        for raw in sorted(paths):
            name = os.fsdecode(raw)
            parts = Path(name).parts
            if name in {"docs/hotl.state.json", "docs/log.md", "docs/user-checks.md", "docs/.hotl.lock"}:
                continue
            if parts[0] == ".agents" or name.startswith("docs/reviews/") or any(p.startswith(".hotl-tmp-") for p in parts):
                continue
            require(not any(p == ".env" or (p.startswith(".env.") and p != ".env.example") for p in parts),
                    "Secret environment file must be untracked and gitignored: " + name)
            path = self.root / name
            require(not path.is_symlink(), "Verification scope contains symlink; define a regular-file project: " + name)
            require(path.resolve().is_relative_to(self.root), "File escapes project: " + name)
            require(not path.is_dir(), "Submodule/directory needs separate verification: " + name)
            data = path.read_bytes() if path.exists() else b"<deleted>"
            executable = bool(path.stat().st_mode & 0o111) if path.exists() else False
            output[name] = [digest(data), executable, path.exists()]
        return output

    def snapshot(self):
        output = hashlib.sha256()
        for name, info in self.snapshot_files().items():
            output.update(json_bytes([name, *info]))
        return output.hexdigest()

    def editorial_changes(self, state):
        baseline = state.get("review_baseline")
        require(baseline is not None, "Editorial verification requires an independent completion baseline")
        current = self.snapshot_files()
        changed = sorted(name for name in baseline.keys() | current.keys()
                         if baseline.get(name) != current.get(name))
        for name in changed:
            if name == "docs/tasks.md":
                continue
            path = Path(name)
            allowed = (name == "README.md" or name.startswith(("docs/help/", "docs/guide/")))
            require(allowed and path.suffix == ".md" and path.name not in {"AGENTS.md", "SKILL.md"}
                    and name in current and current[name][2] and not current[name][1],
                    "Editorial verification cannot cover: " + name)
        require(any(name != "docs/tasks.md" for name in changed), "No editorial document changes")
        return changed

    def evidence_hash(self, relative):
        path = self.root / relative
        require(not Path(relative).is_absolute() and path.resolve().is_relative_to(self.docs / "reviews"),
                "Evidence must be a regular file under docs/reviews/")
        require(path.is_file() and not path.is_symlink(), "Missing evidence file")
        require(path.stat().st_size > 0, "Empty evidence")
        return digest(path.read_bytes())

    def trace(self, state):
        # 方針：本文と進捗の正本はMarkdown。ここでは複写せず、参照から現在の一覧を作る。
        # 照合記録は確認した版だけを保存し、承認・実装・検証の代わりにはしない。
        report = _trace.inspect_documents(self.docs)
        alignment = state.get("alignment")
        report["alignment"] = ("not_recorded" if not alignment else
                               "current" if alignment["documents"] == report["documents"] else "needs_reconciliation")
        report["approval"] = "approved" if self.approval_valid(state) else "outdated" if state["approval"] else "not_approved"
        report["pending_inputs"] = [i["id"] for i in state["inputs"] if i["outcome"] is None]
        report["paused"] = state["paused"]
        report["requirements"] = []
        for item in report["items"]:
            if not item["id"].startswith(("R-", "NR-")):
                continue
            specs = [node["id"] for node in report["items"] if node["id"].startswith("S-") and item["id"] in node["refs"]]
            tasks = [node for node in report["items"] if node["id"].startswith("T-")
                     and set(node["refs"]) & set([item["id"]] + specs)]
            report["requirements"].append(dict(id=item["id"], specifications=specs,
                tasks=[dict(id=node["id"], progress=node["progress"]) for node in tasks],
                approval=report["approval"]))
        report["semantics"] = "requires_human_or_agent_review"
        report["verification"] = "not_checked"
        if state["reviews"]:
            try:
                snapshot = self.snapshot()
                latest = {r["role"]: r for r in state["reviews"]}
                fresh = all(r["result"] == "pass" and r["snapshot"] == snapshot
                            and r["evidence_sha256"] == self.evidence_hash(r["evidence"])
                            for r in latest.values())
                report["verification"] = "current_recorded_reviews" if fresh else "stale_or_failed"
                report["review_roles"] = sorted(latest)
            except (WorkflowError, OSError):
                report["verification"] = "unavailable"
        return report

    def summary(self, state):
        log = self.docs / "log.md"
        return dict(project=state["project"], phase=state["phase"], revision=state["revision"],
                    paused=state["paused"], approval_valid=self.approval_valid(state),
                    pending=[i for i in state["inputs"] if i["outcome"] is None],
                    user_checks=[q for q in self.questions(state) if q["status"] == "open"],
                    user_checks_stale=(not (self.docs / "user-checks.md").is_file() or (self.docs / "user-checks.md").is_symlink()
                                       or (self.docs / "user-checks.md").read_bytes() != self.questions_bytes(state)),
                    writers=state["writers"], reviews=state["reviews"],
                    log_stale=log.is_symlink() or not log.is_file() or log.read_bytes() != self.log_bytes(state))

    def execute(self, command, payload, expected=None):
        if command == "status":
            return self.summary(self.read())
        if command == "trace":
            return self.trace(self.read())
        if command == "questions":
            return {"questions": self.questions(self.read())}
        with self.lock():
            if command == "init":
                require(not self.path.exists(), "State already exists; use status")
                require(not any((self.docs / n).exists() for n in
                                ("log.md", "user-checks.md", "requirements.md", "spec.md", "design.md", "tasks.md", "hearing-notes.md", "lessons.md")),
                        "Existing workflow document names; choose a clean project or explicitly migrate")
                require(not (self.root / ".agents/skills/hotl-gpt-pm").exists() and
                        not list(self.root.glob("*/docs/hotl.state.json")),
                        "Workspace root cannot be initialized as a project")
                state = dict(version=1, framework=FRAMEWORK, project=self.root.name,
                             revision=0, created_at=now(), updated_at=now(), phase="hearing",
                             paused=False, approval=None, presented=None,
                             events=[], inputs=[], reviews=[], writers=[])
                self.event(state, "start", nonempty(payload, "request"))
                self.save(state)
                return self.summary(state)
            state = self.read()
            if expected is not None:
                require(state["revision"] == expected, "Stale revision; read status and reconsider the operation")
            if command == "dashboard":
                return _dashboard.generate(self, state, atomic_write, now())
            if command == "sync":
                atomic_write(self.docs / "log.md", self.log_bytes(state))
                self.project_questions(state)
                dashboard = _dashboard.generate(self, state, atomic_write, now())
                return dict(self.summary(state), dashboard=dashboard)
            if command not in {"receive", "note", "dismiss", "resume", "ask", "answer", "withdraw"} and state["approval"] and not self.approval_valid(state):
                self.reset(state, "Approved requirements changed or disappeared")
                self.save(state)
                raise WorkflowError("Approval invalidated. Inspect the requirements difference before proceeding")
            before = copy.deepcopy(state)
            result = self.apply(state, command, payload)
            if state != before:
                self.save(state)
            return {"result": result, **self.summary(state)}

    def apply(self, state, command, p):
        if command == "ask":
            key = nonempty(p, "key")
            require(p.get("kind") in {"decision", "permission", "visual_check"}, "Use present/approve for requirements approval")
            require(type(p.get("blocking")) is bool, "Specify whether this decision blocks related work")
            require(isinstance(p.get("options", []), list) and all(isinstance(x, str) and x.strip() for x in p.get("options", [])), "Invalid options")
            require(isinstance(p.get("related", []), list) and all(isinstance(x, str) and x.strip() for x in p.get("related", [])), "Invalid related references")
            targets = {}
            require(isinstance(p.get("targets", []), list), "targets must be a list of relative file paths")
            for relative in p.get("targets", []):
                require(isinstance(relative, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts
                        and not any(part.startswith(".env") for part in Path(relative).parts), "Invalid or secret target path")
                sha = self.question_target_hash(relative)
                require(sha is not None, "Missing regular target file: " + relative)
                targets[relative] = sha
            body = dict(key=key, kind=p["kind"], title=nonempty(p, "title"), reason=nonempty(p, "reason"),
                        recommendation=nonempty(p, "recommendation"), options=p.get("options", []),
                        related=p.get("related", []), targets=targets, blocking=p["blocking"])
            old = next((q for q in state.get("questions", []) if q["key"] == key), None)
            if old:
                require(all(old[k] == value for k, value in body.items()), "Question key reused with different content or target; withdraw and create a new key")
                return old["id"]
            rows = state.setdefault("questions", [])
            row = dict(id="Q-" + str(len(rows) + 1), status="open", at=now(), input_count=len(state["inputs"]), **body)
            rows.append(row)
            self.event(state, "question", json.dumps(row, ensure_ascii=False), row["id"])
            return row["id"]
        if command in {"answer", "withdraw"}:
            row = next((q for q in state.get("questions", []) if q["id"] == p.get("question_id")), None)
            require(row is not None and row["status"] == "open", "No open question; requirement approvals use approve/reset")
            if command == "withdraw":
                row.update(status="withdrawn", detail=nonempty(p, "detail"))
            else:
                item = get_input(state, p, "instruction")
                require(state["inputs"].index(item) >= row["input_count"], "Answer predates the question")
                require(not next(q for q in self.questions(state) if q["id"] == row["id"])["stale"], "Target changed; withdraw and present a new question")
                require(p.get("outcome") in {"accepted", "declined", "answered"}, "Invalid answer outcome")
                row.update(status=p["outcome"], answer=nonempty(p, "answer"), input_id=item["id"])
                resolve(self, state, item, "answered", row["id"] + ": " + row["answer"])
            return self.event(state, "question-closed", json.dumps(row, ensure_ascii=False), row["id"])
        if command == "align":
            require(not state["paused"], "Paused; alignment must wait for explicit resume")
            report = self.trace(state)
            require(not report["issues"], "Traceability issues: " + "; ".join(report["issues"]))
            state["alignment"] = dict(documents=report["documents"], at=now(),
                                      actor=nonempty(p, "actor"), detail=nonempty(p, "detail"))
            return self.event(state, "alignment", json.dumps(state["alignment"], ensure_ascii=False))
        if command == "check":
            require(state["phase"] not in AUTONOMOUS or self.approval_valid(state), "Autonomous phase has no valid approval")
            return "ok"
        if command == "receive":
            key, message = nonempty(p, "source_key"), nonempty(p, "message")
            intents = p.get("intents")
            require(isinstance(intents, list) and intents, "Provide every distinct intent")
            for intent in intents:
                require(isinstance(intent, dict) and intent.get("kind") in KINDS, "Unknown intent kind")
                nonempty(intent, "text")
            old = [i for i in state["inputs"] if i["source_key"] == key]
            if old:
                require([(i["kind"], i["text"]) for i in old] == [(i["kind"], i["text"]) for i in intents]
                        and old[0]["message"] == message, "Source key reused with different content")
                return [i["id"] for i in old]
            ids = []
            for intent in intents:
                ident = "I-" + str(len(state["inputs"]) + 1)
                item = dict(id=ident, source_key=key, message=message, kind=intent["kind"],
                            text=intent["text"], outcome=None, detail=None)
                state["inputs"].append(item)
                self.event(state, "input", intent["text"], ident)
                if intent["kind"] == "stop":
                    state["paused"] = True
                    item.update(outcome="applied", detail="Paused by user")
                    self.event(state, "resolved", "Paused by user", ident)
                ids.append(ident)
            return ids
        if command == "note":
            require(p.get("kind") in {"decision", "finding", "proposal", "report"}, "Invalid note kind")
            return self.event(state, p["kind"], nonempty(p, "text"), p.get("related"))
        if command == "resume":
            item = get_input(state, p, "resume")
            latest_stop = max((index for index, entry in enumerate(state["inputs"])
                               if entry["kind"] == "stop"), default=-1)
            require(state["inputs"].index(item) > latest_stop,
                    "Resume predates the latest stop; wait for a newer user resume")
            state["paused"] = False
            return resolve(self, state, item, "applied", "Resumed by user")
        if command == "dismiss":
            item = get_input(state, p)
            require(p.get("outcome") in {"superseded", "declined"}, "Dismissal requires superseded or declined")
            return resolve(self, state, item, p["outcome"], nonempty(p, "detail"))
        if command == "resolve":
            item = get_input(state, p)
            require(item["kind"] not in {"approval", "resume", "stop"}, "Use the dedicated command for control inputs")
            outcome, detail = nonempty(p, "outcome"), nonempty(p, "detail")
            require(outcome in {"applied", "answered", "task", "superseded", "declined"}, "Invalid outcome")
            if item["kind"] == "bug" and outcome not in {"superseded", "declined"}:
                require(outcome == "task" and p.get("task_id") in {t[1] for t in self.tasks() if t[0] in {" ", ">"}},
                        "Bug reports must link to an unfinished task")
                detail = p["task_id"] + ": " + detail
                state["reviews"] = []
            if item["kind"] == "change" and outcome not in {"superseded", "declined"}:
                require(state["approval"] is None, "Reset approval before applying requirement changes")
            return resolve(self, state, item, outcome, detail)
        require(not state["paused"], "Paused; process an explicit user resume input first")
        if command == "reset":
            phase = p.get("phase", "requirements")
            require(phase in {"hearing", "requirements"}, "Reset target must be hearing or requirements")
            self.reset(state, nonempty(p, "reason"), phase)
            return phase
        if command == "transition":
            target = nonempty(p, "phase")
            allowed = {"hearing": "requirements", "specification": "design", "design": "development"}
            require(allowed.get(state["phase"]) == target, "Transition requires a dedicated gate or is out of order")
            if target in AUTONOMOUS:
                self.guard(state)
                self.document_hash("spec.md" if target == "design" else "design.md")
            state["phase"] = target
            return self.event(state, "phase", target)
        if command == "present":
            require(state["phase"] in {"requirements", "awaiting_approval"}, "Not in requirements phase")
            state["presented"] = dict(sha256=self.document_hash(), at=now(),
                                      input_count=len(state["inputs"]), summary=nonempty(p, "summary"))
            state["phase"] = "awaiting_approval"
            self.event(state, "presented", json.dumps(state["presented"], ensure_ascii=False))
            return state["presented"]
        if command == "approve":
            require(state["phase"] == "awaiting_approval" and state["presented"], "No presented requirements")
            item = get_input(state, p, "approval")
            require(state["inputs"].index(item) >= state["presented"]["input_count"], "Approval predates the presented requirements")
            require(not any(i["kind"] == "change" and i["outcome"] is None for i in state["inputs"]),
                    "Unresolved requirement change; reflect it before approval")
            require(p.get("sha256") == state["presented"]["sha256"] == self.document_hash(),
                    "Requirements differ from the version presented to the user")
            state["approval"] = dict(sha256=p["sha256"], by=nonempty(p, "by"), at=now(), input_id=item["id"])
            state.update(phase="specification", presented=None)
            return resolve(self, state, item, "applied", "Approved presented requirements")
        if command == "reopen":
            require(state["phase"] == "done", "Only completed work can be reopened")
            self.guard(state)
            item = get_input(state, p, "bug")
            state.update(phase="development", reviews=[])
            return self.event(state, "reopen", item["text"], item["id"])
        if command == "work":
            self.guard(state)
            require(state["phase"] == "development", "Not in development")
            actor = nonempty(p, "actor")
            if actor not in state["writers"]:
                state["writers"].append(actor)
            state["reviews"] = []
            return self.event(state, "work", actor + ": " + nonempty(p, "detail"))
        if command == "snapshot":
            self.guard(state)
            return self.snapshot()
        if command == "review":
            self.guard(state)
            require(state["phase"] == "development" and state["writers"], "Register implementation actors before review")
            reviewer = nonempty(p, "reviewer")
            editorial = p.get("role") == "editorial"
            if editorial:
                require(reviewer in state["writers"], "Editorial self-verification requires an implementation actor")
                require(p.get("no_behavior_change") is True, "Confirm no behavior, contract, or operational instruction changes")
                changed = self.editorial_changes(state)
                reason = nonempty(p, "reason")
            else:
                require(reviewer not in state["writers"], "Reviewer also implemented this project; use a different context")
            require(p.get("role") in {"code", "acceptance", "ux", "security", "editorial"}, "Unknown review role")
            require(p.get("result") in {"pass", "fail"}, "Review result must be pass or fail")
            require(p.get("snapshot") == self.snapshot(), "Review targets an outdated snapshot")
            evidence = nonempty(p, "evidence")
            review = dict(role=p["role"], reviewer=reviewer, result=p["result"],
                          snapshot=p["snapshot"], evidence=evidence, evidence_sha256=self.evidence_hash(evidence))
            if editorial:
                review.update(reason=reason, no_behavior_change=True, changed=changed)
            if p["result"] == "fail":
                state.pop("review_baseline", None)
            state["reviews"].append(review)
            self.event(state, "review", json.dumps(review, ensure_ascii=False))
            return review
        if command == "complete":
            self.guard(state)
            require(state["phase"] == "development", "Not in development")
            require(not any(i["outcome"] is None for i in state["inputs"]), "Unresolved user inputs remain")
            require(not any(q["status"] == "open" and q["blocking"] for q in self.questions(state)), "Blocking user decisions remain")
            if state.get("alignment"):
                report = self.trace(state)
                require(not report["issues"] and report["alignment"] == "current",
                        "Documents changed or traceability is incomplete; reconcile and align before completion")
            rows = self.tasks()
            require(all(t[0] in {"x", "-"} for t in rows), "Incomplete tasks remain")
            active = [t for t in rows if t[0] != "-"]
            require(active and "[acceptance]" in active[-1][2], "Last active task must be acceptance verification")
            snapshot = self.snapshot()
            latest = {r["role"]: r for r in state["reviews"]}
            independent = {"code", "acceptance"}.issubset(latest)
            require(independent or "editorial" in latest, "Independent code and acceptance reviews required")
            if not independent:
                self.editorial_changes(state)
            for review in latest.values():
                require(review["result"] == "pass" and review["snapshot"] == snapshot,
                        "Failed or outdated review: " + review["role"])
                if review["role"] != "editorial":
                    require(review["reviewer"] not in state["writers"], "Review is not independent")
                require(review["evidence_sha256"] == self.evidence_hash(review["evidence"]), "Review evidence changed")
            state["phase"] = "done"
            if independent:
                state["review_baseline"] = self.snapshot_files()
            return self.event(state, "phase", "done")
        raise WorkflowError("Unknown command: " + command)

    def guard(self, state):
        require(self.approval_valid(state), "Valid requirements approval required")
        require(not any(i["kind"] == "change" and i["outcome"] is None for i in state["inputs"]),
                "Process pending requirement changes first")


def nonempty(value, key):
    result = value.get(key)
    require(isinstance(result, str) and result.strip(), "Nonempty string required: " + key)
    return result


def get_input(state, payload, kind=None):
    items = [i for i in state["inputs"] if i["id"] == payload.get("input_id")]
    require(items, "Unknown input ID")
    item = items[0]
    require(item["outcome"] is None, "Input is already resolved")
    require(kind is None or item["kind"] == kind, "Wrong input kind")
    return item


def resolve(store, state, item, outcome, detail):
    item.update(outcome=outcome, detail=detail)
    store.event(state, "resolved", outcome + ": " + detail, item["id"])
    return item["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Explicit project root")
    parser.add_argument("--expect", type=int, help="Reject stale state revisions")
    parser.add_argument("command", choices=["init", "status", "sync", "check", "receive", "resolve", "dismiss",
                        "resume", "note", "reset", "transition", "present", "approve", "reopen",
                        "work", "snapshot", "review", "complete", "trace", "align", "questions", "ask", "answer", "withdraw", "dashboard"])
    parser.add_argument("--input", help="JSON payload file, or - for stdin; never a shell-interpolated body")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin) if args.input == "-" else (
            json.loads(Path(args.input).read_text()) if args.input else {})
        require(isinstance(payload, dict), "Payload must be a JSON object")
        print(json.dumps(Store(args.project).execute(args.command, payload, args.expect), ensure_ascii=False, indent=2))
    except (WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        print("hotl: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
