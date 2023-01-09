try:
    from .ElementTree_parse import ParseError, parse_el, parse, fromstring
except:
    # install xml.etree.ElementTree-parse package for parsing support.
    pass


class Element:
    def __init__(self, tag=None, attrib=None):
        self.tag = tag
        self.attrib = attrib or {}
        self.text = None
        self.tail = None
        self._children = []

    def __getitem__(self, i):
        return self._children[i]

    def __len__(self):
        return len(self._children)

    def append(self, el):
        self._children.append(el)

    def get(self, key, default=None):
        return self.attrib.get(key, default)

    def set(self, key, value):
        self.attrib[key] = value

    def makeelement(self, tag, attrib):
        return self.__class__(tag, attrib)

    def items(self):
        return self.attrib.items()

    def iter(self, tag=None):
        if tag == "*":
            tag = None
        if tag is None or self.tag == tag:
            yield self
        for e in self._children:
            for e in e.iter(tag):
                yield e


class ElementTree:
    def __init__(self, root):
        self.root = root

    def getroot(self):
        return self.root

    def write(
        self,
        file_or_filename,
        # keyword arguments
        encoding=None,
        xml_declaration=None,
        default_namespace=None,
        method=None,
    ):
        # assert self._root is not None
        if not method:
            method = "xml"
        elif method not in _serialize:
            # FIXME: raise an ImportError for c14n if ElementC14N is missing?
            raise ValueError("unknown method %r" % method)
        if hasattr(file_or_filename, "write"):
            file = file_or_filename
        else:
            file = open(file_or_filename, "wb")
        write = file.write
        if not encoding:
            if method == "c14n":
                encoding = "utf-8"
            else:
                encoding = "us-ascii"
        elif xml_declaration or (
            xml_declaration is None and encoding not in ("utf-8", "us-ascii")
        ):
            if method == "xml":
                write("<?xml version='1.0' encoding='%s'?>\n" % encoding)
        qnames, namespaces = _namespaces(self.root, encoding, default_namespace)
        _serialize_xml(write, self.root, encoding, qnames, namespaces)
        if file_or_filename is not file:
            file.close()


def SubElement(parent: Element, tag, attrib={}, **extra):
    attrib = attrib.copy()
    attrib.update(extra)
    element = parent.makeelement(tag, attrib)
    parent.append(element)
    return element


# --------------------------------------------------------------------
# serialization support


def tostring(element, encoding=None, method=None):
    class dummy:
        pass

    data = []
    file = dummy()
    file.write = data.append
    ElementTree(element).write(file, encoding, method=method)
    return b"".join(data)


_namespace_map = {
    # "well-known" namespace prefixes
    "http://www.w3.org/XML/1998/namespace": "xml",
    "http://www.w3.org/1999/xhtml": "html",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://schemas.xmlsoap.org/wsdl/": "wsdl",
    # xml schema
    "http://www.w3.org/2001/XMLSchema": "xs",
    "http://www.w3.org/2001/XMLSchema-instance": "xsi",
    # dublin core
    "http://purl.org/dc/elements/1.1/": "dc",
}


def _namespaces(elem, encoding, default_namespace=None):
    # identify namespaces used in this tree

    # maps qnames to *encoded* prefix:local names
    qnames = {None: None}

    # maps uri:s to prefixes
    namespaces = {}
    if default_namespace:
        namespaces[default_namespace] = ""

    def encode(text):
        return text.encode(encoding)

    def add_qname(qname):
        # calculate serialized qname representation
        try:
            if qname[:1] == "{":
                uri, tag = qname[1:].rsplit("}", 1)
                prefix = namespaces.get(uri)
                if prefix is None:
                    prefix = _namespace_map.get(uri)
                    if prefix is None:
                        prefix = "ns%d" % len(namespaces)
                    if prefix != "xml":
                        namespaces[uri] = prefix
                if prefix:
                    qnames[qname] = encode("%s:%s" % (prefix, tag))
                else:
                    qnames[qname] = encode(tag)  # default element
            else:
                if default_namespace:
                    # FIXME: can this be handled in XML 1.0?
                    raise ValueError(
                        "cannot use non-qualified names with " "default_namespace option"
                    )
                qnames[qname] = encode(qname)
        except TypeError:
            _raise_serialization_error(qname)

    # populate qname and namespaces table
    # try:
    iterate = elem.iter
    # except AttributeError:
    #     iterate = elem.getiterator # cET compatibility
    for elem in iterate():
        tag = elem.tag
        # if isinstance(tag, QName):
        #     if tag.text not in qnames:
        #         add_qname(tag.text)
        if isinstance(tag, str):
            if tag not in qnames:
                add_qname(tag)
        # elif tag is not None and tag is not Comment and tag is not PI:
        #     _raise_serialization_error(tag)
        for key, value in elem.items():
            # if isinstance(key, QName):
            #     key = key.text
            if key not in qnames:
                add_qname(key)
            # if isinstance(value, QName) and value.text not in qnames:
            #     add_qname(value.text)
        text = elem.text
        # if isinstance(text, QName) and text.text not in qnames:
        #     add_qname(text.text)
    return qnames, namespaces


def _serialize_xml(write, elem: Element, encoding, qnames, namespaces):
    tag = elem.tag
    text = elem.text
    tag = qnames[tag]
    if tag is None:
        if text:
            write(_escape_cdata(text, encoding))
        for e in elem:
            _serialize_xml(write, e, encoding, qnames, None)
    else:
        write(b"<" + tag)
        items = elem.items()
        if items:  # or namespaces:
            # if namespaces:
            #     for v, k in sorted(namespaces.items(),
            #                        key=lambda x: x[1]):  # sort on prefix
            #         if k:
            #             k = b":" + k
            #         write(b" xmlns%s=\"%s\"" % (
            #             k.encode(encoding),
            #             _escape_attrib(v, encoding)
            #             ))
            for k, v in sorted(items):  # lexical order
                v = _escape_attrib(v, encoding)
                write(b' %s="%s"' % (qnames[k], v))
        if text or len(elem):
            write(b">")
            if text:
                write(_escape_cdata(text, encoding))
            for e in elem:
                _serialize_xml(write, e, encoding, qnames, None)
            write(b"</" + tag + b">")
        else:
            write(b" />")


_serialize = {
    "xml": _serialize_xml,
}


def _escape_cdata(text, encoding):
    # escape character data
    try:
        # it's worth avoiding do-nothing calls for strings that are
        # shorter than 500 character, or so.  assume that's, by far,
        # the most common case in most applications.
        if "&" in text:
            text = text.replace("&", "&amp;")
        if "<" in text:
            text = text.replace("<", "&lt;")
        if ">" in text:
            text = text.replace(">", "&gt;")
        return text.encode(encoding, "xmlcharrefreplace")
    except (TypeError, AttributeError):
        _raise_serialization_error(text)


def _escape_attrib(text, encoding):
    # escape attribute value
    try:
        if "&" in text:
            text = text.replace("&", "&amp;")
        if "<" in text:
            text = text.replace("<", "&lt;")
        if ">" in text:
            text = text.replace(">", "&gt;")
        if '"' in text:
            text = text.replace('"', "&quot;")
        if "\n" in text:
            text = text.replace("\n", "&#10;")
        return text.encode(encoding, "xmlcharrefreplace")
    except (TypeError, AttributeError):
        _raise_serialization_error(text)


def _raise_serialization_error(text):
    raise TypeError("cannot serialize %r (type %s)" % (text, type(text).__name__))
