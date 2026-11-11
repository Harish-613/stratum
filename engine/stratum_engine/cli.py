import argparse
import json
import sys
from pathlib import Path

from stratum_engine.analyze import analyze, to_dot
from stratum_engine.languages import language_for
from stratum_engine.parse import parse_file, parse_python
from stratum_engine.scan import scan_all, scan_folder

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(prog="stratum")
    parser.add_argument("command", choices=["version", "parse", "scan", "symbols", "scan-all", "analyze", "dot", "serve", "docs"])
    parser.add_argument("file", nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="docs: show the chunk plan without calling an LLM")
    parser.add_argument("--provider", choices=["anthropic", "openai"], help="docs: LLM provider")
    parser.add_argument("--model", help="docs: model name")
    parser.add_argument("--only", nargs="*", help="docs: limit to these repo-relative files")
    args = parser.parse_args()

    if args.command == "version":
        print(json.dumps({"version": VERSION}))
    elif args.command == "parse":
        source = Path(args.file).read_bytes()
        print(json.dumps(parse_python(source)))
    elif args.command == "scan":
        print(json.dumps(scan_folder(args.file), indent=2))
    elif args.command == "symbols":
        language = language_for(args.file)
        if language is None:
            sys.exit(f"stratum: unsupported file type: {args.file}")
        print(json.dumps(parse_file(Path(args.file).read_bytes(), language), indent=2))
    elif args.command == "scan-all":
        print(json.dumps(scan_all(args.file), indent=2))
    elif args.command == "analyze":
        print(json.dumps(analyze(args.file), indent=2))
    elif args.command == "dot":
        print(to_dot(analyze(args.file)))
    elif args.command == "docs":
        from stratum_engine import docs

        report = analyze(args.file)
        if args.dry_run:
            print(json.dumps(docs.plan(args.file, args.only or sorted(report["files"])), indent=2))
        else:
            llm = docs.llm_from_env(args.provider, args.model)
            print(json.dumps(docs.generate_docs(args.file, report, llm, args.only), indent=2))
    elif args.command == "serve":
        from stratum_engine.server import serve

        serve()


if __name__ == "__main__":
    main()
