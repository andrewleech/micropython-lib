try:
    from .ElementTree_parse import ParseError, parse_el, parse, fromstring
except:
    # install xml.etree.ElementTree-parse package for parsing support.
    pass


class Element:

    def __init__(self):
        self.tag = None
        self.attrib = {}
        self.text = None
        self.tail = None
        self._children = []

    def __getitem__(self, i):
        return self._children[i]

    def __len__(self):
        return len(self._children)


class ElementTree:
    def __init__(self, root):
        self.root = root

    def getroot(self):
        return self.root
