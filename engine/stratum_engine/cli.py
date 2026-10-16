import argparse
import json
from pathlib import Path

from stratum_engine.parse import parse_python
from stratum_engine.scan import scan_folder

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(prog="stratum")
    parser.add_argument("command", choices=["version", "parse", "scan"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()

    if args.command == "version":
        print(json.dumps({"version": VERSION}))
    elif args.command == "parse":
        source = Path(args.file).read_bytes()
        print(json.dumps(parse_python(source)))
    elif args.command == "scan":
        print(json.dumps(scan_folder(args.file), indent =2))


if __name__ == "__main__":
    main()