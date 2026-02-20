#!/usr/bin/env python3
"""Repo Oracle: AI-first repository analyst powered by OpenGradient.

Main modes:
- ask: answer deep questions about a local codebase with file citations
- review: analyze git diff and report high-signal findings

The model is used in two explicit reasoning stages:
1) Planner: selects relevant files and analysis focus
2) Analyst: produces final answer/findings with citations
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import opengradient as og


DEFAULT_MODEL_PRIORITY = [
    ("GPT_4O", og.TEE_LLM.GPT_4O),
    ("CLAUDE_3_5_HAIKU", og.TEE_LLM.CLAUDE_3_5_HAIKU),
    ("GEMINI_2_0_FLASH", og.TEE_LLM.GEMINI_2_0_FLASH),
]

EXCLUDE_GLOBS = [
    ".git/*",
    "node_modules/*",
    ".venv/*",
    "venv/*",
    "dist/*",
    "build/*",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.mp4",
    "*.mp3",
    "*.pdf",
    "*.lock",
]

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".bash",
    ".zsh",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
}

GITHUB_HOSTS = {"github.com", "www.github.com"}


@dataclass
class PlannerResult:
    files: List[str]
    focus: str


@dataclass
class OracleClient:
    client: og.Client
    model_name: str
    model: Any


@dataclass
class AskResult:
    model_name: str
    answer: str
    planner_files: List[str]
    planner_focus: str


@dataclass
class ReviewResult:
    model_name: str
    answer: str
    diff_empty: bool


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _normalize_repo_source(raw: str) -> str:
    src = raw.strip()
    if src.startswith("github.com/"):
        return f"https://{src}"
    return src


def _looks_like_github_source(src: str) -> bool:
    return src.startswith(
        (
            "https://github.com/",
            "http://github.com/",
            "https://www.github.com/",
            "http://www.github.com/",
            "git@github.com:",
        )
    )


def _parse_github_clone_url(src: str) -> Tuple[str, str]:
    if src.startswith("git@github.com:"):
        path = src.split(":", 1)[1]
    else:
        parsed = urlparse(src)
        if parsed.netloc.lower() not in GITHUB_HOSTS:
            raise ValueError("Only github.com repository links are supported.")
        path = parsed.path

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("GitHub link must include owner/repo.")
    owner = parts[0].strip()
    repo = parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("GitHub link must include owner/repo.")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    slug = f"{owner}__{repo}"
    return clone_url, slug


def _run_git(args: Sequence[str]) -> str:
    proc = subprocess.run(["git", *args], text=True, capture_output=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git command failed").strip()
        raise RuntimeError(err)
    return (proc.stdout or "").strip()


def _ensure_github_repo_local(src: str) -> Path:
    clone_url, slug = _parse_github_clone_url(src)
    cache_root = Path(tempfile.gettempdir()) / "repo_oracle_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    repo_dir = cache_root / slug

    if (repo_dir / ".git").exists():
        try:
            _run_git(["-C", str(repo_dir), "pull", "--ff-only"])
        except Exception:
            # Keep existing local cache if remote update fails.
            pass
        return repo_dir

    if repo_dir.exists():
        raise RuntimeError(f"Cache path exists but is not a git repository: {repo_dir}")

    _run_git(["clone", "--depth", "1", clone_url, str(repo_dir)])
    return repo_dir


def resolve_repo_source(root_raw: Optional[str], default_root: Optional[Path] = None) -> Path:
    src = _normalize_repo_source(root_raw or "")
    if not src:
        if default_root is None:
            raise ValueError("Repository source is required.")
        root = default_root.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Invalid root path: {root}")
        return root

    if _looks_like_github_source(src):
        return _ensure_github_repo_local(src).resolve()

    root = Path(src).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Invalid root path: {root}")
    return root


def load_private_key() -> Optional[str]:
    key = os.getenv("OG_PRIVATE_KEY")
    if key:
        return key.strip()

    cfg_path = Path.home() / ".opengradient_config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            cfg_key = data.get("private_key")
            if isinstance(cfg_key, str) and cfg_key.strip():
                return cfg_key.strip()
        except Exception:
            return None
    return None


def init_oracle_client(force_model: Optional[str] = None) -> OracleClient:
    private_key = load_private_key()
    if not private_key:
        raise RuntimeError("Missing OG_PRIVATE_KEY and ~/.opengradient_config.json private_key")

    client = og.Client(private_key=private_key)
    if hasattr(client.llm, "ensure_opg_approval"):
        try:
            client.llm.ensure_opg_approval(opg_amount=1.0)
        except Exception as exc:
            eprint(f"[warn] ensure_opg_approval failed: {exc}")

    if force_model:
        for name, model in DEFAULT_MODEL_PRIORITY:
            if force_model == name or force_model == str(model):
                return OracleClient(client=client, model_name=name, model=model)
        return OracleClient(client=client, model_name=force_model, model=force_model)

    for name, model in DEFAULT_MODEL_PRIORITY:
        try:
            result = client.llm.chat(
                model=model,
                messages=[
                    {"role": "system", "content": "Reply with READY only."},
                    {"role": "user", "content": "Ping"},
                ],
                max_tokens=10,
                temperature=0.0,
                x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH,
            )
            content = ((result.chat_output or {}).get("content") or "").strip().upper()
            if "READY" in content:
                return OracleClient(client=client, model_name=name, model=model)
        except Exception:
            continue

    # fallback to first model even if probe failed
    name, model = DEFAULT_MODEL_PRIORITY[0]
    return OracleClient(client=client, model_name=name, model=model)


def chat(oracle: OracleClient, system: str, user: str, max_tokens: int = 900, temperature: float = 0.2) -> str:
    result = oracle.client.llm.chat(
        model=oracle.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH,
    )
    return ((result.chat_output or {}).get("content") or "").strip()


def matches_any(path_text: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path_text, pat) for pat in patterns)


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    # extension-less files (Dockerfile, Makefile, etc.)
    if not path.suffix:
        return True
    return False


def iter_repo_files(root: Path) -> List[Path]:
    out: List[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if matches_any(rel, EXCLUDE_GLOBS):
            continue
        if not is_text_file(p):
            continue
        out.append(p)
    return out


def read_text_safely(path: Path, max_chars: int = 20000) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if len(content) > max_chars:
        return content[:max_chars] + "\n...<truncated>..."
    return content


def build_file_catalog(root: Path, files: Sequence[Path]) -> str:
    lines = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        size = f.stat().st_size
        lines.append(f"{rel} (bytes={size})")
    return "\n".join(lines)


def extract_json(raw: str) -> Optional[dict]:
    text = raw.strip()
    if not text:
        return None

    if "```" in text:
        chunks = text.split("```")
        for chunk in chunks:
            piece = chunk.strip()
            if piece.startswith("json"):
                piece = piece[4:].strip()
            if piece.startswith("{") and piece.endswith("}"):
                try:
                    return json.loads(piece)
                except Exception:
                    pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def planner_stage(oracle: OracleClient, question: str, root: Path, files: Sequence[Path], max_files: int) -> PlannerResult:
    catalog = build_file_catalog(root, files)
    system = (
        "You are a repository investigation planner. "
        "Return STRICT JSON only with keys: files (array of paths), focus (string). "
        "Pick files that are most relevant to answering the user's question."
    )
    user = (
        f"Question:\n{question}\n\n"
        f"Repository file catalog:\n{catalog}\n\n"
        f"Constraints: choose at most {max_files} files, no invented paths."
    )

    raw = chat(oracle, system=system, user=user, max_tokens=450, temperature=0.0)
    data = extract_json(raw) or {}

    picked = data.get("files") if isinstance(data, dict) else None
    focus = data.get("focus") if isinstance(data, dict) else ""

    selected: List[str] = []
    if isinstance(picked, list):
        catalog_set = {p.relative_to(root).as_posix() for p in files}
        for item in picked:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s in catalog_set and s not in selected:
                selected.append(s)
            if len(selected) >= max_files:
                break

    if not selected:
        # fallback: naive keyword ranking
        words = [w.lower() for w in re.findall(r"[a-zA-Z_]{3,}", question)]
        scored: List[Tuple[int, str]] = []
        for p in files:
            rel = p.relative_to(root).as_posix()
            score = sum(1 for w in words if w in rel.lower())
            scored.append((score, rel))
        scored.sort(reverse=True)
        selected = [rel for score, rel in scored[:max_files] if score > 0]
        if not selected:
            selected = [p.relative_to(root).as_posix() for p in files[:max_files]]

    if not isinstance(focus, str) or not focus.strip():
        focus = "Trace the most relevant logic, data flow, and edge cases before answering."

    return PlannerResult(files=selected, focus=focus.strip())


def gather_context(root: Path, rel_paths: Sequence[str], per_file_chars: int = 12000) -> str:
    chunks: List[str] = []
    for rel in rel_paths:
        p = root / rel
        if not p.exists() or not p.is_file():
            continue
        text = read_text_safely(p, max_chars=per_file_chars)
        if not text:
            continue
        chunks.append(f"### FILE: {rel}\n{text}")
    return "\n\n".join(chunks)


def ask_mode(oracle: OracleClient, root: Path, question: str, max_files: int) -> int:
    result = run_ask(oracle=oracle, root=root, question=question, max_files=max_files)
    print(f"Model: {result.model_name}")
    print("=" * 72)
    print(result.answer)
    print("=" * 72)
    print("Planner selected files:")
    for rel in result.planner_files:
        print(f"- {rel}")
    return 0


def run_ask(oracle: OracleClient, root: Path, question: str, max_files: int) -> AskResult:
    files = iter_repo_files(root)
    if not files:
        raise RuntimeError("No text files found in repository root.")

    planner = planner_stage(oracle, question=question, root=root, files=files, max_files=max_files)
    context = gather_context(root, planner.files)

    system = (
        "You are a senior software analyst. "
        "Answer using only provided repository context. "
        "When citing evidence, include inline citations like [path]. "
        "If uncertain, say what is missing explicitly."
    )
    user = (
        f"Question:\n{question}\n\n"
        f"Analysis focus from planner:\n{planner.focus}\n\n"
        f"Context files:\n{context}\n\n"
        "Output format:\n"
        "1) Direct answer\n"
        "2) Evidence bullets with citations\n"
        "3) Risks or unknowns"
    )

    answer = chat(oracle, system=system, user=user, max_tokens=1400, temperature=0.1)
    return AskResult(
        model_name=oracle.model_name,
        answer=answer,
        planner_files=planner.files,
        planner_focus=planner.focus,
    )


def git_diff_text(root: Path, target: str) -> str:
    cmd = ["git", "-C", str(root), "diff", "--unified=0"]
    clean_target = (target or "").strip()
    if clean_target:
        cmd.append(clean_target)
    # Restrict diff scope to the selected root (avoids reviewing unrelated repo paths).
    cmd.extend(["--", "."])
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        out = exc.output or ""
        lower = out.lower()
        if "fatal:" in lower and "diff --git" not in lower:
            raise RuntimeError(out.strip())
    return out


def review_mode(oracle: OracleClient, root: Path, target: str) -> int:
    result = run_review(oracle=oracle, root=root, target=target)
    if result.diff_empty:
        print("No diff to review.")
        return 0
    print(f"Model: {result.model_name}")
    print("=" * 72)
    print(result.answer)
    return 0


def run_review(oracle: OracleClient, root: Path, target: str) -> ReviewResult:
    diff = git_diff_text(root, target=target)
    if not diff.strip():
        return ReviewResult(model_name=oracle.model_name, answer="", diff_empty=True)

    system = (
        "You are a strict code reviewer. "
        "Prioritize bugs, regressions, security, and missing tests. "
        "Output concise findings ordered by severity."
    )
    user = (
        "Review the following git diff.\n"
        "Return:\n"
        "- Findings (severity, file, why it matters, suggested fix)\n"
        "- Open questions\n"
        "- Residual risk\n\n"
        f"DIFF:\n{diff}"
    )

    answer = chat(oracle, system=system, user=user, max_tokens=1500, temperature=0.0)
    return ReviewResult(model_name=oracle.model_name, answer=answer, diff_empty=False)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="repo_oracle",
        formatter_class=argparse.RawTextHelpFormatter,
        description=textwrap.dedent(
            """\
            Repo Oracle (AI-first codebase tool)

            Examples:
              python repo_oracle.py ask "How does auth work?" --root .
              python repo_oracle.py review --root . --target HEAD~1
            """
        ),
    )
    p.add_argument(
        "--root",
        default=".",
        help="Repository root path OR GitHub URL (https://github.com/owner/repo)",
    )
    p.add_argument("--model", default=None, help="Optional model name or CID")

    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="Ask deep questions about repository code")
    ask.add_argument("question", help="Natural-language question")
    ask.add_argument("--max-files", type=int, default=8, help="Max files for AI context")

    review = sub.add_parser("review", help="AI review of git diff")
    review.add_argument("--target", default="", help="Diff target (default: working tree)")

    return p.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        root = resolve_repo_source(args.root, default_root=Path("."))
        oracle = init_oracle_client(force_model=args.model)
        if args.cmd == "ask":
            return ask_mode(oracle, root=root, question=args.question, max_files=max(1, args.max_files))
        if args.cmd == "review":
            return review_mode(oracle, root=root, target=args.target)
    except Exception as exc:
        eprint(f"Error: {exc}")
        return 1

    eprint(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
