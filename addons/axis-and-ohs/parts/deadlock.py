import re
import sys

WAIT = re.compile(r"RtlpWaitForCriticalSection .* wait timed out in thread ([0-9a-f]+), blocked by ([0-9a-f]+)")


def waits(lines):
    edges = {}
    for line in lines:
        match = WAIT.search(line)
        if match:
            edges[match.group(1)] = match.group(2)
    return edges


def cycle(edges):
    for start in edges:
        seen = []
        thread = start
        while thread in edges and thread not in seen:
            seen.append(thread)
            thread = edges[thread]
        if thread in seen:
            return seen[seen.index(thread):]
    return None


def main(argv):
    if len(argv) != 1:
        print("usage: deadlock.py <log>", file=sys.stderr)
        return 2
    with open(argv[0], errors="replace") as log:
        found = cycle(waits(log))
    if found:
        print("threads in a cycle: " + " -> ".join(found + [found[0]]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
