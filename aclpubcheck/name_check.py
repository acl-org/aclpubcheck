import os
import rebiber
from pylatexenc.latex2text import LatexNodes2Text
from pybtex.database import parse_file
import contextlib
from unidecode import unidecode
import re


class PDFNameCheck:

    def __init__(self):
        # Generate and update the bib list from various conferences
        filepath = os.path.abspath(rebiber.__file__).replace("__init__.py", "")
        bib_list_path = os.path.join(filepath, "bib_list.txt")
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f):
            self.bib_db = rebiber.construct_bib_db(
                bib_list_path, start_dir=filepath)

    def execute_curl(self, config):
        # The curl string to convert the PDF to bib.
        # I have used scholarcy API here.
        # See the link here: https://ref.scholarcy.com/api/
        # I used the POST curl for download

        self.filename = config.file.split('.')[0]
        temp_name = self.filename.split('/')[-1]
        os.makedirs('temp', exist_ok=True)

        curl_string = 'curl --silent -X \'POST\'' \
            ' \'https://ref.scholarcy.com/api/references/download\'' \
            ' -H \'accept: application/json\'' \
            ' -H \'Authorization: Bearer \'' \
            ' -H \'Content-Type: multipart/form-data\'' \
            f' -F \'file=@{config.file};type=application/pdf\'' \
            ' -F \'document_type=full_paper\'' \
            f' -F \'references={config.ref_string}\'' \
            f' -F \'reference_style={config.mode}\'' \
            ' -F \'reference_format=bibtex\'' \
            ' -F \'parser=v2\'' \
            f' -F \'engine=v1\' > temp/before-rebiber-{temp_name}.bib'

        # Execute that curl string
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f):
            os.system(curl_string)

    def apply_rebiber(self):
        # The curl string generates a bib file called 'before rebiber'
        # Pass it to rebiber
        temp_name = self.filename.split('/')[-1]
        all_bib_entries = rebiber.load_bib_file(f'temp/before-rebiber-{temp_name}.bib')

        # Update the bib file using rebiber and call it 'after rebiber'
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f):
            rebiber.normalize_bib(
                self.bib_db, all_bib_entries, f'temp/after-rebiber-{temp_name}.bib')

    def extract_names(self):
        # Parse both bib files
        temp_name = self.filename.split('/')[-1]
        old_bib_data = parse_file(f'temp/before-rebiber-{temp_name}.bib')
        new_bib_data = parse_file(f'temp/after-rebiber-{temp_name}.bib')

        name_list = {}

        paper_keys = list(new_bib_data.entries.keys())

        # Here old means before updating
        # Here new means after updating
        # We will collect the author names before and after the bib updates
        for paper in paper_keys:
            old_paper_authors = []
            new_paper_authors = []
            if 'author' in old_bib_data.entries[paper].persons:
                old_key = old_bib_data.entries[paper].persons['author']
                new_key = new_bib_data.entries[paper].persons['author']
                old_length = len(old_bib_data.entries[paper].persons['author'])
                new_length = len(new_bib_data.entries[paper].persons['author'])
                additional = False
                for i in range(new_length):
                    if i < old_length:
                        # Bugfix: Sometimes names with dots are being parsed as full names
                        if ' '.join(old_key[i].bibtex_first_names).replace('.', '') == \
                                ' '.join(new_key[i].bibtex_first_names + new_key[i].last_names):
                            old_key[i] = new_key[i]

                        old_name = old_key[i].bibtex_first_names + \
                            old_key[i].last_names
                        new_name = new_key[i].bibtex_first_names + \
                            new_key[i].last_names

                        # Bugfix: Sometimes there are two names in a name
                        if old_key[i].last_names == new_key[i].bibtex_first_names + new_key[i].last_names:
                            additional = i
                        new_name = [LatexNodes2Text().latex_to_text(name)
                                    for name in new_name]
                        old_paper_authors.append(old_name)
                        new_paper_authors.append(new_name)
                    else:
                        # Sometimes authors tend to cite only n authors
                        new_name = new_key[i].first_names + \
                            new_key[i].last_names
                        new_paper_authors.append(new_name)

                # Bugfix: Sometimes there are two names in a name
                if additional:
                    old_paper_authors[additional] = new_paper_authors[additional]
                    if additional+1 < len(new_paper_authors):
                        old_paper_authors.insert(
                            additional+1, new_paper_authors[additional+1])
                name_list[paper] = {}
                name_list[paper]['old'] = old_paper_authors
                name_list[paper]['new'] = new_paper_authors
                name_list[paper]['title'] = LatexNodes2Text().latex_to_text(
                    new_bib_data.entries[paper].fields['title'])

                if 'url' in new_bib_data.entries[paper].fields:
                    name_list[paper]['url'] = new_bib_data.entries[paper].fields['url']

        return name_list

    def if_equal(self, string_a, string_b):
        '''
        Do a basic cleanup to tell whether the names are same or not
        '''
        # remove spaces and lowercase
        string_a = ('').join(string_a).lower()
        string_b = ('').join(string_b).lower()
        # remove punctuations
        string_a = re.sub(r'\W+', '', string_a)
        string_b = re.sub(r'\W+', '', string_b)
        # remove accents
        string_a = unidecode(string_a)
        string_b = unidecode(string_b)
        return string_a == string_b

    def compare_changes(self, name_list, config):

        warnings = []
        error_count = 1

        for paper in name_list:
            output_strings = []
            old = name_list[paper]['old']
            new = name_list[paper]['new']
            title = name_list[paper]['title']
            if 'url' in name_list[paper]:
                url = name_list[paper]['url']
            else:
                url = ''
            old_length = len(old)
            new_length = len(new)
            # Citation error: Cites do not contain every author
            if old_length != new_length:
                error_count += 1
                output_strings.append(
                    f'Number of authors in the title `{title}` is incorrect.')
                output_strings.append(
                    f'The number of authors should be {new_length}, not {old_length}.')
                if url:
                    output_strings.append(
                        f'Please correct the citation by visiting this url: {url}')
            if old_length == new_length:
                already_warned = False
                for i in range(old_length):
                    # If you wanna check the full name
                    if config.whole_name:
                        # Check if names are sanme
                        if self.if_equal(old[i], new[i]) is False:
                            # If not, check if we have warned them already
                            if already_warned is False:
                                error_count += 1
                                output_strings.append(
                                    f'Your citation for `{title}` might have incorrect author names.')
                                if url:
                                    output_strings.append(
                                        f'Please correct the citation by visiting this url: {url}')
                            already_warned = True
                            # If you wanna show the names
                            if config.show_names:
                                old_name = ' '.join(name_list[paper]['old'][i])
                                new_name = ' '.join(name_list[paper]['new'][i])
                                output_strings.append(
                                    f'The name should be {new_name} not {old_name}.')
                    else:
                        # If you wanna check only the first name
                        if config.first_name:
                            if config.initials and \
                                    (re.search(r'^[A-Z]\.', old[i][0]) or re.search(r'^[A-Z]\.', new[i][0])):
                                old_first_name = re.sub(
                                    r'[^A-Z]', '', old[i][0])
                                new_first_name = re.sub(
                                    r'[^A-Z]', '', new[i][0])
                            else:
                                old_first_name = old[i][0]
                                new_first_name = new[i][0]
                            if self.if_equal(old_first_name, new_first_name) is False:
                                if already_warned is False:
                                    error_count += 1
                                    output_strings.append(
                                        f'Your citation for `{title}` might have incorrect author names.')
                                    if url:
                                        output_strings.append(
                                            f'Please correct the citation by visiting this url: {url}')
                                already_warned = True
                                if config.show_names:
                                    old_name = ' '.join(
                                        name_list[paper]['old'][i])
                                    new_name = ' '.join(
                                        name_list[paper]['new'][i])
                                    first_author_id = i
                                    output_strings.append(
                                        f'The author #{first_author_id} name should be {new_name} not {old_name}.')
                        # If you wanna check only the last name
                        if config.last_name:
                            if self.if_equal(old[i][-1], new[i][-1]) is False:
                                if already_warned is False:
                                    error_count += 1
                                    output_strings.append(
                                        f'Your citation for `{title}` might have incorrect author names.')
                                    if url:
                                        output_strings.append(
                                            f'Please correct the citation by visiting this url: {url}')
                                already_warned = True
                                last_author_id = i
                                if config.show_names and already_warned and (first_author_id != last_author_id):
                                    old_name = ' '.join(
                                        name_list[paper]['old'][i])
                                    new_name = ' '.join(
                                        name_list[paper]['new'][i])
                                    output_strings.append(
                                        f'The author #{last_author_id} name should be {new_name} not {old_name}.')

            if len(output_strings) > 0:
                warning = ' '.join(output_strings)
                warnings.append(' '.join(output_strings))

        return warnings

    def execute(self, config):
        self.execute_curl(config)
        self.apply_rebiber()
        name_list = self.extract_names()
        output_strings = self.compare_changes(name_list, config)
        return output_strings
