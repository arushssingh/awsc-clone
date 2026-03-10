import json
import sys
import os
import traceback
import importlib.util


def main():
    handler_path = os.environ.get("HANDLER", "handler.handler")
    module_name, func_name = handler_path.rsplit(".", 1)

    # Load the handler module from /var/task/
    module_file = f"/var/task/{module_name}.py"
    if not os.path.exists(module_file):
        print(json.dumps({
            "status": "error",
            "error": f"Handler module not found: {module_file}",
        }))
        sys.exit(1)

    spec = importlib.util.spec_from_file_location(module_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    handler_func = getattr(mod, func_name, None)
    if handler_func is None:
        print(json.dumps({
            "status": "error",
            "error": f"Handler function '{func_name}' not found in {module_name}",
        }))
        sys.exit(1)

    # Read event from file
    event = {}
    event_file = "/var/task/_event.json"
    if os.path.exists(event_file):
        with open(event_file) as f:
            event = json.load(f)

    context = {
        "function_name": os.environ.get("FUNCTION_NAME", "unknown"),
        "memory_limit_mb": os.environ.get("MEMORY_LIMIT", "128"),
    }

    try:
        result = handler_func(event, context)
        print(json.dumps({"status": "success", "output": result}, default=str))
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": str(e),
            "trace": traceback.format_exc(),
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
