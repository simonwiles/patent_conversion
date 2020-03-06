#!/usr/bin/env python3

""" preprocess.py

    # DOCUMENTATION
    * for config values of the form "field:value" (i.e. with a colon), the text is not
       retrieved; the presence of the element causes "field" to be set to "value".
       (This is to handle self-closing tags.)
    * for config values that begin with a pipe "|", values for multiple occurences are
       concatenated together with the pipe as a separator.

    # QUESTIONS / ISSUES

    ## DTDs and entity resolution.
    * The DTD is here:
       https://github.com/USPTO/PatentPublicData/blob/master/PatentDocument/src/main/resources/dtd/ST32-US-Grant-025xml.dtd
       but it references ST32-US-Grant-025xml.ent which is nowhere to be found in that repo...

    * DTDs from here: https://bulkdata.uspto.gov/data/patent/grant/redbook/2002/GrantV2-5DTD.zip
       (linked from https://www.uspto.gov/learning-and-resources/xml-resources)
       are also missing resources...

    * new DTDs provided by Gideon solve some of these problems -- however, the following are still
      missing:
      - IndentingNewLine
      - LeftBracketingBar
      - LeftDoubleBracketingBar
      - RightBracketingBar
      They seem to be MathML symbols, but the mappings that I've used (deriving from
        https://reference.wolfram.com/language/ref/character/LeftBracketingBar.html and
        http://www.mathmlcentral.com/characters/glyphs/LeftBracketingBar.html) point to code points
        in the PUA of the Unicode BMP -- i.e., they're only going to work with specific fonts.
      The symbols seem like they should be part of mmlextra (see
       https://www.w3.org/TR/REC-MathML/chap6/byalpha.html), but they're not in any of the versions
       (plural!) of this file that I have available, or can find documented online (see, e.g.,
       https://www.w3.org/TR/MathML2/mmlextra.html,
       https://www.w3.org/2003/entities/mathmldoc/mmlextra.html etc.)

      See `replace_missing_mathml_ents()` below...


    ## CONFIG / DESIRED OUTPUT ISSUES

    * config file has some paths which map to the same key?:
      "SDOBI/B500/B520/B522": "USPCSecondary",
      "SDOBI/B500/B520/B522US": "USPCSecondary",

    * some docs have multiple B300s -- should these be pulled out as separate entities?
      - see D0474629.xml

    * structured `claimsText` info. (SDOCL/CL/CLM) -- okay to slam these all together?
      - see RE038119.xml

    * relatedDocs -- relatedDoc2 (B630) occurs multiple time in some docs, but has multiple
       related fields, so I've pulled it out as a separate entity.  What about relatedDoc1
       (B620)?  Also occurs multiple times in some docs, but why is only one value needed?
       - see 06564550.xml

    * unextracted data (e.g. SDODE, etc.) -- really not needed?

    * should we be looking to normalize esp., e.g., applicants?

"""

import argparse
import csv
import json
import logging
import re
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from lxml import etree

try:
    from termcolor import colored
except ImportError:
    logging.debug("termcolor (pip install termcolor) not available")

    def colored(text, _color):
        """ Dummy function in case termcolor is not available. """
        return text


def replace_missing_mathml_ents(doc):
    """ Substitute out some undefined entities that appear in the XML -- see notes
        for further details. """
    doc = doc.replace("&IndentingNewLine;", "&#xF3A3;")
    doc = doc.replace("&LeftBracketingBar;", "&#xF603;")
    doc = doc.replace("&RightBracketingBar;", "&#xF604;")
    doc = doc.replace("&LeftDoubleBracketingBar;", "&#xF605;")
    doc = doc.replace("&RightDoubleBracketingBar;", "&#xF606;")
    return doc


class DTDResolver(etree.Resolver):
    def __init__(self, dtd_path):
        self.dtd_path = Path(dtd_path)

    def resolve(self, system_url, _public_id, context):
        if system_url.startswith(str(self.dtd_path)):
            return self.resolve_filename(system_url, context)
        else:
            return self.resolve_filename(
                str((self.dtd_path / system_url).resolve()), context,
            )


class DocdbToTabular:
    def __init__(
        self, xml_input, config, dtd_path, recurse, output_path, no_validate, **_kwargs
    ):
        self.xml_files = Path(xml_input)
        if self.xml_files.is_file():
            self.xml_files = [self.xml_files]
        elif self.xml_files.is_dir():
            self.xml_files = self.xml_files.glob(f'{"**/" if recurse else ""}*.xml')
        else:
            logging.fatal("specified input is invalid")
            exit(1)

        # do this now, because we don't want to process all that data and then find
        #  the output_path is invalid... :)
        self.output_path = Path(output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)

        self.config = json.load(open(config))

        self.tables = defaultdict(list)

        if no_validate:
            self.parser = etree.XMLParser(
                load_dtd=True, resolve_entities=True, ns_clean=True
            )
        else:
            self.parser = etree.XMLParser(
                load_dtd=True, resolve_entities=True, ns_clean=True, dtd_validation=True
            )
        self.parser.resolvers.add(DTDResolver(dtd_path))

    @staticmethod
    def get_all_xml_docs(filepath):
        with open(filepath, "r") as _fh:
            data = _fh.read()
        return re.split(r"\n(?=<\?xml)", data)

    @staticmethod
    def yield_xml_doc(filepath):
        xml_doc = []
        with open(filepath, "r") as _fh:
            for line in _fh:
                if line.startswith("<?xml"):
                    if xml_doc:
                        yield "".join(xml_doc)
                    xml_doc = []
                xml_doc.append(line)

    @staticmethod
    def get_text(elem):
        return re.sub(
            r"\s+", " ", etree.tostring(elem, method="text", encoding="unicode")
        ).strip()

    def get_pk(self, tree, config):
        if config.get("pk", None):
            elems = tree.findall("./" + config["pk"])
            assert len(elems) == 1
            return self.get_text(elems[0])
        return None

    def process_path(
        self, tree, path, config, record, parent_entity=None, parent_pk=None
    ):

        try:
            elems = [tree.getroot()]
        except AttributeError:
            elems = tree.findall("./" + path)

        if isinstance(config, str):
            if elems:

                if config.startswith("|"):
                    record[config[1:]] = "|".join(
                        [self.get_text(elem) for elem in elems]
                    )
                    return

                try:
                    assert len(elems) == 1
                except AssertionError:
                    logging.fatal("Multiple elements found for %s", path)
                    logging.fatal([self.get_text(el) for el in elems])
                    raise

                # handle enum types
                if ":" in config:
                    record[config.split(":")[0]] = config.split(":")[1]
                    return
                record[config] = self.get_text(elems[0])
            return

        entity = config["entity"]
        for elem in elems:
            srecord = {}

            pk = self.get_pk(tree, config)
            if pk:
                srecord["id"] = pk
            else:
                srecord["id"] = f"{len(self.tables[entity])}"

            if parent_pk:
                srecord[f"{parent_entity}_id"] = parent_pk
            for subpath, subconfig in config["fields"].items():
                self.process_path(elem, subpath, subconfig, srecord, entity, pk)

            self.tables[entity].append(srecord)

    def process_doc(self, doc):

        doc = replace_missing_mathml_ents(doc)

        tree = etree.parse(BytesIO(doc.encode("utf8")), self.parser)

        for path, config in self.config.items():
            self.process_path(tree, path, config, {})

    def convert(self):
        for input_file in self.xml_files:

            logging.info(colored("Processing %s...", "green"), input_file.resolve())

            for i, doc in enumerate(self.yield_xml_doc(input_file)):
                if i % 100 == 0:
                    logging.debug(colored("Processing document %d...", "cyan"), i + 1)
                try:
                    self.process_doc(doc)
                except (AssertionError, etree.XMLSyntaxError) as exc:
                    logging.debug(doc)
                    p_id = re.search(
                        r"<B210><DNUM><PDAT>(\d+)<\/PDAT><\/DNUM><\/B210>", doc
                    ).group(1)
                    logging.warning(
                        colored("ID %s: %s (record has not been parsed)", "red"),
                        p_id,
                        exc.msg,
                    )

            logging.info(colored("...%d records processed!", "green"), i + 1)

    def get_fieldnames(self):
        """ On python >=3.7, dictionaries maintain key order, so fields are guaranteed to be
            returned in the order in which they appear in the config file.  To guarantee this
            on versions of python <3.7 (insofar as it matters), collections.OrderedDict would
            have to be used here.
        """

        fieldnames = defaultdict(list)

        def add_fieldnames(config, _fieldnames, parent_entity=None):
            if isinstance(config, str):
                if ":" in config:
                    _fieldnames.append(config.split(":")[0])
                    return
                if config.startswith("|"):
                    _fieldnames.append(config[1:])
                    return
                _fieldnames.append(config)
                return

            entity = config["entity"]
            _fieldnames = []
            if config.get("pk") or parent_entity:
                _fieldnames.append("id")
            if parent_entity:
                _fieldnames.append(f"{parent_entity}_id")
            for subconfig in config["fields"].values():
                add_fieldnames(subconfig, _fieldnames, entity)
            # different keys may be appending rows to the same table(s), so we're appending
            #  to lists of fieldnames here.
            fieldnames[entity] = list(
                dict.fromkeys(fieldnames[entity] + _fieldnames).keys()
            )

        for config in self.config.values():
            add_fieldnames(config, [])

        return fieldnames

    def write_csv_files(self):

        fieldnames = self.get_fieldnames()

        logging.info(
            colored("Writing csv files to %s ...", "green"), self.output_path.resolve()
        )
        for tablename, rows in self.tables.items():
            output_file = self.output_path / f"{tablename}.csv"
            with output_file.open("w") as _fh:
                writer = csv.DictWriter(_fh, fieldnames=fieldnames[tablename])
                writer.writeheader()
                writer.writerows(rows)


def main():
    """ Command-line entry-point. """
    arg_parser = argparse.ArgumentParser(description="Description: {}".format(__file__))

    arg_parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="increase verbosity"
    )
    arg_parser.add_argument(
        "-q", "--quiet", action="store_true", default=False, help="quiet operation"
    )

    arg_parser.add_argument(
        "-i",
        "--xml-input",
        action="store",
        required=True,
        help='"XML" file or directory to parse recursively',
    )

    arg_parser.add_argument(
        "-r",
        "--recurse",
        action="store_true",
        help='if supplied, the parser will search subdirectories for "XML" files to parse',
    )

    arg_parser.add_argument(
        "-c",
        "--config",
        action="store",
        required=True,
        help="config file (in JSON format) describing the fields to extract from the XML",
    )

    arg_parser.add_argument(
        "-d",
        "--dtd-path",
        action="store",
        required=True,
        help="path to folder where dtds and related documents can be found",
    )

    arg_parser.add_argument(
        "-o",
        "--output-path",
        action="store",
        required=True,
        help="path to folder in which to save output (will be created if necessary)",
    )

    arg_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="skip validation of input XML (for speed)",
    )

    args = arg_parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_level = logging.CRITICAL if args.quiet else log_level
    logging.basicConfig(level=log_level, format="%(message)s")

    convertor = DocdbToTabular(**vars(args))
    convertor.convert()
    convertor.write_csv_files()


if __name__ == "__main__":
    main()
