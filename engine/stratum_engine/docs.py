"""LLM documentation pipeline.

1. Chunk: each file is split at syntax-tree boundaries so a chunk is always a
   run of whole top-level statements (imports, functions, classes). A
   definition too big for the budget is split into its children (a class
   into its methods); only a single leaf statement larger than the budget is
   cut by lines.
2. Summarize chunks, then combine chunk notes into one summary per file.
3. Write ARCHITECTURE.md from the file summaries plus graph facts the engine
   already knows (most-imported files, cycles, entry points).

Every LLM response is cached under .stratum/cache.json keyed by a hash of the
prompt, so re-running after an edit only pays for chunks whose code changed.
"""

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from stratum_engine.languages import language_for, parser_for

CHARS_PER_TOKEN = 4  # rough average for source code
DEFAULT_CHUNK_TOKENS = 1500

# ---------------------------------------------------------------- chunking


def chunk_source(source: bytes, language: str, max_tokens: int = DEFAULT_CHUNK_TOKENS) -> list[dict]:
    """Split a file into chunks of whole AST nodes.

    Returns [{"start_line", "end_line", "text", "tokens"}] covering the file in order.
    """
    tree = parser_for(language).parse(source)
    budget = max_tokens * CHARS_PER_TOKEN
    spans = _pack(tree.root_node, budget)
    lines = source.split(b"\n")
    chunks = []
    for start_row, end_row in spans:
        text = b"\n".join(lines[start_row:end_row + 1]).decode(errors="replace")
        if text.strip():
            chunks.append({
                "start_line": start_row + 1,
                "end_line": end_row + 1,
                "text": text,
                "tokens": max(1, len(text) // CHARS_PER_TOKEN),
            })
    return chunks


def _size(node) -> int:
    return node.end_byte - node.start_byte


def _pack(node, budget: int) -> list[tuple[int, int]]:
    """Greedily group consecutive children into row spans under `budget` bytes."""
    spans: list[tuple[int, int]] = []
    current: list = []
    current_size = 0

    def flush():
        nonlocal current, current_size
        if current:
            spans.append((current[0].start_point[0], current[-1].end_point[0]))
        current, current_size = [], 0

    for child in node.named_children:
        size = _size(child)
        if size > budget:
            flush()
            if child.named_child_count:
                spans.extend(_pack_big(child, budget))
            else:
                spans.extend(_split_rows(child.start_point[0], child.end_point[0], budget, child))
            continue
        if current_size + size > budget:
            flush()
        current.append(child)
        current_size += size
    flush()
    return _merge_touching(spans)


def _pack_big(node, budget: int) -> list[tuple[int, int]]:
    """An oversized definition: pack its body's children, keeping the header
    (e.g. `class Foo(Base):`) attached to the first piece."""
    body = node.child_by_field_name("body") or node
    inner = _pack(body, budget) if body.named_child_count else []
    if not inner:
        return _split_rows(node.start_point[0], node.end_point[0], budget, node)
    first_start, first_end = inner[0]
    inner[0] = (node.start_point[0], first_end)
    if inner[-1][1] < node.end_point[0]:
        inner[-1] = (inner[-1][0], node.end_point[0])  # closing brace
    return inner


def _split_rows(start: int, end: int, budget: int, node) -> list[tuple[int, int]]:
    """Last resort for a single huge leaf: cut by lines."""
    text = node.text.split(b"\n")
    spans, row, size = [], start, 0
    for i, line in enumerate(text):
        size += len(line) + 1
        if size > budget and start + i > row:
            spans.append((row, start + i - 1))
            row, size = start + i, len(line) + 1
    spans.append((row, end))
    return spans


def _merge_touching(spans):
    out = []
    for s in spans:
        if out and s[0] <= out[-1][1]:  # two nodes sharing a row
            out[-1] = (out[-1][0], max(out[-1][1], s[1]))
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------- LLM client


class LLM:
    """Minimal Anthropic / OpenAI chat client over urllib (no SDK dependency)."""

    def __init__(self, provider: str, api_key: str, model: str | None = None, max_tokens: int = 700):
        self.provider = provider
        self.api_key = api_key
        self.model = model or {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5-mini"}[provider]
        self.max_tokens = max_tokens

    def __call__(self, system: str, prompt: str) -> str:
        if self.provider == "anthropic":
            body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                    "messages": [{"role": "user", "content": prompt}]}
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
        body = {"model": self.model, "max_completion_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"].strip()


class CachedLLM:
    """Wraps any (system, prompt) -> str callable with an on-disk cache."""

    def __init__(self, llm, cache_path: Path):
        self.llm = llm
        self.cache_path = cache_path
        self.cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        self.calls = 0
        self.hits = 0

    def __call__(self, system: str, prompt: str) -> str:
        key = hashlib.blake2b(f"{system}\0{prompt}".encode(), digest_size=16).hexdigest()
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        self.calls += 1
        out = self.llm(system, prompt)
        self.cache[key] = out
        return out

    def save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=0))


# ---------------------------------------------------------------- pipeline

SYSTEM = ("You write concise, accurate internal documentation for software engineers. "
          "Describe only what the code shows. Use Markdown. No preamble.")


def _chunk_prompt(path, chunk, total, index):
    return (f"File `{path}`, part {index} of {total} (lines {chunk['start_line']}-{chunk['end_line']}).\n"
            f"In 3-6 bullet points: what this code does, its key functions/classes and how they are used, "
            f"and any non-obvious behavior.\n\n```\n{chunk['text']}\n```")


def _file_prompt(path, notes, file_report):
    deps = ", ".join(file_report.get("imports", [])[:15]) or "none inside the repo"
    return (f"Write documentation for `{path}` from the notes below.\n"
            f"Repo files it imports: {deps}. Imported by {file_report.get('fan_in', 0)} file(s).\n"
            "Format: one-sentence purpose, then a short 'Key pieces' list, then 'Used by / depends on' if relevant.\n\n"
            + "\n\n".join(notes))


def _arch_prompt(report, summaries):
    hubs = sorted(report["files"].items(), key=lambda kv: -kv[1]["fan_in"])[:10]
    facts = [
        f"{report['stats']['files']} files, {report['stats']['loc']} lines; languages: {report['stats']['languages']}",
        "Most imported: " + ", ".join(f"{p} ({f['fan_in']})" for p, f in hubs),
        f"Entry points: {', '.join(report['entry_points'][:15]) or 'none detected'}",
        f"Import cycles: {report['cycles'][:5] or 'none'}",
    ]
    body = "\n\n".join(f"### {p}\n{s}" for p, s in summaries.items())
    return ("Write ARCHITECTURE.md for this repository: overview, main components and how they interact, "
            "data/control flow, and notable risks (cycles, hotspots). Keep it under 600 words.\n\n"
            + "\n".join(f"- {f}" for f in facts) + "\n\nFile summaries:\n\n" + body)


def plan(root: str, paths: list[str], max_tokens: int = DEFAULT_CHUNK_TOKENS) -> list[dict]:
    """Chunk plan without calling an LLM (what `--dry-run` shows)."""
    out = []
    for rel in paths:
        language = language_for(rel)
        if language is None:
            continue
        chunks = chunk_source((Path(root) / rel).read_bytes(), language, max_tokens)
        out.append({"file": rel, "chunks": [{k: c[k] for k in ("start_line", "end_line", "tokens")} for c in chunks]})
    return out


def generate_docs(root: str, report: dict, llm, paths: list[str] | None = None,
                  out_dir: str = ".stratum/docs", max_tokens: int = DEFAULT_CHUNK_TOKENS) -> dict:
    """Run the pipeline. `llm` is any callable (system, prompt) -> str."""
    root_path = Path(root)
    cached = CachedLLM(llm, root_path / ".stratum" / "cache.json")
    targets = paths or sorted(report["files"])
    out = root_path / out_dir
    summaries, written = {}, []
    try:
        for rel in targets:
            language = language_for(rel)
            if language is None or rel not in report["files"]:
                continue
            chunks = chunk_source((root_path / rel).read_bytes(), language, max_tokens)
            if not chunks:
                continue
            notes = [cached(SYSTEM, _chunk_prompt(rel, c, len(chunks), i + 1)) for i, c in enumerate(chunks)]
            summary = notes[0] if len(notes) == 1 else cached(SYSTEM, _file_prompt(rel, notes, report["files"][rel]))
            summaries[rel] = summary
            doc_path = out / (rel + ".md")
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(f"# `{rel}`\n\n{summary}\n")
            written.append(str(doc_path.relative_to(root_path)))
        if summaries and paths is None:
            arch = cached(SYSTEM, _arch_prompt(report, summaries))
            (out / "ARCHITECTURE.md").write_text(arch + "\n")
            written.append(str((out / "ARCHITECTURE.md").relative_to(root_path)))
    finally:
        cached.save()
    return {"written": written, "llm_calls": cached.calls, "cache_hits": cached.hits}


def llm_from_env(provider: str | None = None, model: str | None = None, api_key: str | None = None) -> LLM:
    provider = provider or ("openai" if os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY") else "anthropic")
    key = api_key or os.environ.get("ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY")
    if not key:
        raise RuntimeError(f"no API key: set {'ANTHROPIC_API_KEY' if provider == 'anthropic' else 'OPENAI_API_KEY'}")
    return LLM(provider, key, model)
