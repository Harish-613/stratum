import argparse
import json
import sys
from pathlib import Path

from stratum_engine.languages import language_for
from stratum_engine.parse import parse_file, parse_python
from stratum_engine.scan import scan_all, scan_folder

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(prog="stratum")
    parser.add_argument("command", choices=["version", "parse", "scan", "symbols", "scan-all"])
    parser.add_argument("file", nargs="?")
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


if __name__ == "__main__":
    main()
