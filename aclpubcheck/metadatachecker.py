import argparse
import collections
import itertools
import os
import os.path
import regex as re
import unicodedata
import textwrap

import pandas as pd
import pdfplumber
import unidecode

import googletools


def _clean_str(value):
    if pd.isna(value):
        return ''
    # uncurl all quotes
    value = re.sub(r'[\u2018\u2019]', "'", value)
    value = re.sub(r'[\u201C\u201D]', '"', value)
    # use simple dashes
    value = re.sub(r'[\u2013\u2014]', "-", value)
    # not exactly sure why, but this has to be done iteratively
    old_value = None
    value = value.strip()
    while old_value != value:
        old_value = value
        # strip space before accent; PDF seems to introduce these
        value = re.sub(r'\p{Zs}+(\p{Mn})', r'\1', value)
        # combine accents with characters
        value = unicodedata.normalize('NFKC', value)
    return value


def yield_author_problems(names, text):
    # check for author names in the expected order, allowing for
    # punctuation, affiliations, etc. between names
    # NOTE: only removed or re-ordered (not added) authors will be caught
    match = re.search('.*?'.join(names), text, re.DOTALL)
    if not match:

        # check if there is a match when ignoring case, punctuation, accents
        # since this is the most common type of error
        allowed_chars = r'[\p{Zs}\p{p}\p{Mn}]'
        match_ignoring_case_punct_accent = re.search(
            '.*?'.join(
                fr'{allowed_chars}*'.join(unidecode.unidecode(c) for c in p)
                for part in names for p in re.split(allowed_chars, part)),
            unidecode.unidecode(text),
            re.DOTALL | re.IGNORECASE)
        if match_ignoring_case_punct_accent:
            problem = 'AUTHOR-MISMATCH-CASE-PUNCT-ACCENT'
            # these offsets may be slightly incorrect because unidecode may
            # change the number of characters, but it should be close enough
            start, end = match_ignoring_case_punct_accent.span()
            in_text = text[start: end]
        else:
            problem = 'AUTHOR-MISMATCH'
            in_text = text
        yield problem, f"meta=\"{' '.join(names)}\"\npdf =\"{in_text}\""


def yield_title_problems(title, text):
    # ignore spaces and some LaTeX-isms
    title_chars = re.sub(r'[\s{}$^]', '', title.replace('--', '-'))
    title_regex = r'\s*'.join(re.escape(c) for c in title_chars)

    # ignore differences in case; LaTeX \sc comes out as caps in PDF
    match = re.search(title_regex, text, re.IGNORECASE)
    if not match:
        yield 'TITLE', f"meta=\"{title}\"\npdf =\"{text}\""


def yield_copyright_problems(signature, org_name, org_address):
    if not signature:
        yield "COPYRIGHT", "The signature is missing."
    elif signature == "NA":
        yield "COPYRIGHT", f'The signature "{signature}" must be accompanied ' \
                           f'by a "License to Publish" or equivalent.'
    elif len(signature) < 3 or len(signature.split()) < 2:
        yield "COPYRIGHT", f'The signature "{signature}" does not appear to ' \
                           f'be a full name.'
    if not org_name:
        yield "COPYRIGHT", "The organization name is missing."
    elif len(org_name) < 5 and org_name not in {'IBM'}:
        yield "COPYRIGHT", f'The organization name "{org_name}" does not ' \
                           f'appear to be a full name. '
    if not org_address:
        yield "COPYRIGHT", "The organization address is missing."
    elif len(org_address) < 3 or len(org_address.split()) < 2:
        org_address_simple = org_address.replace("\n", " ")
        yield "COPYRIGHT", f'The organization address "{org_address_simple}" ' \
                           f'does not appear to be a complete physical address.'


def check_metadata(
        submissions_path,
        pdfs_dir,
        spreadsheet_id,
        sheet_id,
        id_column,
        problem_column,
        post=False):

    # map submission IDs to PDF paths
    id_to_pdf = {}
    for root, _, filenames in os.walk(pdfs_dir):
        for filename in filenames:
            if filename.endswith("_Paper.pdf"):
                submission_id, _ = filename.split("_", 1)
                id_to_pdf[int(submission_id)] = os.path.join(root, filename)

    id_to_sheet_row = {}
    problems = collections.defaultdict(lambda: collections.defaultdict(list))

    df = pd.read_csv(submissions_path, keep_default_na=False)
    for index, row in df.iterrows():
        submission_id = row["Submission ID"]
        title = _clean_str(row["Title"])

        # NOTE: These were the names in the custom final submission form
        # for NAACL 2021. Names and structure may be different depending
        # on your final submission form.
        signature = _clean_str(row["copyrightSig"])
        org_name = _clean_str(row["orgName"])
        org_address = _clean_str(row["orgAddress"])

        # row in the spreadsheet is 1-based and first row is the header
        id_to_sheet_row[submission_id] = index + 2

        # open the PDF
        pdf_path = id_to_pdf[submission_id]
        pdf = pdfplumber.open(pdf_path)

        # assumes metadata can be found in the first 500 characters
        text = _clean_str(pdf.pages[0].extract_text()[:500])

        # collect all authors and their affiliations
        names = []
        for i in range(1, 25):
            for x in ['First', 'Middle', 'Last']:
                name_part = _clean_str(row[f'{i}: {x} Name'])
                if name_part:
                    names.extend(name_part.split())

        # collect all problems
        for problem_type, problem_text in itertools.chain(
                yield_author_problems(names, text),
                yield_title_problems(title, text),
                yield_copyright_problems(signature, org_name, org_address)):
            problems[submission_id][problem_type].append(problem_text)

    # print all problems, grouped by type of problem
    for submission_id in sorted(problems):
        for problem_type in sorted(problems[submission_id]):
            problem_text = '\n'.join(problems[submission_id][problem_type])
            problem_text = textwrap.indent(problem_text, '  ')
            print(f'{submission_id}:{problem_type}:\n{problem_text}\n')

    # report overall problem statistics
    print(f"{len(problems)} submissions failed:")
    problem_counts = collections.Counter(
        problem_type
        for type_texts in problems.values()
        for problem_type in type_texts.keys()
    )
    for problem_type in sorted(problem_counts.keys()):
        print(f"  {problem_counts[problem_type]} {problem_type}")

    # if requested, post problems to the Google Sheet
    if post:
        values = googletools.sheets_service().spreadsheets().values()

        # get the number of rows
        id_range = f'{sheet_id}!{id_column}2:{id_column}'
        request = values.get(spreadsheetId=spreadsheet_id, range=id_range)
        submission_ids = {int(value) for [value] in request.execute()['values']}
        if submission_ids != id_to_sheet_row.keys():
            raise ValueError(f'in Google sheet only: '
                             f'{submission_ids - id_to_sheet_row.keys()}; '
                             f'in START sheet only: '
                             f'{id_to_sheet_row.keys() - submission_ids}')
        n_rows = len(submission_ids) + 1

        sheet_row_to_problems = collections.defaultdict(list)
        for submission_id, type_texts in problems.items():
            for problem_type, texts in type_texts.items():
                problems = '\n'.join(texts)
                sheet_row_to_problems[id_to_sheet_row[submission_id]].append(
                    f'{problem_type}:\n{problems}')

        # fill in the problem column
        request = values.update(
            spreadsheetId=spreadsheet_id,
            range=f'{sheet_id}!{problem_column}2:{problem_column}',
            valueInputOption='RAW',
            body={'values': [['\n'.join(sheet_row_to_problems.get(i, []))]
                             for i in range(2, n_rows)]})
        request.execute()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--submissions', dest='submissions_path',
                        default='Submission_Information.csv')
    parser.add_argument('--pdfs', dest='pdfs_dir', default='final')
    parser.add_argument('--post', action='store_true')
    parser.add_argument('--spreadsheet-id',
                        default='1lQyGZNBEBwukf8-mgPzIH57xUX9y4o2OUCzpEvNpW9A')
    parser.add_argument('--sheet-id', default='Sheet1')
    parser.add_argument('--id-column', default='A')
    parser.add_argument('--problem-column', default='E')
    args = parser.parse_args()
    check_metadata(**vars(args))
