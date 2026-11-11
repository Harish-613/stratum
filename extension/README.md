# Stratum for VS Code

Interactive dependency graphs for Python, JavaScript/TypeScript, Go and Java projects, with import cycles, unreachable files, duplicate functions and complex code highlighted. The graph updates as you save.

See the [project README](../README.md) for setup, how the analysis works, and benchmarks.

## Commands

- **Stratum: Analyze Workspace**: full scan, then opens the graph
- **Stratum: Show Dependency Graph**: reopen the graph
- **Stratum: Generate Docs (LLM)**: write Markdown docs to `.stratum/docs/`
- **Stratum: Forget Saved API Key**

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `stratum.pythonPath` | repo `.venv` if present, else `python3` | Interpreter with `stratum_engine` installed |
| `stratum.enginePath` | `../engine` | Folder containing `stratum_engine` |
| `stratum.docs.provider` | `anthropic` | `anthropic` or `openai` |
| `stratum.docs.model` | engine default | Model used for docs |
