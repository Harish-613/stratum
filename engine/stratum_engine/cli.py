import argparse
import json
from pathlib import Path

from stratum_engine.parse import parse_python

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(prog="stratum")
    parser.add_argument("command", choices=["version", "parse"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()

    if args.command == "version":
        print(json.dumps({"version": VERSION}))
    elif args.command == "parse":
        source = Path(args.file).read_bytes()
        print(json.dumps(parse_python(source)))


if __name__ == "__main__":
    main()