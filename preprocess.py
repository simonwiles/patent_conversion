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
       * but it references ST32-US-Grant-025xml.ent which is nowhere to be found in that repo...

       * DTDs from here: https://bulkdata.uspto.gov/data/patent/grant/redbook/2002/GrantV2-5DTD.zip
         (linked from https://www.uspto.gov/learning-and-resources/xml-resources)
         are also missing resources...

    * after hacking a bit, got some stuff working:
      * 06564405.xml has &thgr; -- should be &theta;
      * 06566367.xml has &mgr; -- should be &mu;
      * 06566367.xml has &Dgr; -- should be &Delta;
      * 06566372.xml has &agr; → &alpha;
      * 06566372.xml has &bgr; → &beta;

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

"""

import argparse
import csv
import json
import logging
import os
import re
from collections import defaultdict
from io import BytesIO

from lxml import etree

INPUT_FILE = "../SamplePatentFiles/pg030520.xml"
CONFIG = json.load(open("config.json"))


TABLES = defaultdict(list)


class DTDResolver(etree.Resolver):
    def resolve(self, system_url, _public_id, context):
        return self.resolve_filename(os.path.join("dtds", system_url), context,)


def get_all_xml_docs(filepath):
    with open(filepath, "r") as _fh:
        data = _fh.read()
    return re.split(r"\n(?=<\?xml)", data)


def yield_xml_doc(filepath):
    xml_doc = []
    with open(filepath, "r") as _fh:
        for line in _fh:
            if line.startswith("<?xml"):
                if xml_doc:
                    yield "".join(xml_doc)
                xml_doc = []
            xml_doc.append(line)


def get_text(elem):
    return re.sub(
        r"\s+", " ", etree.tostring(elem, method="text", encoding="unicode")
    ).strip()


def get_pk(tree, config):
    if config.get("pk", None):
        elems = tree.findall("./" + config["pk"])
        assert len(elems) == 1
        return get_text(elems[0])
    return None


def process_path(tree, path, config, record, parent_entity=None, parent_pk=None):

    try:
        elems = [tree.getroot()]
    except:
        elems = tree.findall("./" + path)

    if isinstance(config, str):
        if elems:

            if config.startswith("|"):
                record[config[1:]] = "|".join([get_text(elem) for elem in elems])
                return

            try:
                assert len(elems) == 1
            except AssertionError:
                logging.fatal("Multiple elements found for %s", path)
                logging.fatal([get_text(el) for el in elems])
                raise

            # handle enum types
            if ":" in config:
                record[config.split(":")[0]] = config.split(":")[1]
                return
            record[config] = get_text(elems[0])
        return

    entity = config["entity"]
    for idx, elem in enumerate(elems):
        srecord = {}

        pk = get_pk(tree, config)
        if pk:
            srecord["id"] = pk
        elif parent_pk:
            srecord["id"] = f"{parent_pk}_{idx}"
        else:
            srecord["id"] = idx

        if parent_pk:
            srecord[f"{parent_entity}_id"] = parent_pk
        for subpath, subconfig in config["fields"].items():
            process_path(elem, subpath, subconfig, srecord, entity, pk)

        TABLES[entity].append(srecord)


def process_doc(doc):

    #
    doc = doc.replace("&mgr;", "&mu;")
    doc = doc.replace("&thgr;", "&theta;")
    doc = doc.replace("&Dgr;", "&Delta;")
    doc = doc.replace("&agr;", "&alpha;")
    doc = doc.replace("&bgr;", "&beta;")

    # parser = etree.XMLParser(load_dtd=True, ns_clean=True, dtd_validation=True)
    # parser = etree.XMLParser(resolve_entities=False)
    parser = etree.XMLParser(load_dtd=True, resolve_entities=False)
    parser.resolvers.add(DTDResolver())
    tree = etree.parse(BytesIO(doc.encode("utf8")), parser)

    for path, config in CONFIG.items():
        process_path(tree, path, config, {})


def get_fieldnames():
    """ On python >=3.7, dictionaries maintain key order, so fields are guaranteed to be
        returned in the order in which they appear in the config file.  To guarantee this
        on versions of python <3.7, collections.OrderedDict should be used here.
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
        fieldnames[entity] = list(dict.fromkeys(_fieldnames).keys())

    for config in CONFIG.values():
        add_fieldnames(config, [])

    return fieldnames


def main():
    """ Command-line entry-point. """
    arg_parser = argparse.ArgumentParser(description="Description: {}".format(__file__))

    arg_parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="Increase verbosity"
    )
    arg_parser.add_argument(
        "-q", "--quiet", action="store_true", default=False, help="quiet operation"
    )

    args = arg_parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_level = logging.CRITICAL if args.quiet else log_level
    logging.basicConfig(level=log_level, format="%(message)s")

    logging.info("Processing %s...", INPUT_FILE)
    for i, doc in enumerate(yield_xml_doc(INPUT_FILE)):
        if i % 100 == 0:
            logging.debug("Processing document %d...", i + 1)
        try:
            process_doc(doc)
        except (AssertionError, etree.XMLSyntaxError):
            logging.debug(doc)
            raise
    logging.info("...%d records processed!", i + 1)

    # import pprint
    # pprint.pprint(TABLES)

    fieldnames = get_fieldnames()
    # import pprint
    # pprint.pprint(fieldnames)

    logging.info("Writing csv files...")
    for tablename, rows in TABLES.items():
        with open(f"../output/{tablename}.csv", "w") as _fh:
            writer = csv.DictWriter(_fh, fieldnames=fieldnames[tablename])
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
