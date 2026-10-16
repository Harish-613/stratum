import argparse
import json

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(prog="stratum")
    parser.add_argument("command", choices=["version"])
    args = parser.parse_args()

    if args.command == "version":
        print(json.dumps({"version": VERSION}))


if __name__ == "__main__":
    main()