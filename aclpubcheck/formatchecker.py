'''
python3 formatchecker.py [-h] [--paper_type {long,short,other}] file_or_dir [file_or_dir ...]
'''

import argparse
import json
from enum import Enum
from collections import defaultdict
from os import walk
from os.path import isfile, join
import pdfplumber
from tqdm import tqdm
from termcolor import colored
import os
import numpy as np
import traceback
from name_check import PDFNameCheck
from argparse import Namespace



class Error(Enum):
    SIZE = "Size"
    PARSING = "Parsing"
    MARGIN = "Margin"
    SPELLING = "Spelling"
    FONT = "Font"
    PAGELIMIT = "Page Limit"


class Warn(Enum):
    BIB = "Bibliography"


class Page(Enum):
    # 595 pixels (72ppi) = 21cm
    WIDTH = 595
    # 842 pixels (72ppi) = 29.7cm
    HEIGHT = 842


class Margin(Enum):
    TOP = "top"
    BOTTOM = "bottom"
    RIGHT = "right"
    LEFT = "left"


class Formatter(object):

    def __init__(self):
        # TODO: these should be constants
        self.right_offset = 4.5
        self.left_offset = 2
        self.top_offset = 1

        # this is used to check if an area out of the margin is a "false positive",
        # i.e., an area containing invisible symbols. When a candidate area out of
        # the margin is proposed, this is cropped and if all pixels are equal to
        # the background, this is skipped
        self.background_color = 255
        self.pdf_namecheck = PDFNameCheck()


    def format_check(self, submission, paper_type, output_dir = ".", print_only_errors = False):
        """
        Return True if the paper is correct, False otherwise.
        """
        print(f"Checking {submission}")

        # TOOD: make this less of a hack
        self.number = submission.split("/")[-1].split("_")[0].replace(".pdf", "")
        self.pdf = pdfplumber.open(submission)
        self.logs = defaultdict(list)  # reset log before calling the format-checking functions
        self.page_errors = set()
        self.pdfpath = submission

        # TODO: A few papers take hours to check. Consider using a timeout
        self.check_page_size()
        self.check_page_margin(output_dir)
        self.check_page_num(paper_type)
        self.check_font()
        self.check_references()

        # TODO: put json dump back on
        output_file = "errors-{0}.json".format(self.number)
        # string conversion for json dump
        logs_json = {}
        for k, v in self.logs.items():
            logs_json[str(k)] = v

        if self.logs:
            print(f"Errors. Check {output_file} for details.")

        errors, warnings = 0, 0
        if self.logs.items():
            for e, ms in self.logs.items():
                for m in ms:
                    if isinstance(e, Error) and e != Error.PARSING:
                        print(colored("Error ({0}):".format(e.value), "red")+" "+m)
                        errors += 1
                    elif e == Error.PARSING:
                        print(colored("Parsing Error:".format(e.value), "yellow")+" "+m)
                    else:
                        print(colored("Warning ({0}):".format(e.value), "yellow")+" "+m)
                        warnings += 1


            # English nominal morphology
            error_text = "errors"
            if errors == 1:
                error_text = "error"
            warning_text = "warnings"
            if warnings == 1:
                warning_text = "warning"

            if print_only_errors == False or errors >= 1:
                json.dump(logs_json, open(os.path.join(output_dir,output_file), 'w'))  # always write a log file even if it is empty

            # display to user
            print()
            print("We detected {0} {1} and {2} {3} in your paper.".format(*(errors, error_text, warnings, warning_text)))
            print("In general, it is required that you fix errors for your paper to be published. Fixing warnings is optional, but recommended.")
            print("Important: Some of the margin errors may be spurious. The library detects the location of images, but not whether they have a white background that blends in.")
            print("Important: Some of the warnings generated for citations may be spurious and inaccurate, due to parsing and indexing errors.")
            print("We encourage you to double check the citations and update them depending on the latest source. If you believe that your citation is updated and correct, then please ignore those warnings.")

            if errors >= 1:
                return logs_json
            else:
                return {}


        else:
            if print_only_errors == False:
                json.dump(logs_json, open(os.path.join(output_dir,output_file), 'w'))

            print(colored("All Clear!", "green"))
            return logs_json



    def check_page_size(self):
        """ Checks the paper size (A4) of each pages in the submission. """

        pages = []
        for i, page in enumerate(self.pdf.pages):

            if (round(page.width), round(page.height)) != (Page.WIDTH.value, Page.HEIGHT.value):
                pages.append(i+1)
        for page in pages:
            error = "Page #{} is not A4.".format(page)
            self.logs[Error.SIZE] += [error]
        self.page_errors.update(pages)


    def check_page_margin(self, output_dir):
        """ Checks if any text or figure is in the margin of pages. """

        pages_image = defaultdict(list)
        pages_text = defaultdict(list)
        perror = []
        for i, p in enumerate(self.pdf.pages):
            if i+1 in self.page_errors:
                continue
            try:
                # Parse images
                # 57 pixels (72ppi) = 2cm; 71 pixels (72ppi) = 2.5cm.
                for image in p.images:
                    violation = None
                    if int(image["bottom"]) > 0 and float(image["top"]) < (57-self.top_offset):
                        violation = Margin.TOP
                    elif int(image["x1"]) > 0 and float(image["x0"]) < (71-self.left_offset):
                        violation = Margin.LEFT
                    elif int(image["x0"]) < Page.WIDTH.value and Page.WIDTH.value-float(image["x1"]) < (71-self.right_offset):
                        violation = Margin.RIGHT

                    if violation:
                        # if the image is completely white, it can be skipped

                        # get the actual visible area
                        x0 = max(0, int(image["x0"]))
                        # check the intersection with the right margin to handle larger images
                        # but with an "overflow" that is of the same color of the backgrond
                        if violation == Margin.RIGHT:
                            x0 = max(x0, Page.WIDTH.value - 71 + self.right_offset)

                        x1 = min(int(image["x1"]), Page.WIDTH.value)
                        if violation == Margin.LEFT:
                            x1 = min(x1, 71 - self.right_offset)

                        y0 = max(0, int(image["top"]))

                        y1 = min(int(image["bottom"]), Page.HEIGHT.value)
                        if violation == Margin.TOP:
                            y1 = min(y1, 57-self.top_offset)

                        bbox = (x0, y0, x1, y1)

                        # avoid problems in cropping images too small
                        if x1 - x0 <= 1 or y1 - y0 <= 1:
                            continue

                        # cropping the image to check if it is white
                        # i.e., all pixels set to 255
                        cropped_page = p.crop(bbox)
                        try:
                          image_obj = cropped_page.to_image(resolution=100)
                          if np.mean(image_obj.original) != self.background_color:
                            pages_image[i] += [(image, violation)]
                        # if there are some errors during cropping, it is better to check
                        except:
                          pages_image[i] += [(image, violation)]

                # Parse texts
                for j, word in enumerate(p.extract_words(extra_attrs=["non_stroking_color", "stroking_color"])):
                    violation = None

                    #if word["non_stroking_color"] == (0, 0, 0) or word["non_stroking_color"] == 0 or word["stroking_color"] == 0:
                    if word["non_stroking_color"] == (0, 0, 0) or word["non_stroking_color"] == [0]:
                        continue

                    if word["non_stroking_color"] is None and word["stroking_color"] is None:
                        continue

                    if int(word["bottom"]) > 0 and float(word["top"]) < (57-self.top_offset):
                        violation = Margin.TOP
                    elif int(word["x1"]) > 0 and float(word["x0"]) < (71-self.left_offset):
                        violation = Margin.LEFT
                    elif int(word["x0"]) < Page.WIDTH.value and Page.WIDTH.value-float(word["x1"]) < (71-self.right_offset):
                        violation = Margin.RIGHT

                    if violation and int(word["x0"]) < Page.WIDTH.value and int(word["x1"]) >= 0 and int(word["bottom"]) >= 0:
                        # if the area image is completely white, it can be skipped
                        # get the actual visible area
                        x0 = max(0, int(word["x0"]))
                        # check the intersection with the right margin to handle larger images
                        # but with an "overflow" that is of the same color of the backgrond
                        if violation == Margin.RIGHT:
                            x0 = max(x0, Page.WIDTH.value - 71 + self.right_offset)

                        x1 = min(int(word["x1"]), Page.WIDTH.value)
                        if violation == Margin.LEFT:
                            x1 = min(x1, 71 - self.right_offset)

                        y0 = max(0, int(word["top"]))

                        y1 = min(int(word["bottom"]), Page.HEIGHT.value)
                        if violation == Margin.TOP:
                            y1 = min(y1, 57-self.top_offset)

                        bbox = (x0, y0, x1, y1)

                        # avoid problems in cropping images too small
                        if x1 - x0 <= 1 or y1 - y0 <= 1:
                            continue

                        # cropping the image to check if it is white
                        # i.e., all pixels set to 255
                        try:
                            cropped_page = p.crop(bbox)
                            image_obj = cropped_page.to_image(resolution=100)
                            if np.mean(image_obj.original) != self.background_color:
                                print("Found text violation:\t" + str(violation) + "\t" + str(word))
                                pages_text[i] += [(word, violation)]
                        except:
                          # if there are some errors during cropping, it is better to check
                          pages_image[i] += [(word, violation)]

            except:
                traceback.print_exc()
                perror.append(i+1)

        if perror:
            self.page_errors.update(perror)
            self.logs[Error.PARSING] = ["Error occurs when parsing page {}.".format(perror)]

        if pages_text or pages_image:
            pages = sorted(set(pages_text.keys()).union(set((pages_image.keys()))))
            for page in pages:
                im = self.pdf.pages[page].to_image(resolution=150)
                for (word, violation) in pages_text[page]:

                    bbox = None
                    if violation == Margin.RIGHT:
                        self.logs[Error.MARGIN] += ["Text on page {} bleeds into the right margin.".format(page+1)]
                        bbox = (Page.WIDTH.value-80, int(word["top"]-20), Page.WIDTH.value-20, int(word["bottom"]+20))
                        im.draw_rect(bbox, fill=None, stroke="red", stroke_width=5)
                    elif violation == Margin.LEFT:
                        self.logs[Error.MARGIN] += ["Text on page {} bleeds into the left margin.".format(page+1)]
                        bbox = (20, int(word["top"]-20), 80, int(word["bottom"]+20))
                        im.draw_rect(bbox, fill=None, stroke="red", stroke_width=5)
                    elif violation == Margin.TOP:
                        self.logs[Error.MARGIN] += ["Text on page {} bleeds into the top margin.".format(page+1)]
                        bbox = (20, int(word["top"]-20), 80, int(word["bottom"]+20))
                        im.draw_rect(bbox, fill=None, stroke="red", stroke_width=5)
                    else:
                        # TODO: add bottom margin violations
                        pass


                for (image, violation) in pages_image[page]:

                    self.logs[Error.MARGIN] += ["An image on page {} bleeds into the margin.".format(page+1)]
                    bbox = (image["x0"], image["top"], image["x1"], image["bottom"])
                    im.draw_rect(bbox, fill=None, stroke="red", stroke_width=5)

                png_file_name = "errors-{0}-page-{1}.png".format(*(self.number, page+1))
                im.save(os.path.join(output_dir, png_file_name), format="PNG")
                #+ "Specific text: "+str([v for k, v in pages_text.values()])]


    def check_page_num(self, paper_type):
        """Check if the paper exceeds the page limit."""

        # TODO: Enable uploading a paper_type file to include all papers' types.

        # thresholds for different types of papers
        standards = {"short": 5, "long": 9, "other": float("inf")}
        page_threshold = standards[paper_type.lower()]
        candidates = {"References", "Acknowledgments", "Acknowledgement", "Acknowledgment", "EthicsStatement", "EthicalConsiderations", "Ethicalconsiderations", "BroaderImpact", "EthicalConcerns"}
        #acks = {"Acknowledgment", "Acknowledgement"}

        # Find (references, acknowledgements, ethics).
        marker = None
        if len(self.pdf.pages) <= page_threshold:
            return

        for i, page in enumerate(self.pdf.pages):
            if i+1 in self.page_errors:
                continue
            text = page.extract_text().split('\n')
            for j, line in enumerate(text):
                if marker is None and any(x in line for x in candidates):
                    marker = (i+1, j+1)
                #if "Acknowl" in line and all(x not in line for x in acks):
                #    self.logs[Error.SPELLING] = ["'Acknowledgments' was misspelled."]

        # if the first marker appears after the first line of page 10,
        # there is high probability the paper exceeds the page limit.

        if marker > (page_threshold + 1, 1):
            page, line = marker
            self.logs[Error.PAGELIMIT] = [f"Paper exceeds the page limit "
                                      f"because first (References, "
                                      f"Acknowledgments, Ethics Statement) was found on "
                                      f"page {page}, line {line}."]


    def check_font(self):
        """ Checks the fonts. """

        correct_fontnames = set(["NimbusRomNo9L-Regu",
                                 "TeXGyreTermesX-Regular",
                                 "TimesNewRomanPSMT",
                                 ])

        fonts = defaultdict(int)
        for i, page in enumerate(self.pdf.pages):
            try:
                for char in page.chars:
                    fonts[char['fontname']] += 1
            except:
                self.logs[Error.FONT] += [f"Can't parse page #{i+1}"]

        max_font_count, max_font_name = max((count, name) for name, count in fonts.items())  # find most used font
        sum_char_count = sum(fonts.values())

        # TODO: make this a command line argument
        if max_font_count / sum_char_count < 0.35:  # the most used font should be used more than 35% of the time
            self.logs[Error.FONT] += ["Can't find the main font"]

        if not any([max_font_name.endswith(correct_fontname) for correct_fontname in correct_fontnames]):  # the most used font should be `correct_fontname`
            self.logs[Error.FONT] += [f"Wrong font. The main font used is {max_font_name} when it should a font in {correct_fontnames}."]

    def make_name_check_config(self):
        """Configure the name checking parameters"""

        config_dict = {
            'file': self.pdfpath,
            'show_names': False, # Show how the name is changed
            'whole_name': False, # Consider the whole name changes
            'first_name': True, # Consider only first name changes
            'last_name': True, # Consider only last name changes
            'ref_string': 'References', # How the bibilography starts
            'mode': 'ensemble', # The mode for scholarcy, ensemble worked the best for ACL papers
            'initials': True # Allow abbreviating first names to initials only.
        }

        return Namespace(**config_dict)


    def check_references(self):
        """ Check that citations have URLs, and that they have venues (not just arXiv ids). """

        found_references = False
        arxiv_word_count = 0
        doi_url_count = 0
        arxiv_url_count = 0
        all_url_count = 0

        for i, page in enumerate(self.pdf.pages):
            try:
                page_text = page.extract_text()
            except:
                page_text = ""
                self.logs[Warn.BIB] += [f"Can't parse page #{i+1}"]

            lines = page_text.split('\n')
            for j, line in enumerate(lines):
                if "References" in line:
                    found_references = True
                    break
            if found_references:
                arxiv_word_count += page_text.lower().count('arxiv')
                urls = [h['uri'] for h in page.hyperlinks]
                urls = set(urls)  # When link text spans more than one line, it returns the same url multiple times
                for url in urls:
                    if 'doi.org' in url:
                        doi_url_count += 1
                    elif 'arxiv.org' in url:
                        arxiv_url_count += 1
                    all_url_count += 1

        # The following checks fail in ~60% of the papers. TODO: relax them a bit

        if args.disable_name_check:
            config = self.make_name_check_config()
            output_strings = self.pdf_namecheck.execute(config)
            self.logs[Warn.BIB] += output_strings

        if doi_url_count < 3:
            self.logs[Warn.BIB] += [f"Bibliography should use ACL Anthology DOIs whenever possible. Only {doi_url_count} references do."]

        if arxiv_url_count > 0.2 * all_url_count:  # only 20% of the links are allowed to be arXiv links
            self.logs[Warn.BIB] += [f"It appears you are using arXiv links more than you should ({arxiv_url_count}/{all_url_count}). Consider using ACL Anthology DOIs instead."]

        if all_url_count < 5:
            self.logs[Warn.BIB] += [f"It appears most of the references are not using paper links. Only {all_url_count} links found."]

        if arxiv_word_count > 10:
            self.logs[Warn.BIB] += [f"It appears you are using arXiv references more than you should ({arxiv_word_count} found). Consider using ACL Anthology references instead."]

        if not found_references:
            self.logs[Warn.BIB] += ["Couldn't find any references."]


args = None
def worker(pdf_path, paper_type):
    """ process one pdf """
    return Formatter().format_check(submission=pdf_path, paper_type=paper_type)


def main():
    global args
    parser = argparse.ArgumentParser()
    parser.add_argument('submission_paths', metavar='file_or_dir', nargs='+',
                        default=[])
    parser.add_argument('--paper_type', choices={"short", "long", "other"},
                        default='long')
    parser.add_argument('--num_workers', type=int, default=1)
    parser.add_argument('--disable_name_check', action='store_false')

    args = parser.parse_args()
    

    # retrieve file paths
    paths = {join(root, file_name)
             for path in args.submission_paths
             for root, _, file_names in walk(path)
             for file_name in file_names}
    paths.update(args.submission_paths)

    # retrieve files
    fileset = sorted([p for p in paths if isfile(p) and p.endswith(".pdf")])

    if not fileset:
        print(f"No PDF files found in {paths}")

    if args.num_workers > 1:
        from multiprocessing.pool import Pool
        with Pool(args.num_workers) as p:
            list(tqdm(p.imap(worker, fileset), total=len(fileset)))
    else:
        # TODO: make the tqdm togglable
        #for submission in tqdm(fileset):
        for submission in fileset:
            worker(submission, args.paper_type)

if __name__ == "__main__":
    main()
