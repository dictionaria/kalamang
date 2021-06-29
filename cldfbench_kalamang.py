from collections import ChainMap
from itertools import chain
import pathlib
import re
import sys

from pydictionaria import sfm2cldf
from pydictionaria.sfm_lib import Database as SFM
from pydictionaria.concepts import ConceptMap

from pydictionaria.preprocess_lib import (
    marker_fallback_sense, marker_fallback_entry, merge_markers
)

import attr
from cldfbench import CLDFSpec
from segments import Profile, Tokenizer

import pylexibank


SEMANTIC_DOMAINS = (
    'aquatic life',
    'arrange, hold, transfer',
    'birds',
    'bodily states, colours, dimensions, quantity',
    'body',
    'canoe parts',
    'culture and communication',
    'earth',
    'food, cooking, fire',
    'relational nouns',
    'house parts',
    'human artifacts',
    'impact, cut, break',
    'insects and small animals',
    'kin',
    'location, direction, time',
    'medicines',
    'motion',
    'other animals',
    'placenames',
    'plants',
    'sounds, smells, sensations, vision',
    'state',
    'values and emotions',
    'work',
)

PENDING_SIGNIFIERS = {'tentative', 'pending'}

VAR_MARKERS = {
    'lx',
    'lx_Kar',
    'hm',
    'ph_Kar',
    'va',
    'vt',
    'lf',
    'vet',
    'mn',
    'cet',
    'se',
    'co_Dut',
    'dt',
}


class DropTracker:

    def __init__(self, crossref_markers):
        self._dropped_ids = set()
        self._crossref_markers = crossref_markers

    def dropper_func(self, fun):
        def catch_dropped(entry):
            new_entry = fun(entry)
            if new_entry is False:
                self._dropped_ids.add(
                    '{}{}'.format(entry.get('lx', ''), entry.get('hm', '')))
            return new_entry
        return catch_dropped

    def _drop_crossrefs(self, mvpair):
        m, v = mvpair
        if m in self._crossref_markers:
            new_v = ' ; '.join(
                id_.strip()
                for id_ in v.split(';')
                if id_.strip() not in self._dropped_ids)
            return m, new_v
        else:
            return m, v

    def drop_crossrefs(self, entry):
        return entry.__class__(map(self._drop_crossrefs, entry))


def drop_mly(entry):
    if 'MLY' in entry.get('ps', ''):
        return False
    else:
        return entry


def drop_variant(entry):
    if {m for m, _ in entry} <= VAR_MARKERS:
        return False
    else:
        return entry


def is_pending(sense):
    return sense.get('z6', '').lower() in PENDING_SIGNIFIERS


def drop_pending(entry):
    prefix = entry.__class__()
    senses = []
    for marker, value in entry:
        if marker == 'sn':
            senses.append(entry.__class__())
            senses[-1].append((marker, value))
        elif senses:
            senses[-1].append((marker, value))
        else:
            prefix.append((marker, value))

    if senses:
        senses_left = [s for s in senses if not is_pending(s)]
        if senses_left:
            return entry.__class__(chain(prefix, *senses_left))
        else:
            return False
    elif is_pending(prefix):
        return False
    else:
        return entry


def parse_semantic_domains(value):
    rest = value.strip().lower()
    domains = []
    while rest:
        for dom in SEMANTIC_DOMAINS:
            if rest.startswith(dom):
                domains.append(dom)
                rest = rest[len(dom):].strip()
                break
        else:
            print('unkown semantic domain:', rest, file=sys.stderr)
            domains.append(rest)
            break
    return ' ; '.join(domains)


def merged_va(marker_dict):
    va = marker_dict.get('va') or ''
    vet = marker_dict.get('vet') or ''
    if va and vet:
        return '{}: {}'.format(vet, va)
    else:
        return va


def merge_mn(entry):
    mns = []
    for marker, value in entry:
        if marker == 'mn':
            mns.append(value)
        elif mns:
            yield 'mn', ' ; '.join(mns)
            yield marker, value
            mns = []
        else:
            yield marker, value
    if mns:
        yield 'mn', ' ; '.join(mns)


def mn_to_lv(entry):
    prev = None
    for marker, value in entry:
        if marker == 'mn' and prev == 'lf':
            yield 'lv', value
        else:
            yield marker, value
        prev = marker if value else None


def filter_sp_var(entry):
    if not entry.get('vet'):
        return entry
    new_entry = entry.__class__()
    prev_va = None

    for marker, value in entry:
        if marker == 'vet' and value == 'sp. var. of':
            prev_va = None
            continue

        if prev_va:
            new_entry.append(('va', prev_va))
            prev_va = None

        if marker == 'va':
            prev_va = value
        else:
            new_entry.append((marker, value))

    return new_entry


def merged_pc(marker_dict):
    eng = marker_dict.get('pc_Eng')
    kar = marker_dict.get('pc_Kar')
    if eng and kar:
        return '{} – {}'.format(eng, kar)
    else:
        return eng


def reorganize(sfm):
    dt = DropTracker({'lv', 'mn'})

    sfm.visit(dt.dropper_func(drop_mly))
    sfm.visit(dt.dropper_func(drop_variant))
    sfm.visit(dt.dropper_func(drop_pending))

    sfm.visit(dt.drop_crossrefs)

    return sfm


def preprocess(entry):
    entry = entry.__class__(
        (m, v)
        for m, v in entry
        if m != 'pc_Mal')
    entry = entry.__class__(
        (m, re.sub(r'\s*\&lt;(\s*)', r'\1', v) if m == 'esl' else v)
        for m, v in entry)

    entry = merge_markers(
        entry, ['pc_Eng', 'pc_Kar'], 'pc_Eng', format_fn=merged_pc)
    entry = marker_fallback_sense(entry, 'de', 'ge')
    entry = marker_fallback_sense(entry, 'd_Mal', 'g_Mal')

    entry = filter_sp_var(entry)

    if entry.get('mn'):
        entry = entry.__class__(merge_mn(entry))
        entry = entry.__class__(mn_to_lv(entry))

    if entry.get('sd'):
        entry = entry.__class__(
            (m, parse_semantic_domains(v) if m == 'sd' else v)
            for m, v in entry)

    return entry

def authors_string(authors):
    def is_primary(a):
        return not isinstance(a, dict) or a.get('primary', True)

    primary = ' and '.join(
        a['name'] if isinstance(a, dict) else a
        for a in authors
        if is_primary(a))
    secondary = ' and '.join(
        a['name']
        for a in authors
        if not is_primary(a))
    if primary and secondary:
        return '{} with {}'.format(primary, secondary)
    else:
        return primary or secondary


@attr.s
class CustomLexeme(pylexibank.Lexeme):
    Sense_ID = attr.ib(default=None)


class Dataset(pylexibank.Dataset):
    dir = pathlib.Path(__file__).parent
    id = "kalamang"
    lexeme_class = CustomLexeme

    def cldf_specs(self):  # A dataset must declare all CLDF sets it creates.
        return {
            None: super().cldf_specs(),
            'dictionary': CLDFSpec(
                dir=self.cldf_dir,
                module='Dictionary',
                metadata_fname='cldf-metadata.json'),
        }

    def cmd_download(self, args):
        """
        Download files to the raw/ directory. You can use helpers methods of `self.raw_dir`, e.g.

        >>> self.raw_dir.download(url, fname)
        """
        pass

    def cmd_makecldf(self, args):
        """
        Convert the raw data to a CLDF dataset.

        >>> args.writer.objects['LanguageTable'].append(...)
        """
        with self.cldf_writer(args, cldf_spec='dictionary') as writer:

            # read data

            md = self.etc_dir.read_json('md.json')
            properties = md.get('properties') or {}
            language_name = md['language']['name']
            isocode = md['language']['isocode']
            language_id = md['language']['isocode']
            glottocode = md['language']['glottocode']

            marker_map = ChainMap(
                properties.get('marker_map') or {},
                sfm2cldf.DEFAULT_MARKER_MAP)
            entry_sep = properties.get('entry_sep') or sfm2cldf.DEFAULT_ENTRY_SEP
            sfm = SFM(
                self.raw_dir / 'db.sfm',
                marker_map=marker_map,
                entry_sep=entry_sep)

            examples = sfm2cldf.load_examples(self.raw_dir / 'examples.sfm')

            if (self.etc_dir / 'cdstar.json').exists():
                media_catalog = self.etc_dir.read_json('cdstar.json')
            else:
                media_catalog = {}

            concept_map = ConceptMap.from_csv(self.etc_dir / 'concepts.csv')

            # preprocessing

            sfm = reorganize(sfm)
            sfm.visit(preprocess)

            # processing

            reprs = ['Phonemic']
            with open(self.dir / 'cldf.log', 'w', encoding='utf-8') as log_file:
                log_name = '%s.cldf' % language_id
                cldf_log = sfm2cldf.make_log(log_name, log_file)

                entries, senses, examples, media = sfm2cldf.process_dataset(
                    self.id, language_id, properties,
                    sfm, examples, media_catalog=media_catalog,
                    glosses_path=self.raw_dir / 'glosses.flextext',
                    examples_log_path=self.dir / 'examples.log',
                    glosses_log_path=self.dir / 'glosses.log',
                    cldf_log=cldf_log)

                # good place for some post-processing

                senses = [
                    concept_map.add_concepticon_id(sense)
                    for sense in senses]

                # cldf schema

                sfm2cldf.make_cldf_schema(
                    writer.cldf, properties,
                    entries, senses, examples, media)

                sfm2cldf.attach_column_titles(writer.cldf, properties)

                print(file=log_file)

                entries = sfm2cldf.ensure_required_columns(
                    writer.cldf, 'EntryTable', entries, cldf_log)
                senses = sfm2cldf.ensure_required_columns(
                    writer.cldf, 'SenseTable', senses, cldf_log)
                examples = sfm2cldf.ensure_required_columns(
                    writer.cldf, 'ExampleTable', examples, cldf_log)
                media = sfm2cldf.ensure_required_columns(
                    writer.cldf, 'media.csv', media, cldf_log)

                entries = sfm2cldf.remove_senseless_entries(
                    senses, entries, cldf_log)

            # output

            writer.cldf.properties['dc:creator'] = authors_string(
                md.get('authors') or ())

            writer.objects['EntryTable'] = entries
            writer.objects['SenseTable'] = senses
            writer.objects['ExampleTable'] = examples
            writer.objects['media.csv'] = media

            entry_table = writer.cldf['EntryTable']
            sense_table = writer.cldf['SenseTable']
            media_table = writer.cldf['MediaTable']

        with self.cldf_writer(args, clean=False) as writer:
            # TODO integrate into pydictionaria

            writer.cldf.add_component(entry_table)
            writer.cldf.add_component(sense_table)
            writer.cldf.add_component(media_table)
            writer.cldf.add_foreign_key(
                'FormTable', 'Sense_ID', 'SenseTable', 'ID')

            cids = sorted({
                (int(s['Concepticon_ID']), s['Description'])
                for s in senses
                if s.get('Concepticon_ID')
            })
            for cid, english in cids:
                if self.concepticon.cached_glosses.get(cid):
                    writer.add_concept(
                        ID=cid,
                        Name=english,
                        Concepticon_ID=cid,
                        Concepticon_Gloss=self.concepticon.cached_glosses[cid])

            ortho_profile = Profile.from_file(fname=self.etc_dir / 'orthography.tsv')
            tokenizer = Tokenizer(profile=ortho_profile)

            entries_by_id = {e['ID']: e for e in entries}
            for sense in senses:
                if sense.get('Concepticon_ID'):
                    form = entries_by_id[sense['Entry_ID']]['Headword'].replace(' ', '_')
                    lexeme = writer.add_form_with_segments(
                        ID='{}-{}'.format(sense['ID'], sense['Concepticon_ID']),
                        Parameter_ID=sense['Concepticon_ID'],
                        Sense_ID=sense['ID'],
                        Form=form,
                        Value=entries_by_id[sense['Entry_ID']]['Headword'],
                        Language_ID=language_id,
                        Graphemes=tokenizer(form),
                        Segments=tokenizer(form, column='IPA').split())

            writer.add_language(
                ID=language_id,
                Name=language_name,
                ISO639P3code=isocode,
                Glottocode=glottocode)
            #Representations=reprs
