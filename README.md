# ACL pubcheck
ACL pubcheck is a Python tool that automatically detects author formatting errors, margin violations as well as many other common formatting errors in papers that are using the LaTeX sty file associated with ACL venues. The script can be used to check your papers before you submit to a conference. (We highly recommend running ACL pubcheck on your papers *pre-submission*&mdash;a well formatted paper helps keep the reviewers focused on the scientific content.) However, its main purpose is to ensure your accepted paper is properly formatted, i.e., it follows the venue's style guidelines. The script is used by the publication chairs at most ACL events to check for formatting issues. Indeed, running this script yourself and fixing errors before uploading the camera-ready version of your paper will often save you a personalized email from the publication chairs. 

You can install the package by cloning the repo
1. ``git clone git@github.com:acl-org/aclpubcheck.git``
2. ``cd aclpubcheck``
3. ``pip install -e .``

Or directly
1. ``pip3 install git+https://github.com/acl-org/aclpubcheck.git``

You can run the script on a paper as follows
``python3 aclpubcheck/formatchecker.py --paper_type PAPER_TYPE PAPER_NAME.pdf``

**Notice**: Additional info can be found in the PDF document ``aclpubcheck_additional_info.pdf`` included in this package.
