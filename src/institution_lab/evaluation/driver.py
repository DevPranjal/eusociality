"""Runs inside the unprivileged candidate container, with no expected answers."""

import importlib
import json
import sys


def main() -> None:
    print(json.dumps({"evaluator_ready": True}), flush=True)
    checks = json.load(sys.stdin)
    results = []
    for check in checks:
        try:
            function = importlib.import_module("operations." + check["operation"]).run
            result = {
                "operation": check["operation"],
                "outputs": [function(value) for value in check["inputs"]],
            }
        except (
            ImportError,
            AttributeError,
            ValueError,
            TypeError,
            KeyError,
            NotImplementedError,
            ZeroDivisionError,
            IndexError,
        ) as error:
            result = {
                "operation": check["operation"],
                "error": type(error).__name__,
                "detail": str(error),
            }
        results.append(result)
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
