import importlib
import json
import sys

if __name__ == "__main__":
    operation = importlib.import_module("operations." + sys.argv[1]).run
    inputs = json.load(sys.stdin)
    print(json.dumps([operation(item) for item in inputs], sort_keys=True))
