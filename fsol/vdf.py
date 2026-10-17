import re

TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')
ESCAPE = re.compile(r"\\(.)")


def unescape(text):
    return ESCAPE.sub(lambda match: {"n": "\n", "t": "\t"}.get(match.group(1), match.group(1)), text)


def parse(text):
    stack = [{}]
    key = None
    for match in TOKEN.finditer(text):
        string, brace = match.group(1), match.group(2)
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
        elif string is None:
            continue
        elif key is None:
            key = unescape(string)
        else:
            stack[-1][key] = unescape(string)
            key = None
    return stack[0]


def load(path):
    with open(path, encoding="utf-8", errors="replace") as source:
        return parse(source.read())


def find(tree, *keys):
    node = tree
    for wanted in keys:
        if not isinstance(node, dict):
            return None
        node = next((value for key, value in node.items() if key.lower() == wanted.lower()), None)
    return node
