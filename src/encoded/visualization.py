from pyramid.response import Response
from pyramid.view import view_config
from pyramid.compat import bytes_
from snovault import Item
from collections import OrderedDict
from copy import deepcopy
import json
import os
from urllib.parse import (
    parse_qs,
    urlencode,
    urlparse,
)
import subprocess
import requests
import shlex
import sys
from snovault.elasticsearch.interfaces import ELASTIC_SEARCH
import time
from pkg_resources import resource_filename
#from types.file import download

import logging
import boto
import pprint
log = logging.getLogger(__name__)
#log.setLevel(logging.DEBUG)
log.setLevel(logging.INFO)

# NOTE: Caching is turned on and off with this global AND TRACKHUB_CACHING in peak_indexer.py
USE_CACHE = False  # Use elasticsearch caching of individual acc_composite blobs

# GRCh38 datahub url for epigenome browser 02-08-2019
_ASSEMBLY_MAPPER = {
    'GRCh38-minimal': 'hg38',
    'GRCh38': 'GRCh38',
    'GRCh37': 'hg19',
    "hg18": 'hg18',
    'mm10-minimal': 'mm10',
    'GRCm38': 'mm10',
    'NCBI37': 'mm9',
    'BDGP6': 'dm6',
    'BDGP5': 'dm3',
    'WBcel235': 'ce11'
}

_ASSEMBLY_MAPPER_FULL = {
    'GRCh38':         { 'species':          'Homo sapiens',     'assembly_reference': 'GRCh38',
                        'common_name':      'human',
                        'ucsc_assembly':    'hg38',
                        'ensembl_host':     'www.ensembl.org',
                        'quickview':        True,
                        'comment':          'Ensembl works'
    },
    'hg18':         {   'species':          'Homo sapiens',     'assembly_reference': 'GRCh18',
                        'common_name':      'human',
                        'ucsc_assembly':    'hg18',
                        'ensembl_host':     'www.ensembl.org',
                        'quickview':        True,
                        'comment':          'Ensembl works'
    },
    'GRCh38-minimal': { 'species':          'Homo sapiens',     'assembly_reference': 'GRCh38',
                        'common_name':      'human',
                        'ucsc_assembly':    'hg38',
                        'quickview':        True,
                        'ensembl_host':     'www.ensembl.org',
    },
    'hg19': {           'species':          'Homo sapiens',     'assembly_reference': 'GRCh37',
                        'common_name':      'human',
                        'ucsc_assembly':    'hg19',
                        'NA_ensembl_host':  'grch37.ensembl.org',
                        'quickview':        True,
                        'comment':          'Ensembl DOES NOT WORK'
    },
    'mm10': {           'species':          'Mus musculus',     'assembly_reference': 'GRCm38',
                        'common_name':      'mouse',
                        'ucsc_assembly':    'mm10',
                        'ensembl_host':     'www.ensembl.org',
                        'comment':          'Ensembl works'
    },
    'mm10-minimal': {   'species':          'Mus musculus',     'assembly_reference': 'GRCm38',
                        'common_name':      'mouse',
                        'ucsc_assembly':    'mm10',
                        'ensembl_host':     'www.ensembl.org',
                        'quickview':        True,
                        'comment':          'Should this be removed?'
    },
    'mm9': {            'species':          'Mus musculus',     'assembly_reference': 'NCBI37',
                        'common_name':      'mouse',
                        'ucsc_assembly':    'mm9',
                        'NA_ensembl_host':  'may2012.archive.ensembl.org',
                        'quickview':        True,
                        'comment':          'Ensembl DOES NOT WORK'
    },
    'dm6': {    'species':          'Drosophila melanogaster',  'assembly_reference': 'BDGP6',
                'common_name':      'fruit fly',
                'ucsc_assembly':    'dm6',
                'NA_ensembl_host':  'www.ensembl.org',
                'quickview':        True,
                'comment':          'Ensembl DOES NOT WORK'
    },
    'dm3': {    'species':          'Drosophila melanogaster',  'assembly_reference': 'BDGP5',
                'common_name':      'fruit fly',
                'ucsc_assembly':    'dm3',
                'NA_ensembl_host':  'dec2014.archive.ensembl.org',
                'quickview':        True,
                'comment':          'Ensembl DOES NOT WORK'
    },
    'ce11': {   'species':          'Caenorhabditis elegans',   'assembly_reference': 'WBcel235',
                'common_name':      'worm',
                'ucsc_assembly':    'ce11',
                'NA_ensembl_host':  'www.ensembl.org',
                'quickview':        True,
                'comment':          'Ensembl DOES NOT WORK'
    },
    'ce10': {   'species':          'Caenorhabditis elegans',   'assembly_reference': 'WS220',
                'common_name':      'worm',
                'ucsc_assembly':    'ce10',
                'quickview':        True,
                'comment':          'Never Ensembl'
    },
    'ce6': {    'species':          'Caenorhabditis elegans',   'assembly_reference': 'WS190',
                'common_name':      'worm',
                'ucsc_assembly':    'ce6',
                'comment':          'Never Ensembl, not found in encoded'
    },
    'J02459.1': {   'species':      'Escherichia virus Lambda', 'assembly_reference': 'J02459.1',
                    'common_name':  'lambda phage',
                    'comment':      'Never visualized'
    },
}


def includeme(config):
    config.add_route('batch_hub', '/batch_hub/{search_params}/{txt}')
    config.add_route('batch_hub:trackdb', '/batch_hub/{search_params}/{assembly}/{txt}')
    config.add_route('browser_hub', '/browser_hub/{search_params}/{txt}')
    config.add_route('browser_hub:trackdb', '/browser_hub/{search_params}/{assembly}/{txt}')
    config.add_route('index-vis', '/index-vis')
    config.scan(__name__)

PROFILE_START_TIME = 0  # For profiling within this module

TAB = '\t'
NEWLINE = '\n'
HUB_TXT = 'hub.txt'

TRACKDB_TXT = 'trackDb.txt'
BIGWIG_FILE_TYPES = ['bigWig']
BIGBED_FILE_TYPES = ['bigBed']
HIC_FILE_TYPES = ['hic']
VISIBLE_DATASET_STATUSES = ["released", "proposed"]
QUICKVIEW_STATUSES_BLOCKED = ["proposed", "started", "deleted", "revoked", "replaced"]
VISIBLE_FILE_STATUSES = ["released","uploading","restricted"]
VISIBLE_DATASET_TYPES = ["Experiment", "Annotation"]
VISIBLE_DATASET_TYPES_LC = ["experiment", "annotation"]
VISIBLE_ASSEMBLIES = ['hg19', 'GRCh38', 'mm10', 'mm10-minimal' ,'mm9','dm6','dm3','ce10','ce11']
# ASSEMBLY_MAPPINGS is needed to ensure that mm10 and mm10-minimal will
#                   get combined into the same trackHub.txt
# This is necessary because mm10 and mm10-minimal are only mm10 at UCSC,
# so the 2 must be collapsed into one.
ASSEMBLY_MAPPINGS = {
    # any term:       [ set of encoded terms used ]
    "GRCh38":           ["GRCh38", "GRCh38-minimal"],
    "GRCh38-minimal":   ["GRCh38", "GRCh38-minimal"],
    "hg38":             ["GRCh38", "GRCh38-minimal"],
    "GRCh37":           ["hg19", "GRCh37"],  # Is GRCh37 ever in encoded?
    "hg19":             ["hg19", "GRCh37"],
    "hg18":             ["hg18"],
    "GRCm38":           ["mm10", "mm10-minimal", "GRCm38"],  # Is GRCm38 ever in encoded?
    "mm10":             ["mm10", "mm10-minimal", "GRCm38"],
    "mm10-minimal":     ["mm10", "mm10-minimal", "GRCm38"],
    "GRCm37":           ["mm9", "GRCm37"],  # Is GRCm37 ever in encoded?
    "mm9":              ["mm9", "GRCm37"],
    "BDGP6":            ["dm4", "BDGP6"],
    "dm4":              ["dm4", "BDGP6"],
    "BDGP5":            ["dm3", "BDGP5"],
    "dm3":              ["dm3", "BDGP5"],
    # "WBcel235":         ["WBcel235"], # defaults to term: [ term ]
    }


# Supported tokens are the only tokens the code currently knows how to look up.
SUPPORTED_MASK_TOKENS = [
    "{replicate}",         # replicate that that will be displayed: ("rep1", "combined")
    "{rep_tech}",          # The rep_tech if desired ("rep1_1", "combined")
    "{replicate_number}",  # The replicate number displayed for visualized track: ("1", "0")
    "{biological_replicate_number}",
    "{technical_replicate_number}",
    "{assay_title}",
    "{assay_term_name}",                      # dataset.assay_term_name
    "{annotation_type}",                      # some datasets have annotation type and not assay
    "{output_type}",                          # files.output_type
    "{accession}", "{experiment.accession}",  # "{accession}" is assumed to be experiment.accession
    "{file.accession}",
    "{@id}", "{@type}",                       # dataset only
    "{target}", "{target.label}",             # Either is acceptible
    "{target.title}",
    "{target.name}",                          # Used in metadata URLs
    "{target.investigated_as}",
    "{biosample_term_name}", "{biosample_term_name|multiple}",  # "|multiple": none means multiple
    "{output_type_short_label}",                # hard-coded translation from output_type to very
                                                # short version
    "{replicates.library.biosample.summary}",   # Idan, Forrest and Cricket are conspiring to move
                                                # to dataset.biosample_summary & make it shorter
    "{replicates.library.biosample.summary|multiple}",   # "|multiple": none means multiple
    "{assembly}",                               # you don't need this in titles, but it is crucial
                                                # variable and seems to not be being applied
                                                # # correctly in the html generation
    "{lab.title}",                              # In metadata
    "{award.rfa}",
    # TODO "{software? or pipeline?}",  # Cricket: "I am stumbling over the fact that we
    #                                   #    can't distinguish tophat and star produced files"
    # TODO "{phase}",                   # Cricket: "If we get to the point of being fancy
    #                                   #    in the replication timing, then we need this,
    #                                   #    otherwise it bundles up in the biosample summary now"
    ]

# Simple tokens are a straight lookup, no questions asked
SIMPLE_DATASET_TOKENS = ["{biosample_term_name}", "{accession}", "{assay_title}",
                         "{assay_term_name}", "{annotation_type}", "{@id}", "{@type}"]

# static group defs are keyed by group title (or special token) and consist of
# tag: (optional) unique terse key for referencing group
# groups: (optional) { subgroups keyed by subgroup title }
# group_order: (optional) [ ordered list of subgroup titles ]
# other definitions

# live group defs are keyed by tag and are the transformed in memory version of static defs
# title: (required) same as the static group's key
# groups: (if appropriate) { subgroups keyed by subgroup tag }
# group_order: (if appropriate) [ ordered list of subgroup tags ]

VIS_DEFS_FOLDER = "static/vis_defs/"
VIS_DEFS_BY_TYPE = {}
COMPOSITE_VIS_DEFS_DEFAULT = {}


def lookup_token(token, dataset, a_file=None):
    '''Encodes the string to swap special characters and remove spaces.'''

    if token not in SUPPORTED_MASK_TOKENS:
        log.warn("Attempting to look up unexpected token: '%s'" % token)
        return "unknown token"

    if token in SIMPLE_DATASET_TOKENS:
        term = dataset.get(token[1:-1])
        if term is None:
            term = token[1:-1].split('_')[0].capitalize()
        return term
    # test
    elif token == "{experiment.accession}":
        return dataset['accession']
    elif token in ["{target}", "{target.label}", "{target.name}", "{target.title}"]:
        target = dataset.get('target', {})
        if isinstance(target, list):
            if len(target) > 0:
                target = target[0]
            else:
                target = {}
        if token.find('.') > -1:
            sub_token = token.strip('{}').split('.')[1]
        else:
            sub_token = "label"
        return target.get(sub_token, "Unknown Target")
    elif token in ["{target.name}", "{target.investigated_as}"]:
        target = dataset.get('target', {})
        if isinstance(target, list):
            if len(target) > 0:
                target = target[0]
            else:
                target = {}
        if token == "{target.name}":
            return target.get('label', "Unknown Target")
        elif token == "{target.investigated_as}":
            investigated_as = target.get('investigated_as', "Unknown Target")
            if not isinstance(investigated_as, list):
                return investigated_as
            elif len(investigated_as) > 0:
                return investigated_as[0]
            else:
                return "Unknown Target"
    elif token in ["{replicates.library.biosample.summary}",
                   "{replicates.library.biosample.summary|multiple}"]:
        term = None
        replicates = dataset.get("replicates", [])
        if replicates:
            term = replicates[0].get("library", {}).get("biosample", {}).get("summary")
        if term is None:
            term = dataset.get("{biosample_term_name}")
        if term is None:
            if token.endswith("|multiple}"):
                term = "multiple biosamples"
            else:
                term = "Unknown Biosample"
        return term
    elif token == "{lab.title}":
        return dataset['lab'].get('title', 'unknown')
    elif token == "{award.rna}":
        return dataset.get['award'].get('rfa','unknown')
    elif token == "{biosample_term_name|multiple}":
        return dataset.get("biosample_term_name", "multiple biosamples")
    # TODO: rna_species
    # elif token == "{rna_species}":
    #     if replicates.library.nucleic_acid = polyadenylated mRNA
    #        rna_species = polyA RNA
    #     elseif replicates.library.nucleic_acid = RNA
    #        if polyadenylated mRNA in replicates.library.depleted_in_term_name
    #                rna_species = polyA depleted RNA
    #        else
    #                rna_species = total RNA
    elif a_file is not None:
        if token == "{file.accession}":
            return a_file['accession']
        elif token == "{output_type}":
            return a_file['output_type']
        elif token == "{output_type_short_label}":
            output_type = a_file['output_type']
            return OUTPUT_TYPE_8CHARS.get(output_type, output_type)
        elif token == "{replicate}":
            rep_tag = a_file.get("rep_tag")
            if rep_tag is not None:
                while len(rep_tag) > 4:
                    if rep_tag[3] != '0':
                        break
                    rep_tag = rep_tag[0:3] + rep_tag[4:]
                return rep_tag
            rep_tech = a_file.get("rep_tech")
            if rep_tech is not None:
                return rep_tech.split('_')[0]  # Should truncate tech_rep
            rep_tech = rep_for_file(a_file)
            return rep_tech.split('_')[0]  # Should truncate tech_rep
        elif token == "{replicate_number}":
            rep_tag = a_file.get("rep_tag", a_file.get("rep_tech", rep_for_file(a_file)))
            if not rep_tag.startswith("rep"):
                return "0"
            return rep_tag[3:].split('_')[0]
        elif token == "{biological_replicate_number}":
            rep_tech = a_file.get("rep_tech", rep_for_file(a_file))
            if not rep_tech.startswith("rep"):
                return "0"
            return rep_tech[3:].split('_')[0]
        elif token == "{technical_replicate_number}":
            rep_tech = a_file.get("rep_tech", rep_for_file(a_file))
            if not rep_tech.startswith("rep"):
                return "0"
            return rep_tech.split('_')[1]
        elif token == "{rep_tech}":
            return a_file.get("rep_tech", rep_for_file(a_file))
        else:
            return ""
    else:
        log.debug('Untranslated token: "%s"' % token)
        return "unknown"


def convert_mask(mask, dataset, a_file=None):
    '''Given a mask with one or more known {term_name}s, replaces with values.'''
    working_on = mask
    chars = len(working_on)
    while chars > 0:
        beg_ix = working_on.find('{')
        if beg_ix == -1:
            break
        end_ix = working_on.find('}')
        if end_ix == -1:
            break
        term = lookup_token(working_on[beg_ix:end_ix+1], dataset, a_file=a_file)
        new_mask = []
        if beg_ix > 0:
            new_mask = working_on[0:beg_ix]
        new_mask += "%s%s" % (term, working_on[end_ix+1:])
        chars = len(working_on[end_ix+1:])
        working_on = ''.join(new_mask)

    return working_on


def load_vis_defs():
    '''Loads 'vis_defs' (visualization definitions by assay type) from a static file.'''
    global VIS_DEFS_FOLDER
    global VIS_DEFS_BY_TYPE
    global COMPOSITE_VIS_DEFS_DEFAULT
    folder = resource_filename(__name__, VIS_DEFS_FOLDER)
    files = os.listdir(folder)
    for filename in files:
        if filename.endswith('.json'):
            with open(folder + filename) as fh:
                log.debug('Preparing to load %s' % (filename))
                vis_def = json.load(fh)
                if vis_def:
                    VIS_DEFS_BY_TYPE.update(vis_def)
    COMPOSITE_VIS_DEFS_DEFAULT = vis_def.get("opaque",{})


def get_vis_type(dataset):
    '''returns the best static composite definition set, based upon dataset.'''
    global VIS_DEFS_BY_TYPE
    if not VIS_DEFS_BY_TYPE:
        load_vis_defs()

    assay = dataset.get("assay_term_name", 'none')

    if isinstance(assay, list):
        if len(assay) == 1:
            assay = assay[0]
        else:
            log.debug("assay_term_name for %s is unexpectedly a list %s" %
                     (dataset['accession'], str(assay)))
            return "opaque"

    # simple rule defined in most vis_defs
    for vis_type in sorted(VIS_DEFS_BY_TYPE.keys(), reverse=True):  # Reverse pushes anno to bottom
        if "rule" in VIS_DEFS_BY_TYPE[vis_type]:
            rule = VIS_DEFS_BY_TYPE[vis_type]["rule"].replace('{assay_term_name}', assay)
            if rule.find('{') != -1:
                rule = convert_mask(rule, dataset)
            if eval(rule):
                return vis_type

    # Ugly rules:
    if assay in ["RNA-seq", "single cell isolation followed by RNA-seq"]:
        reps = dataset.get("replicates", [])  # NOTE: overly cautious
        if len(reps) < 1:
            log.debug("Could not distinguish between long and short RNA for %s because there are "
                     "no replicates.  Defaulting to short." % (dataset.get("accession")))
            return "SRNA"  # this will be more noticed if there is a mistake
        size_range = reps[0].get("library", {}).get("size_range", "")
        if size_range.startswith('>'):
            try:
                min_size = int(size_range[1:])
                max_size = min_size
            except:
                log.debug("Could not distinguish between long and short RNA for %s.  "
                         "Defaulting to short." % (dataset.get("accession")))
                return "SRNA"  # this will be more noticed if there is a mistake
        elif size_range.startswith('<'):
            try:
                max_size = int(size_range[1:]) - 1
                min_size = 0
            except:
                log.debug("Could not distinguish between long and short RNA for %s.  "
                         "Defaulting to short." % (dataset.get("accession")))
                return "SRNA"  # this will be more noticed if there is a mistake
        else:
            try:
                sizes = size_range.split('-')
                min_size = int(sizes[0])
                max_size = int(sizes[1])
            except:
                log.debug("Could not distinguish between long and short RNA for %s.  "
                         "Defaulting to short." % (dataset.get("accession")))
                return "SRNA"  # this will be more noticed if there is a mistake
        if max_size <= 200 and max_size != min_size:
            return "SRNA"
        elif min_size >= 150:
            return "LRNA"
        elif (min_size + max_size)/2 >= 235:
            # This is some wicked voodoo (SRNA:108-347=227; LRNA:155-315=235)
            return "LRNA"
        else:
            return "SRNA"

    log.debug("%s (assay:'%s') has undefined vis_type" % (dataset['accession'], assay))
    return "opaque"  # This becomes a dict key later so None is not okay

# TODO:
# ENCSR000BBI (assay:'comparative genomic hybridization by array') has undefined vis_type
# ENCSR000DBZ (assay:'FAIRE-seq') has undefined vis_type
# ENCSR901QEL (assay:'protein sequencing by tandem mass spectrometry assay') has undefined vis_type
# ENCSR000AWN (assay:'transcription profiling by array assay') has undefined vis_type
# ENCSR066KKK (assay:'Repli-chip') has undefined vis_type
# ENCSR935ULX (assay:'Repli-seq') has undefined vis_type
# ENCSR000AYD (assay:'RIP-chip') has undefined vis_type
# ENCSR000CWU (assay:'RIP-seq') has undefined vis_type
# ENCSR000BCM (assay:'RNA-PET') has undefined vis_type


EXP_GROUP = "Experiment"
DEFAULT_EXPERIMENT_GROUP = {"tag": "EXP", "groups": {"one": {"title_mask": "{accession}",
                            "url_mask": "experiments/{accession}"}}}


def lookup_vis_defs(vis_type):
    '''returns the best static composite definition set, based upon dataset.'''
    global VIS_DEFS_BY_TYPE
    global COMPOSITE_VIS_DEFS_DEFAULT
    if not VIS_DEFS_BY_TYPE:
        load_vis_defs()
    vis_def = VIS_DEFS_BY_TYPE.get(vis_type, COMPOSITE_VIS_DEFS_DEFAULT)
    if "other_groups" in vis_def and EXP_GROUP not in vis_def["other_groups"]["groups"]:
        vis_def["other_groups"]["groups"][EXP_GROUP] = DEFAULT_EXPERIMENT_GROUP
    if "sortOrder" in vis_def and EXP_GROUP not in vis_def["sortOrder"]:
        vis_def["sortOrder"].append(EXP_GROUP)
    return vis_def


SUPPORTED_SUBGROUPS = ["Biosample", "Targets", "Assay", "Replicates", EXP_GROUP]

SUPPORTED_TRACK_SETTINGS = [
    "type", "visibility", "longLabel", "shortLabel", "color", "altColor", "allButtonPair", "html",
    "scoreFilter", "spectrum", "minGrayLevel", "itemRgb", "viewLimits",
    "autoScale", "negateValues", "maxHeightPixels", "windowingFunction", "transformFunc"]
COMPOSITE_SETTINGS = ["longLabel", "shortLabel", "visibility", "pennantIcon", "allButtonPair",
                      "html"]
VIEW_SETTINGS = SUPPORTED_TRACK_SETTINGS
TRACK_SETTINGS = ["url", "longLabel", "shortLabel", "type", "color", "altColor"]


OUTPUT_TYPE_8CHARS = {
    # "idat green channel": "idat gr",     # raw data
    # "idat red channel": "idat rd",       # raw data
    # "reads":"reads",                     # raw data
    # "intensity values": "intnsty",       # raw data
    # "reporter code counts": "rcc",       # raw data
    # "alignments":"aln",                  # our plan is not to visualize alignments for now
    # "unfiltered alignments":"unflt aln", # our plan is not to visualize alignments for now
    # "transcriptome alignments":"tr aln", # our plan is not to visualize alignments for now
    "minus strand signal of all reads":     "all -",
    "plus strand signal of all reads":      "all +",
    "signal of all reads":                  "all sig",
    "normalized signal of all reads":       "normsig",
    # "raw minus strand signal":"raw -",   # these are all now minus signal of all reads
    # "raw plus strand signal":"raw +",    # these are all now plus signal of all reads
    "raw signal":                           "raw sig",
    "signal":                               "sig",
    "raw normalized signal":                "nraw",
    "read-depth normalized signal":         "rdnorm",
    "control normalized signal":            "ctlnorm",
    "minus strand signal of unique reads":  "unq -",
    "plus strand signal of unique reads":   "unq +",
    "signal of unique reads":               "unq sig",
    "signal p-value":                       "pval sig",
    "fold change over control":             "foldchg",
    "exon quantifications":                 "exon qt",
    "gene quantifications":                 "gene qt",
    "microRNA quantifications":             "miRNA qt",
    "transcript quantifications":           "trsct qt",
    "library fraction":                     "lib frac",
    "methylation state at CpG":             "mth CpG",
    "methylation state at CHG":             "mth CHG",
    "methylation state at CHH":             "mth CHH",
    "enrichment":                           "enrich",
    "replication timing profile":           "repli tm",
    "variant calls":                        "vars",
    "filtered SNPs":                        "f SNPs",
    "filtered indels":                      "f indel",
    "hotspots":                             "hotspt",
    "long range chromatin interactions":    "lrci",
    "chromatin interactions":               "ch int",
    "topologically associated domains":     "tads",
    "genome compartments":                  "compart",
    "open chromatin regions":               "open ch",
    "filtered peaks":                       "filt pk",
    "filtered regions":                     "filt reg",
    "DHS peaks":                            "DHS pk",
    "peaks":                                "peaks",
    "replicated peaks":                     "rep pk",
    "RNA-binding protein associated mRNAs": "RBP RNA",
    "splice junctions":                     "splice",
    "transcription start sites":            "tss",
    "predicted enhancers":                  "pr enh",
    "candidate enhancers":                  "can enh",
    "candidate promoters":                  "can pro",
    "predicted forebrain enhancers":        "fb enh",    # plan to fix these
    "predicted heart enhancers":            "hrt enh",       # plan to fix these
    "predicted whole brain enhancers":      "wb enh",  # plan to fix these
    "candidate regulatory elements":        "can re",
    # "genome reference":"ref",           # references not to be viewed
    # "transcriptome reference":"tr ref", # references not to be viewed
    # "transcriptome index":"tr rix",     # references not to be viewed
    # "tRNA reference":"tRNA",            # references not to be viewed
    # "miRNA reference":"miRNA",          # references not to be viewed
    # "snRNA reference":"snRNA",          # references not to be viewed
    # "rRNA reference":"rRNA",            # references not to be viewed
    # "TSS reference":"TSS",              # references not to be viewed
    # "reference variants":"var",         # references not to be viewed
    # "genome index":"ref ix",            # references not to be viewed
    # "female genome reference":"XX ref", # references not to be viewed
    # "female genome index":"XX rix",     # references not to be viewed
    # "male genome reference":"XY ref",   # references not to be viewed
    # "male genome index":"XY rix",       # references not to be viewed
    # "spike-in sequence":"spike",        # references not to be viewed
    "optimal idr thresholded peaks":        "oIDR pk",
    "conservative idr thresholded peaks":   "cIDR pk",
    "enhancer validation":                  "enh val",
    "semi-automated genome annotation":     "saga"
    }

def sanitize_char(c, exceptions=['_'], htmlize=False, numeralize=False):
    '''Pass through for 0-9,A-Z.a-z,_, but then either html encodes, numeralizes or removes special
       characters.'''
    n = ord(c)
    if n >= 47 and n <= 57:  # 0-9
        return c
    if n >= 65 and n <= 90:  # A-Z
        return c
    if n >= 97 and n <= 122:  # a-z
        return c
    if c in exceptions:
        return c
    if n == 32:              # space
        return '_'
    if htmlize:
        return "&#%d;" % n
    if numeralize:
        return "%d" % n

    return ""


def sanitize_label(s):
    '''Encodes the string to swap special characters and leaves spaces alone.'''
    new_s = ""      # longLabel and shorLabel can have spaces and some special characters
    for c in s:
        new_s += sanitize_char(c, [' ', '_', '.', '-', '(', ')', '+'], htmlize=False)
    return new_s


def sanitize_title(s):
    '''Encodes the string to swap special characters and replace spaces with '_'.'''
    new_s = ""      # Titles appear in tag=title pairs and cannot have spaces
    for c in s:
        new_s += sanitize_char(c, ['_', '.', '-', '(', ')', '+'], htmlize=True)
    return new_s


def sanitize_tag(s):
    '''Encodes the string to swap special characters and remove spaces.'''
    new_s = ""
    first = True
    for c in s:
        new_s += sanitize_char(c, numeralize=True)
        if first:
            if new_s.isdigit():  # tags cannot start with digit.
                new_s = 'z' + new_s
            first = False
    return new_s


def sanitize_name(s):
    '''Encodes the string to remove special characters swap spaces for underscores.'''
    new_s = ""
    for c in s:
        new_s += sanitize_char(c)
    return new_s


def add_to_es(request, comp_id, composite):
    '''Adds a composite json blob to elastic-search'''
    key = "vis_composite"
    es = request.registry.get(ELASTIC_SEARCH, None)
    if not es:
        return
    if not es.indices.exists(key):
        es.indices.create(index=key, body={'index': {'number_of_shards': 1}})
        mapping = {'default': {"_all":    {"enabled": False},
                               "_source": {"enabled": True},
                               # "_id":     {"index": "not_analyzed", "store": True},
                               # "_ttl":    {"enabled": True, "default": "1d"},
                               }}
        es.indices.put_mapping(index=key, doc_type='default', body=mapping)
        log.debug("created %s index" % key)
    es.index(index=key, doc_type='default', body=composite, id=comp_id)

def get_from_es(request, comp_id):
    '''Returns composite json blob from elastic-search, or None if not found.'''
    key = "vis_composite"
    es = request.registry.get(ELASTIC_SEARCH, None)
    if es and es.indices.exists(key):
        try:
            result = es.get(index=key, doc_type='default', id=comp_id)
            return result['_source']
        except:
            pass
    return None


def search_es(request, ids):
    '''Returns a list of composites from elastic-search, or None if not found.'''
    key = "vis_composite"
    es = request.registry.get(ELASTIC_SEARCH, None)
    if es and es.indices.exists(key):
        try:
            query = {"query": {"ids": {"values": ids}}}
            res = es.search(body=query, index=key, doc_type='default', size=99999)  # size=200?
            hits = res.get("hits", {}).get("hits", [])
            results = {}
            for hit in hits:
                results[hit["_id"]] = hit["_source"]  # make this a generator? No... len(results)
            log.debug("ids found: %d   %.3f secs" %
                      (len(results), (time.time() - PROFILE_START_TIME)))
            return results
        except:
            pass
    return {}


def rep_for_file(a_file):
    '''Determines best rep_tech or rep for a file.'''

    # Starting with a little cheat for rare cases where techreps are compared instead of bioreps
    if a_file.get("file_format_type", "none") in ["idr_peak"]:
        return "combined"
    if a_file['output_type'].endswith("idr thresholded peaks"):
        return "combined"

    bio_rep = 0
    tech_rep = 0
    if "replicate" in a_file:
        bio_rep = a_file["replicate"]["biological_replicate_number"]
        tech_rep = a_file["replicate"]["technical_replicate_number"]

    elif "tech_replicates" in a_file:
        # Do we want to make rep1_1.2.3 ?  Not doing it now
        tech_reps = a_file["tech_replicates"]
        if len(tech_reps) == 1:
            bio_rep = int(tech_reps[0].split('_')[0])
            tech_reps = tech_reps[0][2:]
            if len(tech_reps) == 1:
                tech_rep = int(tech_reps)
        elif len(tech_reps) > 1:
            bio = 0
            for tech in tech_reps:
                if bio == 0:
                    bio = int(tech.split('_')[0])
                elif bio != int(tech.split('_')[0]):
                    bio = 0
                    break
            if bio > 0:
                bio_rep = bio

    elif "biological_replicates" in a_file:
        bio_reps = a_file["biological_replicates"]
        if len(bio_reps) == 1:
            bio_rep = bio_reps[0]

    if bio_rep == 0:
        return "combined"

    rep = "rep%d" % bio_rep
    if tech_rep > 0:
        rep += "_%d" % tech_rep
    return rep


def handle_negateValues(live_settings, defs, dataset, composite):
    '''If negateValues is set then adjust some settings like color'''
    if live_settings.get("negateValues", "off") == "off":
        return
    # view limits need to change because numbers are all negative
    viewLimits = live_settings.get("viewLimits")
    if viewLimits is not None:
        low_high = viewLimits.split(':')
        if len(low_high) == 2:
            live_settings["viewLimits"] = "%d:%d" % (int(low_high[1]) * -1, int(low_high[0]) * -1)
    viewLimitsMax = live_settings.get("viewLimitsMax")
    if viewLimitsMax is not None:
        low_high = viewLimitsMax.split(':')
        if len(low_high) == 2:
            live_settings["viewLimitsMax"] = ("%d:%d" %
                                              (int(low_high[1]) * -1, int(low_high[0]) * -1))


def generate_live_groups(composite, title, group_defs, dataset, rep_tags=[]):
    '''Recursively populates live (in memory) groups from static group definitions'''
    live_group = {}
    tag = group_defs.get("tag", title)
    live_group["title"] = title
    live_group["tag"] = tag
    for key in group_defs.keys():
        if key not in ["groups", "group_order"]:  # leave no trace of subgroups keyed by title
            live_group[key] = deepcopy(group_defs[key])

    if title == "replicate":  # transform replicates into unique tags and titles
        if len(rep_tags) == 0:  # reps need special work after files are examined, so just stub.
            return (tag, live_group)
        # Inclusion of rep_tags occurs after files have been examined.
        live_group["groups"] = {}
        rep_title_mask = group_defs.get("title_mask", "Replicate_{replicate_number}")
        for rep_tag in rep_tags:
            rep_title = rep_title_mask
            if "combined_title" in group_defs and rep_tag in ["pool", "combined"]:
                rep_title = group_defs["combined_title"]
            elif rep_title_mask.find('{replicate}') != -1:
                rep_title = rep_title_mask.replace('{replicate}', rep_tag)
            elif rep_title_mask.find('{replicate_number}') != -1:
                if rep_tag in ["pool", "combined"]:
                    rep_title = rep_title_mask.replace('{replicate_number}', "0")
                else:
                    rep_no = int(rep_tag[3:])  # tag might be rep01 but we want replicate 1
                    rep_title = rep_title_mask.replace('{replicate_number}', str(rep_no))
            live_group["groups"][rep_tag] = {"title": rep_title, "tag": rep_tag}
        live_group["preferred_order"] = "sorted"

    elif title in ["Biosample", "Targets", "Assay", EXP_GROUP]:
        groups = group_defs.get("groups", {})
        assert(len(groups) == 1)
        for (group_key, group) in groups.items():
            mask = group.get("title_mask")
            if mask is not None:
                term = convert_mask(mask, dataset)
                if not term.startswith('Unknown '):
                    term_tag = sanitize_tag(term)
                    term_title = term
                    live_group["groups"] = {}
                    live_group["groups"][term_tag] = {"title": term_title, "tag": term_tag}
                    mask = group.get("url_mask")
                    if mask is not None:
                        term = convert_mask(mask, dataset)
                        live_group["groups"][term_tag]["url"] = term
        live_group["preferred_order"] = "sorted"
        # No tag order since only one
    # simple swapping tag and title and creating subgroups set with order
    else:  # "Views", "Replicates", etc:
        # if there are subgroups, they can be handled by recursion
        if "groups" in group_defs:
            live_group["groups"] = {}
            groups = group_defs["groups"]
            group_order = group_defs.get("group_order")
            preferred_order = []  # have to create preferred order based upon tags, not titles
            if group_order is None or not isinstance(group_order, list):
                group_order = sorted(groups.keys())
                preferred_order = "sorted"
            tag_order = []
            for subgroup_title in group_order:
                subgroup = groups.get(subgroup_title, {})
                (subgroup_tag, subgroup) = generate_live_groups(composite, subgroup_title, subgroup,
                                                                dataset)  # recursive
                subgroup["tag"] = subgroup_tag
                if isinstance(preferred_order, list):
                    preferred_order.append(subgroup_tag)
                if title == "Views":
                    assert(subgroup_title != subgroup_tag)
                    handle_negateValues(subgroup, subgroup, dataset, composite)
                live_group["groups"][subgroup_tag] = subgroup
                tag_order.append(subgroup_tag)
            # assert(len(live_group["groups"]) == len(groups))
            if len(live_group['groups']) != len(groups):
                log.debug("len(live_group['groups']):%d != len(groups):%d" %
                         (len(live_group['groups']), len(groups)))
                log.debug(json.dumps(live_group, indent=4))
            live_group["group_order"] = tag_order
            live_group["preferred_order"] = preferred_order
    return (tag, live_group)


def insert_live_group(live_groups, new_tag, new_group):
    '''Inserts new group into a set of live groups during composite remodelling.'''
    old_groups = live_groups.get("groups", {})
    preferred_order = live_groups.get("preferred_order")
    # Note: all cases where group is dynamically added should be in sort order!
    if preferred_order is None or not isinstance(preferred_order, list):
        old_groups[new_tag] = new_group
        live_groups["groups"] = old_groups
        # log.debug("Added %s to %s in sort order" % (new_tag,live_groups.get("tag","a group")))
        return live_groups

    # well we are going to have to generate s new order
    new_order = []
    old_order = live_groups.get("group_order", [])
    if old_order is None:
        old_order = sorted(old_groups.keys())
    for preferred_tag in preferred_order:
        if preferred_tag == new_tag:
            new_order.append(new_tag)
        elif preferred_tag in old_order:
            new_order.append(preferred_tag)

    old_groups[new_tag] = new_group
    live_groups["groups"] = old_groups
    # log.debug("Added %s to %s in preferred order" % (new_tag,live_groups.get("tag","a group")))
    return live_groups


def biosamples_for_file(a_file, dataset):
    '''Returns a dict of biosamples for file.'''
    biosamples = {}
    replicates = dataset.get("replicates")
    if replicates is None:
        return[]

    for bio_rep in a_file.get("biological_replicates", []):
        for replicate in replicates:
            if replicate.get("biological_replicate_number", -1) != bio_rep:
                continue
            biosample = replicate.get("library", {}).get("biosample", {})
            if not biosample:
                continue
            biosamples[biosample["accession"]] = biosample
            break  # If multiple techical replicates then the one should do

    return biosamples


def replicates_pair(a_file):
    if "replicate" in a_file:
        bio_rep = a_file["replicate"]["biological_replicate_number"]
        tech_rep = a_file["replicate"]["technical_replicate_number"]
        # metadata_pairs['replicate&#32;biological'] = str(bio_rep)
        # metadata_pairs['replicate&#32;technical'] = str(tech_rep)
        return ('replicate&#32;(bio_tech)', "%d_%d" % (bio_rep, tech_rep))

    bio_reps = a_file.get('biological_replicates')
    tech_reps = a_file.get('technical_replicates')
    if not bio_reps or len(bio_reps) == 0:
        return ("", "")
    rep_key = ""
    rep_val = ""
    for bio_rep in bio_reps:
        found = False
        br = "%s" % (bio_rep)
        if tech_reps:
            for tech_rep in tech_reps:
                if tech_rep.startswith(br + '_'):
                    found = True
                    rep_key = '&#32;(bio_tech)'
                    if len(rep_val) > 0:
                        rep_val += ', '
                    rep_val += tech_rep
                    break
        if not found:
            if len(rep_val) > 0:
                rep_val += ', '
            rep_val += br
    if ',' in rep_val:
        rep_key = 'replicates' + rep_key
    else:
        rep_key = 'replicate' + rep_key
    # TODO handle tech_reps only?
    return (rep_key, rep_val)


def acc_composite_extend_with_tracks(composite, vis_defs, dataset, assembly, host= None):
    '''Extends live experiment composite object with track definitions'''
    tracks = []
    rep_techs = {}
    files = []
    ucsc_assembly = composite['ucsc_assembly']
    # first time through just to get rep_tech
    group_order = composite["view"].get("group_order", [])
    for view_tag in group_order:
        view = composite["view"]["groups"][view_tag]
        output_types = view.get("output_type", [])
        file_format_types = view.get("file_format_type", [])
        file_format = view["type"].split()[0]
        #if file_format == "bigBed":
        #    view["type"] = "bigBed"  # scoreFilter implies score so 6 +
        #    format_type = view.get('file_format_type','')
        #log.warn("%d files looking for type %s" % ((len(dataset["files"]),view["type"]))
        for a_file in dataset["files"]:
            if a_file['status'] not in VISIBLE_FILE_STATUSES:
                continue
            if file_format != a_file['file_format']:
                continue
            if len(output_types) > 0 and a_file.get('output_type', 'unknown') not in output_types:
                continue
            if len(file_format_types) > 0 and a_file.get('file_format_type', 'unknown') not in file_format_types:
                continue
            if 'assembly' not in a_file or _ASSEMBLY_MAPPER.get(a_file['assembly'], a_file['assembly']) != ucsc_assembly:
                continue
            if "rep_tech" not in a_file:
                rep_tech = rep_for_file(a_file)
                a_file["rep_tech"] = rep_tech
            else:
                rep_tech = a_file["rep_tech"]
            rep_techs[rep_tech] = rep_tech
            files.append(a_file)
    if len(files) == 0:
        #log.warn("No visualizable files for %s" % (dataset["accession"]))
        return None

    # convert rep_techs to simple reps
    rep_ix = 1
    rep_tags = []
    for rep_tech in sorted(rep_techs.keys()):  # ordered by a simple sort
        if rep_tech == "combined":
            rep_tag = "pool"
        else:
            rep_tag = "rep%02d" % rep_ix
            rep_ix += 1
        rep_techs[rep_tech] = rep_tag
        rep_tags.append(rep_tag)

    # Now we can fill in "Replicate" subgroups with with "replicate"
    other_groups = vis_defs.get("other_groups", []).get("groups", [])
    if "Replicates" in other_groups:
        group = other_groups["Replicates"]
        group_tag = group["tag"]
        subgroups = group["groups"]
        if "replicate" in subgroups:
            (repgroup_tag, repgroup) = generate_live_groups(composite, "replicate",
                                                            subgroups["replicate"], dataset,
                                                            rep_tags)

    # second pass once all rep_techs are known
    if host is None:
        host ="https://www.lungepigenome.org/"
    for view_tag in composite["view"].get("group_order", []):
        view = composite["view"]["groups"][view_tag]
        output_types = view.get("output_type", [])
        file_format_types = view.get("file_format_type", [])
        file_format = view["type"].split()[0]
        for a_file in files:
            if a_file['file_format'] not in [file_format, "bed"]:
                continue
            if len(output_types) > 0 and a_file.get('output_type', 'unknown') not in output_types:
                continue
            if len(file_format_types) > 0 and a_file.get('file_format_type',
                                                         'unknown') not in file_format_types:
                continue
            rep_tech = a_file["rep_tech"]
            rep_tag = rep_techs[rep_tech]
            a_file["rep_tag"] = rep_tag
            track = {}
            track["type"] = view["type"]
            track["url"] = "%s%s" % (host,a_file["href"])
            track["showOnHubLoad"] = True
            # longLabel = vis_defs.get('file_defs', {}).get('longLabel')
            longLabel = ("{assay_title} of {biosample_term_name} {output_type}"
                             "{biological_replicate_number}")
            longLabel += " {experiment.accession} - {file.accession}"  # Always add the accessions
            track["name"] = sanitize_label(convert_mask(longLabel, dataset, a_file))
            
            # Expecting short label to change when making assay based composites
            #shortLabel = vis_defs.get('file_defs', {}).get('shortLabel',
                                                          # "{replicate} {output_type_short_label}")
            #track["shortLabel"] = sanitize_label(convert_mask(shortLabel, dataset, a_file))

            # How about subgroups!
            membership = {}
            membership["view"] = view["tag"]
            # view["tracks"].append(track)  # <==== This is how we connect them to the views
            for (group_tag, group) in composite["groups"].items():
                # "Replicates", "Biosample", "Targets", "Assay", ... member?
                group_title = group["title"]
                subgroups = group["groups"]
                if group_title == "Replicates":
                    # Must figure out membership
                    # Generate rep_tag for track, then
                    subgroup = subgroups.get(rep_tag)
                    # if subgroup is None:
                    #    subgroup = { "tag": rep_tag, "title": rep_tag }
                    #    group["groups"][rep_tag] = subgroup
                    if subgroup is not None:
                        membership[group_tag] = rep_tag
                        if "tracks" not in subgroup:
                            subgroup["tracks"] = []
                        subgroup["tracks"].append(track)  # <==== also connected to replicate
                elif group_title in ["Biosample", "Targets", "Assay", EXP_GROUP]:
                    assert(len(subgroups) == 1)
                    # if len(subgroups) == 1:
                    for (subgroup_tag, subgroup) in subgroups.items():
                        membership[group_tag] = subgroup["tag"]
                else:
                    assert(group_tag == "Don't know this group!")
            track["metadata"] = membership
            tracks.append(track)
    return tracks


def acc_composite_extend_with_tracks1(composite, vis_defs, dataset, assembly, host= None):
    '''Extends live experiment composite object with track definitions'''
    tracks = []
    rep_techs = {}
    files = []
    ucsc_assembly = composite['ucsc_assembly']
    # first time through just to get rep_tech
    group_order = composite["view"].get("group_order", [])
    for view_tag in group_order:
        view = composite["view"]["groups"][view_tag]
        output_types = view.get("output_type", [])
        file_format_types = view.get("file_format_type", [])
        file_format = view["type"].split()[0]
        #if file_format == "bigBed":
        #    view["type"] = "bigBed"  # scoreFilter implies score so 6 +
        #    format_type = view.get('file_format_type','')
        #log.warn("%d files looking for type %s" % ((len(dataset["files"]),view["type"]))
        for a_file in dataset["files"]:
            if a_file['status'] not in VISIBLE_FILE_STATUSES:
                continue
            if file_format != a_file['file_format']:
                continue
            if len(output_types) > 0 and a_file.get('output_type', 'unknown') not in output_types:
                continue
            if len(file_format_types) > 0 and a_file.get('file_format_type', 'unknown') not in file_format_types:
                continue
            if 'assembly' not in a_file or _ASSEMBLY_MAPPER.get(a_file['assembly'], a_file['assembly']) != ucsc_assembly:
                continue
            if "rep_tech" not in a_file:
                rep_tech = rep_for_file(a_file)
                a_file["rep_tech"] = rep_tech
            else:
                rep_tech = a_file["rep_tech"]
            rep_techs[rep_tech] = rep_tech
            files.append(a_file)
    if len(files) == 0:
        log.warn("No visualizable files for %s" % (dataset["accession"]))
        return None

    # convert rep_techs to simple reps
    rep_ix = 1
    rep_tags = []
    for rep_tech in sorted(rep_techs.keys()):  # ordered by a simple sort
        if rep_tech == "combined":
            rep_tag = "pool"
        else:
            rep_tag = "rep%02d" % rep_ix
            rep_ix += 1
        rep_techs[rep_tech] = rep_tag
        rep_tags.append(rep_tag)

    # Now we can fill in "Replicate" subgroups with with "replicate"
    other_groups = vis_defs.get("other_groups", []).get("groups", [])
    if "Replicates" in other_groups:
        group = other_groups["Replicates"]
        group_tag = group["tag"]
        subgroups = group["groups"]
        if "replicate" in subgroups:
            (repgroup_tag, repgroup) = generate_live_groups(composite, "replicate",
                                                            subgroups["replicate"], dataset,
                                                            rep_tags)

    # second pass once all rep_techs are known
    if host is None:
        host ="https://www.lungepigenome.org/"
    for view_tag in composite["view"].get("group_order", []):
        view = composite["view"]["groups"][view_tag]
        output_types = view.get("output_type", [])
        file_format_types = view.get("file_format_type", [])
        file_format = view["type"].split()[0]
        for a_file in files:
            if a_file['file_format'] not in [file_format, "bed"]:
                continue
            if len(output_types) > 0 and a_file.get('output_type', 'unknown') not in output_types:
                continue
            if len(file_format_types) > 0 and a_file.get('file_format_type',
                                                         'unknown') not in file_format_types:
                continue
            rep_tech = a_file["rep_tech"]
            rep_tag = rep_techs[rep_tech]
            a_file["rep_tag"] = rep_tag
            track = {}
            track["type"] = view["type"]
            track["url"] = "%s%s" % (host,a_file["href"])
            track["showOnHubLoad"] = False
            # longLabel = vis_defs.get('file_defs', {}).get('longLabel')
            longLabel = ("{assay_title} of {biosample_term_name} {output_type}"
                             "{biological_replicate_number}")
            longLabel += " {experiment.accession} - {file.accession}"  # Always add the accessions
            track["name"] = sanitize_label(convert_mask(longLabel, dataset, a_file))
            
            # Expecting short label to change when making assay based composites
            #shortLabel = vis_defs.get('file_defs', {}).get('shortLabel',
                                                          # "{replicate} {output_type_short_label}")
            #track["shortLabel"] = sanitize_label(convert_mask(shortLabel, dataset, a_file))

            # How about subgroups!
            membership = {}
            membership["view"] = view["tag"]
            # view["tracks"].append(track)  # <==== This is how we connect them to the views
            for (group_tag, group) in composite["groups"].items():
                # "Replicates", "Biosample", "Targets", "Assay", ... member?
                group_title = group["title"]
                subgroups = group["groups"]
                if group_title == "Replicates":
                    # Must figure out membership
                    # Generate rep_tag for track, then
                    subgroup = subgroups.get(rep_tag)
                    # if subgroup is None:
                    #    subgroup = { "tag": rep_tag, "title": rep_tag }
                    #    group["groups"][rep_tag] = subgroup
                    if subgroup is not None:
                        membership[group_tag] = rep_tag
                        if "tracks" not in subgroup:
                            subgroup["tracks"] = []
                        subgroup["tracks"].append(track)  # <==== also connected to replicate
                elif group_title in ["Biosample", "Targets", "Assay", EXP_GROUP]:
                    assert(len(subgroups) == 1)
                    # if len(subgroups) == 1:
                    for (subgroup_tag, subgroup) in subgroups.items():
                        membership[group_tag] = subgroup["tag"]
                else:
                    assert(group_tag == "Don't know this group!")
            track["metadata"] = membership
            tracks.append(track)
    return tracks


def make_acc_composite(dataset, assembly, host=None, hide=False):
    '''Converts experiment composite static definitions to live composite object'''
    if dataset["status"] not in VISIBLE_DATASET_STATUSES:
        log.debug("%s can't be visualized because it's not unreleased status:%s." %
                  (dataset["accession"], dataset["status"]))
        return {}
    vis_type = get_vis_type(dataset)
    vis_defs = lookup_vis_defs(vis_type)
    if vis_defs is None:
        log.debug("%s (vis_type: %s) has undiscoverable vis_defs." %
                 (dataset["accession"], vis_type))
        return {}
    composite = {}
    log.debug("%s has vis_type: %s." % (dataset["accession"],vis_type))

    ucsc_assembly = _ASSEMBLY_MAPPER.get(assembly, assembly)
    if assembly != ucsc_assembly:  # Sometimes 'assembly' is hg38 already.
        composite1['assembly'] = assembly
    composite1['ucsc_assembly'] = ucsc_assembly

    # plumbing for ihec, among other things:
    # for term in ['biosample_term_name', 'biosample_term_id', 'biosample_summary',
    #             'biosample_type', 'assay_term_id', 'assay_term_name']:
    #    if term in dataset:
    #        composite[term] = dataset[term]
    replicates = dataset.get("replicates", [])
 
    longLabel = vis_defs.get('longLabel',
                             '{assay_term_name} of {biosample_term_name} - {accession}')
    # views are always subGroup1
    composite1["view"] = {}
    title_to_tag = {}
    if "Views" in vis_defs:
        (tag, views) = generate_live_groups(composite1, "Views", vis_defs["Views"], dataset)
        composite1[tag] = views
        title_to_tag["Views"] = tag

    if "other_groups" in vis_defs:
        groups = vis_defs["other_groups"].get("groups", {})
        new_dimensions = {}
        new_filters = {}
        composite1["group_order"] = []
        composite1["groups"] = {}  # subgroups def by groups and group_order directly off composite
        group_order = vis_defs["other_groups"].get("group_order")
        preferred_order = []  # have to create preferred order based upon tags, not titles
        if group_order is None or not isinstance(group_order, list):
            group_order = sorted(groups.keys())
            preferred_order = "sorted"
        for subgroup_title in group_order:  # Replicates, Targets, Biosamples
            if subgroup_title not in groups:
                continue
            assert(subgroup_title in SUPPORTED_SUBGROUPS)
            (subgroup_tag, subgroup) = generate_live_groups(composite1, subgroup_title,
                                                            groups[subgroup_title], dataset)
            if isinstance(preferred_order, list):
                preferred_order.append(subgroup_tag)
            if "groups" in subgroup and len(subgroup["groups"]) > 0:
                title_to_tag[subgroup_title] = subgroup_tag
                composite1["groups"][subgroup_tag] = subgroup
                composite1["group_order"].append(subgroup_tag)
    tracks = acc_composite_extend_with_tracks(composite1, vis_defs, dataset, assembly, host=host)
    if tracks is None or len(tracks) == 0:
        # Already warned about files log.debug("No tracks for %s" % dataset["accession"])
        return {}
    composite[""] = tracks
    return tracks

def make_acc_composite1(dataset, assembly, host=None, hide=False):
    '''Converts experiment composite static definitions to live composite object'''
    if dataset["status"] not in VISIBLE_DATASET_STATUSES:
        log.debug("%s can't be visualized because it's not unreleased status:%s." %
                  (dataset["accession"], dataset["status"]))
        return {}
    vis_type = get_vis_type(dataset)
    vis_defs = lookup_vis_defs(vis_type)
    if vis_defs is None:
        log.debug("%s (vis_type: %s) has undiscoverable vis_defs." %
                 (dataset["accession"], vis_type))
        return {}
    composite = {}
    log.debug("%s has vis_type: %s." % (dataset["accession"],vis_type))

    ucsc_assembly = _ASSEMBLY_MAPPER.get(assembly, assembly)
    if assembly != ucsc_assembly:  # Sometimes 'assembly' is hg38 already.
        composite1['assembly'] = assembly
    composite1['ucsc_assembly'] = ucsc_assembly

    # plumbing for ihec, among other things:
    # for term in ['biosample_term_name', 'biosample_term_id', 'biosample_summary',
    #             'biosample_type', 'assay_term_id', 'assay_term_name']:
    #    if term in dataset:
    #        composite[term] = dataset[term]
    replicates = dataset.get("replicates", [])
 
    longLabel = vis_defs.get('longLabel',
                             '{assay_term_name} of {biosample_term_name} - {accession}')
    # views are always subGroup1
    composite1["view"] = {}
    title_to_tag = {}
    if "Views" in vis_defs:
        (tag, views) = generate_live_groups(composite1, "Views", vis_defs["Views"], dataset)
        composite1[tag] = views
        title_to_tag["Views"] = tag

    if "other_groups" in vis_defs:
        groups = vis_defs["other_groups"].get("groups", {})
        new_dimensions = {}
        new_filters = {}
        composite1["group_order"] = []
        composite1["groups"] = {}  # subgroups def by groups and group_order directly off composite
        group_order = vis_defs["other_groups"].get("group_order")
        preferred_order = []  # have to create preferred order based upon tags, not titles
        if group_order is None or not isinstance(group_order, list):
            group_order = sorted(groups.keys())
            preferred_order = "sorted"
        for subgroup_title in group_order:  # Replicates, Targets, Biosamples
            if subgroup_title not in groups:
                continue
            assert(subgroup_title in SUPPORTED_SUBGROUPS)
            (subgroup_tag, subgroup) = generate_live_groups(composite1, subgroup_title,
                                                            groups[subgroup_title], dataset)
            if isinstance(preferred_order, list):
                preferred_order.append(subgroup_tag)
            if "groups" in subgroup and len(subgroup["groups"]) > 0:
                title_to_tag[subgroup_title] = subgroup_tag
                composite1["groups"][subgroup_tag] = subgroup
                composite1["group_order"].append(subgroup_tag)
    tracks = acc_composite_extend_with_tracks1(composite1, vis_defs, dataset, assembly, host=host)
    if tracks is None or len(tracks) == 0:
        # Already warned about files log.debug("No tracks for %s" % dataset["accession"])
        return {}
    composite[""] = tracks
    return tracks


def remodel_acc_to_set_composites(acc_composites, hide_after=None):
    '''Given a set of (search result) acc based composites, remodel them to set based composites.'''
    if acc_composites is None or len(acc_composites) == 0:
        return {}

    set_composites = {}

    for acc in sorted(acc_composites.keys()):
        acc_composite = acc_composites[acc]
        if acc_composite is None or len(acc_composite) == 0:
            # log.debug("Found empty acc_composite for %s" % (acc))
            set_composites[acc] = {}  # wounded composite are added for evidence
            continue

        # Only show the first n datasets
        if hide_after is not None:
            if hide_after <= 0:
                for track in acc_composite.get("tracks", {}):
                    track["checked"] = "off"
            else:
                hide_after -= 1
        # If set_composite of this vis_type doesn't exist, create it
        
        #vis_type = acc_composite["vis_type"]
        
        #vis_defs = lookup_vis_defs(vis_type)
        #assert(vis_type is not None)
        #if vis_type not in set_composites.keys():  # First one so just drop in place
        #    set_composite = acc_composite  # Don't bother with deep copy.
        #    set_defs = vis_defs.get("assay_composite", {})
        #    set_composite["name"] = vis_type.lower()  # is there something more elegant?
        #    for tag in ["visibility"]:
        #        if tag in set_defs:
        #            set_composite[tag] = set_defs[tag]  # Not expecting any token substitutions!!!
        #    set_composite['html'] = vis_type
        #    set_composites[vis_type] = set_composite

        #else:  # Adding an acc_composite to an existing set_composite
        #set_composite = set_composites[vis_type]
        #set_composite['composite_type'] = 'set'
        # combine views
        #    set_views = set_composite.get("view", [])
        #    acc_views = acc_composite.get("view", {})
        #    for view_tag in acc_views["group_order"]:
        #        acc_view = acc_views["groups"][view_tag]
        #        if view_tag not in set_views["groups"].keys():  # Should never happen
                    # log.debug("Surprise: view %s not found before" % view_tag)
        #            insert_live_group(set_views, view_tag, acc_view)
        #        else:  # View is already defined but tracks need to be appended.
        #            set_view = set_views["groups"][view_tag]
        #            if "tracks" not in set_view:
        #                set_view["tracks"] = acc_view.get("tracks", [])
        #            else:
        #                set_view["tracks"].extend(acc_view.get("tracks", []))

            # All tracks in one set: not needed.

            # Combine subgroups:
            #for group_tag in acc_composite["group_order"]:
            #    acc_group = acc_composite["groups"][group_tag]
            #    if group_tag not in set_composite["groups"].keys():  # Should never happen
                    # log.debug("Surprise: group %s not found before" % group_tag)
            #        insert_live_group(set_composite, group_tag, acc_group)
            #    else:  # Need to handle subgroups which definitely may not be there.
            #        set_group = set_composite["groups"].get(group_tag, {})
            #        acc_subgroups = acc_group.get("groups", {})
                    # acc_subgroup_order = acc_group.get("group_order")
            #        for subgroup_tag in acc_subgroups.keys():
            #            if subgroup_tag not in set_group.get("groups", {}).keys():
                            # Adding biosamples, targets, and reps
            #                insert_live_group(set_group, subgroup_tag, acc_subgroups[subgroup_tag])
    
    return set_composites


def remodel_acc_to_ihec_json(acc_composites, request=None):
    '''TODO: remodels 1+ acc_composites into an IHEC hub json structure.'''
    if acc_composites is None or len(acc_composites) == 0:
        return {}

    if request:
        host = "https://www.lungepigenome.org"
    else:
        host = "https://www.lungepigenome.org"
    # {
    # "hub_description": { ... },  similar to hub.txt/genome.txt
    # "datasets": { ... },         one per experiment, contains "browser" objects, one per track
    # "samples": { ... }           one per biosample
    # }
    ihec_json = {}

    # "hub_description": {     similar to hub.txt/genome.txt
    #     "taxon_id": ...,           Species taxonomy id. (e.g. human = 9606, Mus mus. 10090)
    #     "assembly": "...",         UCSC: hg19, hg38
    #     "publishing_group": "...", ENCODE
    #     "email": "...",            t2dream-l@mailman.ucsd.edu
    #     "date": "...",             ISO 8601 format: YYYY-MM-DD
    #     "description": "...",      (optional)
    #     "description_url": "...",  (optional) If single composite: html  (e.g. ANNO.html)
    # }
    hub_description = {}
    hub_description["publishing_group"] = "LungEpigenome"
    hub_description["email"] = "lungepigenome-l@mailman.ucsd.edu"
    hub_description["date"] = time.strftime('%Y-%m-%d', time.gmtime())
    # hub_description["description"] = "...",      (optional)
    # hub_description["description_url"] = "...",  (optional)
    #                                    If single composite: html (e.g. ANNO.html)
    ihec_json["hub_description"] = hub_description

    # "samples": {             one per biosample
    #     "sample_id_1": {                   biosample term
    #         "sample_ontology_uri": "...",  UBERON or CL
    #         "molecule": "...",             ["total RNA", "polyA RNA", "cytoplasmic RNA",
    #                                         "nuclear RNA", "genomic DNA", "protein", "other"]
    #         "disease": "...",              optional?
    #         "disease_ontology_uri": "...", optional?
    #         "biomaterial_type": "...",     ["Cell Line", "Primary Cell", "Primary Cell Culture",
    #                                         "Primary Tissue"]
    #     },
    #     "sample_id_2": { ... }
    # }
    samples = {}
    ihec_json["samples"] = samples

    # "datasets": {
    #    "experiment_1": {    one per experiment    accession
    #        "sample_id": "...",                    biosample_term
    #        "experiment_attributes": {
    #            "experiment_type": "...",
    #            "assay_type": "...",               assay_term_name  Match ontology URI
    #                                                           (e.g. 'DNA Methylation')
    #            "experiment_ontology_uri": "...",  assay_term_id (e.g. OBI:0000716)
    #            "reference_registry_id": "..."     IHEC Reference Epigenome registry ID,
    #                                                 assigned after submitting to EpiRR
    #        },
    #        "analysis_attributes": {
    #            "analysis_group": "...",              metadata_pairs['laboratory']
    #            "alignment_software": "...",          pipeline?
    #            "alignment_software_version": "...",
    #            "analysis_software": "...",
    #            "analysis_software_version": "..."
    #        },
    #        "browser": {
    #            "signal_forward": [               view
    #                {
    #                    "big_data_url": "...",    obvious
    #                    "description_url": "...", Perhaps not
    #                    "md5sum": "...",          Add this to metadata pairs?
    #                    "subtype": "...",         More details?
    #                    "sample_source": "...",   pooled,rep1,rep2
    #                    "primary":                pooled or rep1 ?
    #                },
    #                { ... }
    #            ],
    #            "signal_reverse": [ { ... } ]
    #        }
    #    },
    #    "experiment_2": {
    #        ...
    #    },
    # }
    datasets = {}
    ihec_json["datasets"] = datasets

    # Other collections
    assays = {}
    pipelines = {}

    for acc in acc_composites.keys():
        acc_composite = acc_composites[acc]
        if acc_composite is None or len(acc_composite) == 0:
            # log.debug("Found empty acc_composite for %s" % (acc))
            continue  # wounded composite can be dropped or added for evidence

        # From any acc_composite, update these:
        if "assembly" not in hub_description:
            ucsc_assembly = acc_composite.get('ucsc_assembly')
            if ucsc_assembly:
                hub_description["assembly"] = ucsc_assembly
            taxon_id = acc_composite.get('taxon_id')
            if taxon_id:
                hub_description["taxon_id"] = taxon_id

        dataset = {}
        datasets[acc] = dataset

        # Find/create sample:
        biosample_name = acc_composite.get('biosample_term_name', 'none')
        if biosample_name == 'none':
            log.debug("acc_composite %s is missing biosample_name", acc)
        molecule = acc_composite.get('molecule', 'none')  # ["total RNA", "polyA RNA", ...
        if molecule == 'none':
            log.debug("acc_composite %s is missing molecule", acc)
        sample_id = "%s; %s" % (biosample_name, molecule)
        if sample_id not in samples:
            sample = {}
            biosample_term_id = acc_composite.get('biosample_term_id')
            if biosample_term_id:
                sample["sample_ontology_uri"] = biosample_term_id
            biosample_type = acc_composite.get('biosample_type')  # ["Cell Line","Primary Cell", ...
            if biosample_type:
                sample["biomaterial_type"] = biosample_type
            sample["molecule"] = molecule
            # sample["disease"] =
            # sample["disease_ontology_uri"] =
            samples[sample_id] = sample
        dataset["sample_id"] = sample_id

        # find/create experiment_attributes:
        assay_id = acc_composite.get('assay_term_id')
        if assay_id:
            if assay_id in assays:
                experiment_attributes = deepcopy(assays[assay_id])  # deepcopy needed?
            else:
                experiment_attributes = {}
                experiment_attributes["experiment_ontology_uri"] = assay_id
                assay_name = acc_composite.get('assay_term_name')
                if assay_name:
                    experiment_attributes["assay_type"] = assay_name
                # "experiment_type": assay_name # EpiRR
                # "reference_registry_id": "..."     IHEC Reference Epigenome registry ID,
                #                                     assigned after submitting to EpiRR
                assays[assay_id] = experiment_attributes
            dataset["experiment_attributes"] = experiment_attributes

        # find/create analysis_attributes:
        # WARNING: This could go crazy!
        pipeline_title = acc_composite.get('pipeline')
        if pipeline_title:
            if pipeline_title in pipelines:
                analysis_attributes = deepcopy(pipelines[pipeline_title])  # deepcopy needed?
            else:
                analysis_attributes = {}
                pipeline_group = acc_composite.get('pipeline_group')
                if pipeline_group:
                    analysis_attributes["analysis_group"] = pipeline_group     # "ENCODE DCC"
                analysis_attributes["analysis_software"] = pipeline_title
                # "analysis_software_version": "..."  # NOTE: version is hard for the whole exp
                # "alignment_software": "...",        # NOTE: sw *could* be found but not worth it
                # "alignment_software_version": "...",
                #        },
                pipelines[pipeline_title] = analysis_attributes
            dataset["analysis_attributes"] = analysis_attributes

        # create browser, which holds views, which hold tracks:
        browser = {}
        dataset["browser"] = browser

        # create views, which will hold tracks
        # ihec_views = {}
        views = acc_composite.get("view", [])
        for view_tag in views["group_order"]:
            view = views["groups"][view_tag]

            # Add tracks to views
            tracks = view.get("tracks", [])
            if len(tracks) == 0:
                continue
            ihec_view = []

            for track in tracks:
                ihec_track = {}
                # ["bigDataUrl","longLabel","shortLabel","type","color","altColor"]
                ihec_track["big_data_url"] = host + track["url"] #no proxy required
                ihec_track["description_url"] = '%s/%s/' % (host, acc)
                if request:
                    url = '/'.join(request.url.split('/')[0:-1])
                    url += '/' + acc + '.html'
                    ihec_track["description_url"] = url
                md5sum = track.get('md5sum')
                if md5sum:
                    ihec_track["md5sum"] = md5sum
                ihec_track["subtype"] = track["name"]
                rep_membership = track.get("membership", {}).get("REP")
                rep_group = acc_composite.get("groups", {}).get("REP")
                if rep_membership and rep_group:
                    if rep_membership in rep_group:
                        ihec_track["sample_source"] = rep_group[rep_membership]["title"]
                        subgroup_order = sorted(rep_group["groups"].keys())
                        ihec_track["primary"] = (rep_membership == subgroup_order[0])
                ihec_track["view"] = view["title"]
                ihec_view.append(ihec_track)
            if len(ihec_view) > 0:
                browser[view["title"]] = ihec_view
    
    return ihec_json


def find_or_make_acc_composite(request, assembly, acc, dataset=None, hide=False, regen=False):
    '''Returns json for a single experiment 'acc_composite'.'''
    acc_composite = None
    es_key = acc + "_" + assembly
    found_or_made = "found"
    if USE_CACHE and not regen:  # Find composite?
        acc_composite = get_from_es(request, es_key)

    if acc_composite is None:
        request_dataset = (dataset is None)
        if request_dataset:
            dataset = request.embed("/datasets/" + acc + '/', as_user=True)
            # log.debug("find_or_make_acc_composite len(results) = %d   %.3f secs" %
            #           (len(results),(time.time() - PROFILE_START_TIME)))
        host=request.host_url
        if host is None or host.find("localhost") > -1:
            host = "https://www.lungepigenome.org"
        
        acc_composite = make_acc_composite(dataset, assembly, host=host, hide=hide)
        if USE_CACHE:
            add_to_es(request, es_key, acc_composite)
        found_or_made = "made"

        if request_dataset:  # Manage meomory
            del dataset
    return (found_or_made, acc_composite)

def find_or_make_acc_composite1(request, assembly, acc, dataset=None, hide=False, regen=False):
    '''Returns json for a single experiment 'acc_composite'.'''
    acc_composite = None
    es_key = acc + "_" + assembly
    found_or_made = "found"
    if USE_CACHE and not regen:  # Find composite?
        acc_composite = get_from_es(request, es_key)

    if acc_composite is None:
        request_dataset = (dataset is None)
        if request_dataset:
            dataset = request.embed("/datasets/" + acc + '/', as_user=True)
            # log.debug("find_or_make_acc_composite len(results) = %d   %.3f secs" %
            #           (len(results),(time.time() - PROFILE_START_TIME)))
        host=request.host_url
        if host is None or host.find("localhost") > -1:
            host = "https://www.lungepigenome.org"
        
        acc_composite = make_acc_composite1(dataset, assembly, host=host, hide=hide)
        if USE_CACHE:
            add_to_es(request, es_key, acc_composite)
        found_or_made = "made"

        if request_dataset:  # Manage meomory
            del dataset
    return (found_or_made, acc_composite)


def generate_trackDb(request, dataset, assembly, hide=False, regen=False):
    '''Returns string content for a requested  single experiment trackDb.txt.'''
    # local test: bigBed: curl http://localhost:8000/experiments/ENCSR000DZQ/@@hub/hg19/trackDb.txt
    #             bigWig: curl http://localhost:8000/experiments/ENCSR000ADH/@@hub/mm9/trackDb.txt
    # CHIP: https://4217-trackhub-spa-ab9cd63-tdreszer.demo.encodedcc.org/experiments/ENCSR645BCH/@@hub/GRCh38/trackDb.txt
    # LRNA: curl https://4217-trackhub-spa-ab9cd63-tdreszer.demo.encodedcc.org/experiments/ENCSR000AAA/@@hub/GRCh38/trackDb.txt
    (page,suffix,cmd) = urlpage(request.url)
    json_out = (suffix == 'json')
    vis_json = (page == 'vis_blob' and json_out)
    ihec_out = (page == 'ihec' and json_out)
    if not regen:
        regen = ('regen' in cmd)
        if not regen: # TODO temporary
            regen = ihec_out
    
    acc = dataset['accession']
    ucsc_assembly = _ASSEMBLY_MAPPER.get(assembly, assembly)
    (found_or_made, acc_composite) = find_or_make_acc_composite(request, ucsc_assembly,
                                                                dataset["accession"], dataset,
                                                                hide=hide, regen=regen)
    # vis_type = acc_composite.get("vis_type", get_vis_type(dataset))
    #if regen:  # Want to see message if regen was requested
    #    log.info("%s composite %s_%s %s len(json):%d %.3f" % (found_or_made, dataset['accession'],
    #             ucsc_assembly, vis_type, len(json.dumps(acc_composite)),
    #             (time.time() - PROFILE_START_TIME)))
    #else:
    #    log.debug("%s composite %s_%s %s len(json):%d %.3f" % (found_or_made, dataset['accession'],
    #              ucsc_assembly, vis_type, len(json.dumps(acc_composite)),
    #              (time.time() - PROFILE_START_TIME)))
    if ihec_out:
        ihec_json = remodel_acc_to_ihec_json({acc: acc_composite}, request)
        return json.dumps(ihec_json, indent=4, sort_keys=True)
    if vis_json:
        return json.dumps(acc_composite, indent=4, sort_keys=True)
    elif json_out:
        acc_composites = {} # Standardize output for biodalliance use
        acc_composites[acc] = acc_composite
        return json.dumps(acc_composite, indent=4, sort_keys=True)
    elif ihec_out:
        ihec_json = remodel_acc_to_ihec_json({acc: acc_composite}, request)
        return json.dumps(ihec_json, indent=4, sort_keys=True)


def generate_batch_trackDb(request, hide=False, regen=False):
    '''Returns string content for a requested multi-experiment trackDb.txt.'''
    # local test: RNA-seq: curl https://../batch_hub/type=Experiment,,assay_title=RNA-seq,,award.rfa=ENCODE3,,status=released,,assembly=GRCh38,,replicates.library.biosample.biosample_type=induced+pluripotent+stem+cell+line/GRCh38/trackDb.txt

    (page,suffix,cmd) = urlpage(request.url)
    json_out = (suffix == 'json')
    vis_json = (page == 'vis_blob' and json_out)  # ...&bly=hg19&accjson/hg19/trackDb.txt
    ihec_out = (page == 'ihec' and json_out)
    if not regen:
        regen = ('regen' in cmd)
        if not regen: # TODO temporary
            regen = ihec_out
    assembly = str(request.matchdict['assembly'])
    log.debug("Request for %s trackDb begins   %.3f secs" %
              (assembly, (time.time() - PROFILE_START_TIME)))
    # for track hubs on epigenome browser
    param_list1 = (request.matchdict['search_params'].replace(',,', '='))
    param_list = parse_qs(param_list1.replace('|', '&'))
    set_composites = None
    # Have to make it.
    assemblies = ASSEMBLY_MAPPINGS.get(assembly, [assembly])
    params = {
        'files.file_format': BIGBED_FILE_TYPES + HIC_FILE_TYPES + BIGWIG_FILE_TYPES,
    }
    params.update(param_list)
    params.update({
        'assembly': assemblies,
        'limit': ['all'],
    })
    if USE_CACHE:
        params['frame'] = ['object']
    else:
        params['frame'] = ['embedded']
        
    view = 'search'
    if 'region' in param_list:
        view = 'variant-search'
    path = '/%s/?%s' % (view, urlencode(params, True))
    results = request.embed(path, as_user=True)['@graph']
    
    if not USE_CACHE:
        log.debug("len(results) = %d   %.3f secs" %
                  (len(results), (time.time() - PROFILE_START_TIME)))
    else:
        # Note: better memory usage to get acc array from non-embedded results,
        # since acc_composites should be in cache
        accs = [result['accession'] for result in results]
        del results

    acc_composites = {}
    acc_composites1 = []
    found = 0
    made = 0
    if USE_CACHE and not regen:
        es_keys = [acc + "_" + assembly for acc in accs]
        acc_composites = search_es(request, es_keys)
        found = len(acc_composites.keys())
    accs = [result['accession'] for result in results]
    
    missing_accs = []
    if found == 0:
        missing_accs = accs
    # Don't bother if cache is primed.
    elif found < (len(accs) * 3 / 4):  # some heuristic to decide when too few means regenerate
        missing_accs = list(set(accs) - set(acc_composites.keys()))

    if len(missing_accs) > 0:  # if 0 were found in cache try generating (for pre-primed-cache access)
        #if not USE_CACHE: # already have dataset
        for dataset in results:
            acc = dataset['accession']
            (found_or_made, acc_composite) = find_or_make_acc_composite(request, assembly, acc,
                                                                            dataset, hide=hide,
                                                                            regen=True)
            made += 1
            acc_composites = acc_composite
            acc_composites1.extend(acc_composites)
        #else:       # will have to fetch embedded dataset
        #    for acc in missing_accs:
        #        (found_or_made, acc_composite) = find_or_make_acc_composite(request, assembly, acc,
        #                                                                    None, hide=hide,
        #                                                                    regen=regen)
        #        if found_or_made == "made":
        #            made += 1
                    # log.debug("%s composite %s" % (found_or_made,acc))
        #        else:
        #            found += 1
        #        acc_composites[acc] = acc_composite

    blob = ""
    set_composites = {}
    if made > 0:
        if ihec_out:
            ihec_json = remodel_acc_to_ihec_json(acc_composites, request)
            blob = json.dumps(ihec_json, indent=4, sort_keys=True)
        if json_out:
            blob = json.dumps(acc_composites1, indent=4, sort_keys=True)
            
        #else:
        #    set_composites = remodel_acc_to_set_composites(acc_composites, hide_after=100)

        #    json_out = (request.url.find("jsonout") > -1)  # ...&bly=hg19&jsonout/hg19/trackDb.txt
        #    if json_out:
        #        blob = json.dumps(set_composites, indent=4, sort_keys=True)

    if regen:  # Want to see message if regen was requested
        log.info("acc_composites: %s generated, %d found, %d set(s). len(txt):%s  %.3f secs" %
                 (made, found, len(set_composites), len(blob), (time.time() - PROFILE_START_TIME)))
    else:
        log.debug("acc_composites: %s generated, %d found, %d set(s). len(txt):%s  %.3f secs" %
                  (made, found, len(set_composites), len(blob), (time.time() - PROFILE_START_TIME)))
    
    return blob


def generate_batch_trackDb1(request, hide=False, regen=False):
    '''Returns string content for a requested multi-experiment for LEB epigenome hub.'''
    # local test: RNA-seq: curl https://../batch_hub/type=Experiment,,assay_title=RNA-seq,,award.rfa=ENCODE3,,status=released,,assembly=GRCh38,,replicates.library.biosample.biosample_type=induced+pluripotent+stem+cell+line/GRCh38/trackDb.txt

    (page,suffix,cmd) = urlpage(request.url)
    json_out = (suffix == 'json')
    vis_json = (page == 'vis_blob' and json_out)  # ...&bly=hg19&accjson/hg19/trackDb.txt
    ihec_out = (page == 'ihec' and json_out)
    if not regen:
        regen = ('regen' in cmd)
        if not regen: # TODO temporary
            regen = ihec_out
    assembly = str(request.matchdict['assembly'])
    log.debug("Request for %s trackDb begins   %.3f secs" %
              (assembly, (time.time() - PROFILE_START_TIME)))
    # Have to make it.
    assemblies = ASSEMBLY_MAPPINGS.get(assembly, [assembly])
    params = {
        'files.file_format': BIGBED_FILE_TYPES + HIC_FILE_TYPES + BIGWIG_FILE_TYPES,
    }
    params.update(param_list)
    params.update({
        'assembly': assemblies,
        'limit': ['all'],
    })
    if USE_CACHE:
        params['frame'] = ['object']
    else:
        params['frame'] = ['embedded']
        
    view = 'search'
    if 'region' in param_list:
        view = 'variant-search'
    path = '/%s/?%s' % (view, urlencode(params, True))
    results = request.embed(path, as_user=True)['@graph']
    
    if not USE_CACHE:
        log.debug("len(results) = %d   %.3f secs" %
                  (len(results), (time.time() - PROFILE_START_TIME)))
    else:
        # Note: better memory usage to get acc array from non-embedded results,
        # since acc_composites should be in cache
        accs = [result['accession'] for result in results]
        del results

    acc_composites = {}
    acc_composites1 = []
    found = 0
    made = 0
    if USE_CACHE and not regen:
        es_keys = [acc + "_" + assembly for acc in accs]
        acc_composites = search_es(request, es_keys)
        found = len(acc_composites.keys())
    accs = [result['accession'] for result in results]
    
    missing_accs = []
    if found == 0:
        missing_accs = accs
    # Don't bother if cache is primed.
    elif found < (len(accs) * 3 / 4):  # some heuristic to decide when too few means regenerate
        missing_accs = list(set(accs) - set(acc_composites.keys()))

    if len(missing_accs) > 0:  # if 0 were found in cache try generating (for pre-primed-cache access)
        #if not USE_CACHE: # already have dataset
        for dataset in results:
            acc = dataset['accession']
            (found_or_made, acc_composite) = find_or_make_acc_composite1(request, assembly, acc,
                                                                            dataset, hide=hide,
                                                                            regen=True)
            made += 1
            acc_composites = acc_composite
            acc_composites1.extend(acc_composites)
        #else:       # will have to fetch embedded dataset
        #    for acc in missing_accs:
        #        (found_or_made, acc_composite) = find_or_make_acc_composite(request, assembly, acc,
        #                                                                    None, hide=hide,
        #                                                                    regen=regen)
        #        if found_or_made == "made":
        #            made += 1
                    # log.debug("%s composite %s" % (found_or_made,acc))
        #        else:
        #            found += 1
        #        acc_composites[acc] = acc_composite

    blob = ""
    set_composites = {}
    if made > 0:
        if ihec_out:
            ihec_json = remodel_acc_to_ihec_json(acc_composites, request)
            blob = json.dumps(ihec_json, indent=4, sort_keys=True)
        if json_out:
            blob = json.dumps(acc_composites1, indent=4, sort_keys=True)
            
        #else:
        #    set_composites = remodel_acc_to_set_composites(acc_composites, hide_after=100)

        #    json_out = (request.url.find("jsonout") > -1)  # ...&bly=hg19&jsonout/hg19/trackDb.txt
        #    if json_out:
        #        blob = json.dumps(set_composites, indent=4, sort_keys=True)

    if regen:  # Want to see message if regen was requested
        log.info("acc_composites: %s generated, %d found, %d set(s). len(txt):%s  %.3f secs" %
                 (made, found, len(set_composites), len(blob), (time.time() - PROFILE_START_TIME)))
    else:
        log.debug("acc_composites: %s generated, %d found, %d set(s). len(txt):%s  %.3f secs" %
                  (made, found, len(set_composites), len(blob), (time.time() - PROFILE_START_TIME)))
    
    return blob


def readable_time(secs_float):
    '''Return string of days, hours, minutes, seconds'''
    intervals = [1, 60, 60*60, 60*60*24]
    terms = [('second', 'seconds'), ('minute', 'minutes'), ('hour', 'hours'), ('day', 'days')]

    amount = int(secs_float)
    msecs = int(round(secs_float * 1000) - (amount * 1000))

    result = ""
    for ix in range(len(terms)-1, -1, -1):  # 3,2,1,0
        interval = intervals[ix]
        a = amount // interval
        if a > 0 or interval == 1:
            result += "%d %s, " % (a, terms[ix][a % 1])
            amount -= a * interval
    if msecs > 0:
        result += "%d msecs" % (msecs)
    else:
        result = result[:-2]

    return result


def vis_cache_add(request, dataset, start_time=None):
    '''For a single embedded dataset, builds and adds vis_blobs to es cache for each relevant assembly.'''
    if start_time is None:
        start_time = time.time()
    if not object_is_visualizable(dataset):
        return None
    acc = dataset['accession']
    assemblies = dataset['assembly']
    vis_blobs = []
    for assembly in assemblies:
        ucsc_assembly = _ASSEMBLY_MAPPER.get(assembly, assembly)
        (made, vis_blob) = find_or_make_acc_composite(request, ucsc_assembly, acc, dataset,regen=True)
        if vis_blob:
            vis_blobs.append(vis_blob)
            log.debug("primed vis_cache with vis_blob %s_%s '%s'  %.3f secs" % (acc, ucsc_assembly,vis_blob.get('vis_type', ''), (time.time() - start_time)))
         # Took 12h32m on initial
         # else:
         #    log.debug("prime_vis_es_cache for %s_%s unvisualizable '%s'" % \
         #                                (acc,ucsc_assembly,get_vis_type(dataset)))
    return vis_blobs

@view_config(context=Item, name='index-vis', permission='index', request_method='GET')
def item_index_vis(context, request):
    '''Called during secondary indexing to add one uuid to vis cache.'''
    start_time = time.time()
    uuid = str(context.uuid)
    dataset = request.embed(uuid)
    return vis_cache_add(request, dataset, start_time)


def render(data):
    arr = []
    for i in range(len(data)):
        temp = list(data.popitem())
        str1 = ' '.join(temp)
        arr.append(str1)
    return arr


def get_genome_txt(assembly):
    # UCSC shim
    ucsc_assembly = _ASSEMBLY_MAPPER.get(assembly, assembly)
    genome = OrderedDict([
        ('trackDb', ucsc_assembly + '/trackDb.txt'),
        ('genome', ucsc_assembly)
    ])
    return render(genome)


def get_genomes_txt(assemblies):
    blob = ''
    ucsc_assemblies = set()
    for assembly in assemblies:
        ucsc_assemblies.add(_ASSEMBLY_MAPPER.get(assembly, assembly))
    for ucsc_assembly in ucsc_assemblies:
        if blob == '':
            blob = NEWLINE.join(get_genome_txt(ucsc_assembly))
        else:
            blob += 2 * NEWLINE + NEWLINE.join(get_genome_txt(ucsc_assembly))
    return blob


def get_hub(label, comment=None, name=None):
    if name is None:
        name = sanitize_name(label.split()[0])
    if comment is None:
        comment = "Generated by the team"
    hub = OrderedDict([
        ('email', 'lungepigenome-l@mailman.ucsd.edu'),
        ('genomesFile', 'genomes.txt'),
        ('longLabel', 'LungEpigenome'),
        ('shortLabel', 'Hub (' + label + ')'),
        ('hub', 'LungEpigenome_' + name),
        ('#', comment)
    ])
    return render(hub)

def browsers_available(assemblies, status, files, types, item_type=None):
    '''Retrurns list of browsers this object visualizable on.'''
    if "Dataset" not in types:
        return []
    if item_type is None:
        visualizabe_types = set(VISIBLE_DATASET_TYPES)
        if visualizabe_types.isdisjoint(types):
            return []
    elif item_type not in VISIBLE_DATASET_TYPES_LC:
        return []
    if not files:
        return []
    browsers = set()
    for assembly in assemblies:
        mapped_assembly = _ASSEMBLY_MAPPER_FULL[assembly]
        if not mapped_assembly:
            continue
        if 'ucsc_assembly' in mapped_assembly:
            browsers.add('ucsc')
        if 'ensembl_host' in mapped_assembly:
            browsers.add('ensembl')
        if 'quickview' in mapped_assembly:
            browsers.add('quickview')
    if status not in VISIBLE_DATASET_STATUSES:
        #if status not in QUICKVIEW_STATUSES_BLOCKED:
        #    return ["quickview"]
        return []
    return list(browsers)
def object_is_visualizable(obj,assembly=None):
    '''Retrurns list of browsers this object visualizable on.'''
    if 'accession' not in obj:
        return False
    if assembly is not None:
        assemblies = [ assembly ]
    else:
        assemblies = obj.get('assembly',[])
    return len(browsers) > 0
def vis_format_url(browser, path, assembly, position=None):
    '''Given a url to hub.txt, returns the url to an external browser or None.'''
    mapped_assembly = _ASSEMBLY_MAPPER_FULL[assembly]
    if not mapped_assembly:
        return None
def generate_html(context, request):
    ''' Generates and returns HTML for the track hub'''

    # First determine if single dataset or collection
    # log.debug("HTML request: %s" % request.url)

    html_requested = request.url.split('/')[-1].split('.')[0]
    if html_requested.startswith('ENCSR'):
        embedded = request.embed(request.resource_path(context))
        acc = embedded['accession']
        log.debug("generate_html for %s   %.3f secs" % (acc, (time.time() - PROFILE_START_TIME)))
        assert(html_requested == acc)

        vis_type = get_vis_type(embedded)
        vis_defs = lookup_vis_defs(vis_type)
        longLabel = vis_defs.get('longLabel',
                                 '{assay_term_name} of {biosample_term_name} - {accession}')
        longLabel = sanitize_label(convert_mask(longLabel, embedded))

        link = request.host_url + '/experiments/' + acc + '/'
        acc_link = '<a href={link}>{accession}<a>'.format(link=link, accession=acc)
        if longLabel.find(acc) != -1:
            longLabel = longLabel.replace(acc, acc_link)
        else:
            longLabel += " - " + acc_link
        page = '<h2>%s</h2>' % longLabel

    else:  # collection
        vis_type = html_requested
        vis_defs = lookup_vis_defs(vis_type)
        longLabel = vis_defs.get('assay_composite', {}).get('longLabel',
                                                            "Unknown collection of experiments")
        page = '<h2>%s</h2>' % longLabel

        # TO IMPROVE: limit the search url to this assay only.
        # Not easy since vis_def is not 1:1 with assay
        try:
            param_list = parse_qs(request.matchdict['search_params'].replace(',,', '&'))
            search_url = '%s/search/?%s' % (request.host_url, urlencode(param_list, True))
            # search_url = (request.url).split('@@hub')[0]
            search_link = '<a href=%s>Original search<a><BR>' % search_url
            page += search_link
        except:
            pass

    # TODO: Extend page with assay specific details
    details = vis_defs.get("html_detail")
    if details is not None:
        page += details

    return page  # data_description + header + file_table
def urlpage(url):
    '''returns (page,suffix,cmd) from url: as ('track','json','regen') from ./../track.regen.json'''
    url_end = url.split('/')[-1]
    parts = url_end.split('.')
    page = parts[0]
    suffix = parts[-1] if len(parts) > 1 else 'txt'
    cmd = parts[1]     if len(parts) > 2 else ''
    return (page, suffix, cmd)

def generate_batch_hubs(context, request):
    '''search for the input params and return the trackhub'''
    global PROFILE_START_TIME
    PROFILE_START_TIME = time.time()

    results = {}
    (page,suffix,cmd) = urlpage(request.url)
    log.debug('Requesting %s.%s#%s' % (page,suffix,cmd))
    if (suffix == 'txt' and page == 'trackDb') or (suffix == 'json' and page in ['trackDb','ihec','vis_blob']):
        return generate_batch_trackDb(request)
        
    elif page == 'hub' and suffix == 'txt':
        terms = request.matchdict['search_params'].replace(',,', '&')
        pairs = terms.split('&')
        label = "search:"
        for pair in sorted(pairs):
            (var, val) = pair.split('=')
            if var not in ["type", "assembly", "status", "limit"]:
                label += " %s" % val.replace('+', ' ')
        return NEWLINE.join(get_hub(label, request.url))
    elif page == 'genomes' and suffix == 'txt':
        search_params = request.matchdict['search_params']
        if search_params.find('bed6+') > -1:
            search_params = search_params.replace('bed6+,,','bed6%2B,,')
        log.debug('search_params: %s' % (search_params))
        #param_list = parse_qs(request.matchdict['search_params'].replace(',,', '&'))
        param_list = parse_qs(search_params.replace(',,', '&'))
        log.debug('parse_qs: %s' % (param_list))
        view = 'search'
        if 'region' in param_list:
            view = 'variant-search'
        path = '/%s/?%s' % (view, urlencode(param_list, True))
        log.debug('Path in hunt for assembly %s' % (path))
        results = request.embed(path, as_user=True)
        # log.debug("generate_batch(genomes) len(results) = %d   %.3f secs" %
        #           (len(results),(time.time() - PROFILE_START_TIME)))
        g_text = ''
        if 'assembly' in param_list:
            g_text = get_genomes_txt(param_list.get('assembly'))
        else:
            for facet in results['facets']:
                if facet['field'] == 'assembly':
                    assemblies = []
                    for term in facet['terms']:
                        if term['doc_count'] != 0:
                            assemblies.append(term['key'])
                    if len(assemblies) > 0:
                        g_text = get_genomes_txt(assemblies)
            if g_text == '':
                log.debug('Requesting %s.%s#%s NO ASSEMBLY !!!' % (page,suffix,cmd))
                g_text = json.dumps(results,indent=4)
                assemblies = [result['assemblies'] for result in results['@graph']]
                assembly_set = set(assemblies)
                assemblies = list(assembly_set)
                log.debug('Found %d ASSEMBLY !!!' % len(assemblies))
#/search/?type=Experiment&lab.title=Ali+Mortazavi%2C+UCI&assay_title=microRNA+counts&status=released&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&files.file_type=bigBed+bed6%2B&organ_slims=intestine
#/search/?type=Experimentlab.title=Ali+Mortazavi%2C+UCI&&assay_title=microRNA+counts&status=released&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&files.file_type=bigBed+bed6+organ_slims=intestine&
#/search/?assay_title=microRNA+counts&organ_slims=intestine&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&type=Experiment&files.file_type=bigBed+bed6+&lab.title=Ali+Mortazavi%2C+UCI&status=released
        return g_text
    else:
        # Should generate a HTML page for requests other than those supported
        data_policy = ('<br /><a href="http://encodeproject.org/ENCODE/terms.html">'
                       'ENCODE data use policy</p>')
        return generate_html(context, request) + data_policy
def generate_batch_hubs1(context, request):
    '''search for the input params and return the trackhub'''
    global PROFILE_START_TIME
    PROFILE_START_TIME = time.time()

    results = {}
    (page,suffix,cmd) = urlpage(request.url)
    log.debug('Requesting %s.%s#%s' % (page,suffix,cmd))
    if (suffix == 'txt' and page == 'trackDb') or (suffix == 'json' and page in ['trackDb','ihec','vis_blob']):
        return generate_batch_trackDb1(request)
        
    elif page == 'hub' and suffix == 'txt':
        terms = request.matchdict['search_params'].replace(',,', '&')
        pairs = terms.split('&')
        label = "search:"
        for pair in sorted(pairs):
            (var, val) = pair.split('=')
            if var not in ["type", "assembly", "status", "limit"]:
                label += " %s" % val.replace('+', ' ')
        return NEWLINE.join(get_hub(label, request.url))
    elif page == 'genomes' and suffix == 'txt':
        search_params = request.matchdict['search_params']
        if search_params.find('bed6+') > -1:
            search_params = search_params.replace('bed6+,,','bed6%2B,,')
        log.debug('search_params: %s' % (search_params))
        #param_list = parse_qs(request.matchdict['search_params'].replace(',,', '&'))
        param_list = parse_qs(search_params.replace(',,', '&'))
        log.debug('parse_qs: %s' % (param_list))
        view = 'search'
        if 'region' in param_list:
            view = 'variant-search'
        path = '/%s/?%s' % (view, urlencode(param_list, True))
        log.debug('Path in hunt for assembly %s' % (path))
        results = request.embed(path, as_user=True)
        # log.debug("generate_batch(genomes) len(results) = %d   %.3f secs" %
        #           (len(results),(time.time() - PROFILE_START_TIME)))
        g_text = ''
        if 'assembly' in param_list:
            g_text = get_genomes_txt(param_list.get('assembly'))
        else:
            for facet in results['facets']:
                if facet['field'] == 'assembly':
                    assemblies = []
                    for term in facet['terms']:
                        if term['doc_count'] != 0:
                            assemblies.append(term['key'])
                    if len(assemblies) > 0:
                        g_text = get_genomes_txt(assemblies)
            if g_text == '':
                log.debug('Requesting %s.%s#%s NO ASSEMBLY !!!' % (page,suffix,cmd))
                g_text = json.dumps(results,indent=4)
                assemblies = [result['assemblies'] for result in results['@graph']]
                assembly_set = set(assemblies)
                assemblies = list(assembly_set)
                log.debug('Found %d ASSEMBLY !!!' % len(assemblies))
#/search/?type=Experiment&lab.title=Ali+Mortazavi%2C+UCI&assay_title=microRNA+counts&status=released&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&files.file_type=bigBed+bed6%2B&organ_slims=intestine
#/search/?type=Experimentlab.title=Ali+Mortazavi%2C+UCI&&assay_title=microRNA+counts&status=released&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&files.file_type=bigBed+bed6+organ_slims=intestine&
#/search/?assay_title=microRNA+counts&organ_slims=intestine&replicates.library.biosample.donor.organism.scientific_name=Mus+musculus&type=Experiment&files.file_type=bigBed+bed6+&lab.title=Ali+Mortazavi%2C+UCI&status=released
        return g_text
    else:
        # Should generate a HTML page for requests other than those supported
        data_policy = ('<br /><a href="http://encodeproject.org/ENCODE/terms.html">'
                       'ENCODE data use policy</p>')
        return generate_html(context, request) + data_policy

def respond_with_text(request, text, content_mime):
    '''Resonse that can handle range requests.'''
    # UCSC broke trackhubs and now we must handle byterange requests on these CGI files
    response = request.response
    response.content_type = content_mime
    response.charset = 'UTF-8'
    response.body = bytes_(text, 'utf-8')
    response.accept_ranges = "bytes"
    response.last_modified = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    if 'Range' in request.headers:
        range_request = True
        range = request.headers['Range']
        if range.startswith('bytes'):
            range = range.split('=')[1]
        range = range.split('-')
        # One final present... byterange '0-' with no end in sight
        if range[1] == '':
            range[1] = len(response.body) - 1
        response.content_range = 'bytes %d-%d/%d' % (int(range[0]),int(range[1]),len(response.body))
        response.app_iter = request.response.app_iter_range(int(range[0]),int(range[1]) + 1)
        response.status_code = 206
    return response

@view_config(name='hub', context=Item, request_method='GET', permission='view')
def hub(context, request):
    ''' Creates trackhub on fly for a given experiment '''
    global PROFILE_START_TIME
    PROFILE_START_TIME = time.time()

    embedded = request.embed(request.resource_path(context))
    (page,suffix,cmd) = urlpage(request.url)
    content_mime = 'text/plain'
    if page == 'hub' and suffix == 'txt':
        typeof = embedded.get("assay_title")
        if typeof is None:
            typeof = embedded["@id"].split('/')[1]

        label = "%s %s" % (typeof, embedded['accession'])
        name = sanitize_name(label)
        text = NEWLINE.join(get_hub(label, request.url, name))
    elif page == 'genomes' and suffix == 'txt':
        assemblies = ''
        if 'assembly' in embedded:
            assemblies = embedded['assembly']

        text = get_genomes_txt(assemblies)

    elif (suffix == 'txt' and page == 'trackDb') or (suffix == 'json' and page in ['trackDb','ihec','vis_blob']):
        url_ret = (request.url).split('@@hub')
        url_end = url_ret[1][1:]            
        text = generate_trackDb(request, embedded, url_end.split('/')[0])
    else:
        data_policy = ('<br /><a href="https://www.lungepigenome.org/policy">'
                       'Lung Epigenome data use policy</p>')
        text = generate_html(context, request) + data_policy
        content_mime = 'text/html'

    return respond_with_text(request, text, content_mime)


@view_config(route_name='batch_hub')
@view_config(route_name='batch_hub:trackdb')
def batch_hub(context, request):
    ''' View for batch track hubs '''

    text = generate_batch_hubs(context, request)
    return respond_with_text(request, text, 'text/plain')

@view_config(route_name='browser_hub')
@view_config(route_name='browser_hub:trackdb')
def browser_hub(context, request):
    ''' View for batch track hubs '''

    text = generate_batch_hubs1(context, request)
    return respond_with_text(request, text, 'text/plain')
