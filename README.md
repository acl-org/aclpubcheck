# ACL pubcheck
ACL pubcheck is a Python tool that automatically detects font errors, author formatting errors, margin violations, outdated citations as well as many other common formatting errors in papers that are using the LaTeX sty file associated with ACL venues. The script can be used to check your papers before you submit to a conference. (We highly recommend running ACL pubcheck on your papers *pre-submission*&mdash;a well formatted paper helps keep the reviewers focused on the scientific content.) However, its main purpose is to ensure your accepted paper is properly formatted, i.e., it follows the venue's style guidelines. The script is used by the publication chairs at most ACL events to check for formatting issues. Indeed, running this script yourself and fixing errors before uploading the camera-ready version of your paper will often save you a personalized email from the publication chairs.

## Installation

The simplest way to use `aclpubcheck` is to install using `pip` directly from the GitHub repository (DIFFERENT from `pypi`):

```bash
pip3 install git+https://github.com/acl-org/aclpubcheck
```

Alternatively, you can install directly from source and build locally:
```bash
# clone using ssh
git clone git@github.com:acl-org/aclpubcheck.git
# or http
git clone https://github.com/acl-org/aclpubcheck.git

cd aclpubcheck/

# install locally
pip install -e .
```

## Usage

Once installed, you can use apply it on a PDF:

```bash
# Script execution
aclpubcheck --paper_type PAPER_TYPE path/to/paper.pdf

# Module execution (in case script execution does not work)
python3 -m aclpubcheck --paper_type PAPER_TYPE path/to/paper.pdf
```

Replace `PAPER_TYPE` with one of (1) `long`, (2) `short`, (3) `demo`, depending on the type of paper you have accepted. Then, change `path/to/paper.pdf` to be the path to your paper. For example:

```bash
# -p is a shorthand for --paper_type
python3 -m aclpubcheck --p long example/2023.acl-tutorials.1.pdf
```

If you find that ACL pubcheck gives you a margin error due to a figure that runs into the margin, you can often fix the problem by applying the [adjustbox package](https://ctan.org/pkg/adjustbox?lang=en). Additionally, if the margin error is caused by an equation, then it may help to break the equation over two lines.

ACL pubcheck is meant to be run on the camera ready version of the paper, not on the review version (e.g. anonymous, line-numbered submission version). Running ACL pubcheck on a line-numbered version will result in a stream of spurious errors related to the numbers in the margins.

**Note**: Additional info can be found in the PDF document ``aclpubcheck_additional_info.pdf`` included in this package.

## Online Versions 

If you are having trouble with installing and using the Python toolkit directly, you can use:
- a [**Colab version** you can use to directly upload and run aclpubcheck](https://colab.research.google.com/github/acl-org/aclpubcheck/blob/main/aclpubcheck_online.ipynb) without local installation (thank Danilo Croce).
- a **Hugging Face Space** at https://huggingface.co/spaces/teelinsan/aclpubcheck (thank Andrea Santilli). More info about this version can be found at https://github.com/teelinsan/aclpubcheck-gui

## Updating the names in citations

### Description

Our toolkit now automatically checks your citations and will leave a warning if you have used incorrect names or author list. Please have a look [here](https://2021.naacl.org/blog/name-change-procedure/) on why it is important to use updated citations.

Demo version of PDF name checking is available [here](https://pdf-name-change-checking.herokuapp.com/).

### How it's done

The bibilography from your PDF file is extracted using [Scholarcy API](https://ref.scholarcy.com/api/). Each bib entry in this bib file is updated by pulling information from ACL anthology, DBLP and arXiv; by using fuzzy match of the titles. After updating the bibs, the author names are compared and mismatches in author names are warned.

![Procedure](pdf_image.png)

### Functionality

The functions are present in `aclpubcheck/name_check.py`. The class `PDFNameCheck` is used in `formatchecker.py`.

### Caveats

Some of the warnings generated for citations may be spurious and inaccurate, due to parsing and indexing errors. We encourage you to double check the citations and update them depending on the latest source. If you believe that your citation is updated and correct, then please ignore those warnings. You can fix your bib files using the toolkit like [rebiber](https://github.com/yuchenlin/rebiber).

### Screenshots

This is how the warnings appear for the outdated names. You would be directed to a URL where you can correct the citations. We are not showing the name changes as it might out the deadnames in the warnings.

![Screenshot](screenshot.png)

## Credits
The original version of ACL pubcheck was written by Yichao Zhou, Iz Beltagy, Steven Bethard, Ryan Cotterell and Tanmoy Chakraborty in their role as publications chairs of [NAACL 2021](https://2021.naacl.org/organization/). The tool was improved by Ryan Cotterell and Danilo Croce in their role as publication chairs of [ACL 2022](https://www.2022.aclweb.org/organisers) and [NAACL 2022](https://2022.naacl.org/). Pranav A added the name checking functions to this toolkit.

## Maintenance 
The tool is primarily maintained by Ryan Cotterell and Danilo Croce. More volunteers are welcome!
