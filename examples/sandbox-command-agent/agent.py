"""A command-mode agent: it reads one run request on stdin and writes one JSON response on stdout.

It needs no Ratel SDK. The contract is protocol/sandbox/v1: the request is
{"v": 1, "runId", "input", "params", "tools", "fixtures"}, and the answer is {"output": ...}.
Logs go to stderr.
"""

import json
import sys


def main() -> None:
    request = json.load(sys.stdin)
    text = str((request.get("input") or {}).get("text", ""))
    style = request.get("params", {}).get("style", "plain")
    print(f"run {request['runId']}: {len(text)} characters", file=sys.stderr)

    words = text.split()
    if style == "shout":
        reply = text.upper()
    elif style == "reverse":
        reply = " ".join(reversed(words))
    else:
        reply = text
    json.dump({"output": {"reply": reply, "words": len(words), "style": style}}, sys.stdout)


if __name__ == "__main__":
    main()
