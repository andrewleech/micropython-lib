import io
import xmltok2


class ParseError(Exception):
    pass


def parse_el(stream):
    from xml.etree.ElementTree import Element

    stack = []
    root = None
    last = None

    for ev in xmltok2.tokenize(stream):
        typ = ev[0]

        if typ == xmltok2.START_TAG:
            el = Element()
            el.tag = ev[2]
            if not stack:
                root = el
            else:
                stack[-1]._children.append(el)
            stack.append(el)
            last = None

        elif typ == xmltok2.ATTR:
            # Ignore attrs of processing instructions
            if stack:
                stack[-1].attrib[ev[2]] = ev[3]

        elif typ == xmltok2.TEXT:
            if last is None:
                stack[-1].text = ev[1]
            else:
                last.tail = ev[1]

        elif typ == xmltok2.END_TAG:
            if stack[-1].tag != ev[2]:
                raise ParseError("mismatched tag: /%s (expected: /%s)" % (ev[1][1], stack[-1].tag))
            last = stack.pop()

    return root


def parse(source):
    from xml.etree.ElementTree import ElementTree

    return ElementTree(parse_el(source))


def fromstring(data):
    buf = io.StringIO(data)
    return parse_el(buf)
