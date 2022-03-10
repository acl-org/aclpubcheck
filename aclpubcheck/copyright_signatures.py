import argparse
import textwrap
import pandas as pd


def write_copyright_signatures(submissions_path):

    def clean_str(value):
        return '' if pd.isna(value) else value.strip()

    # write all copyright signatures to a single file, noting any problems
    with open("copyright-signatures.txt", "w") as output_file:
        df = pd.read_csv(submissions_path, keep_default_na=False)
        for index, row in df.iterrows():
            submission_id = row["Submission ID"]

            # NOTE: These were the names in the custom final submission form
            # for NAACL 2021. Names and structure may be different depending
            # on your final submission form.
            signature = clean_str(row["copyrightSig"])
            org_name = clean_str(row["orgName"])
            org_address = clean_str(row["orgAddress"])

            # collect all authors and their affiliations
            authors_parts = []
            for i in range(1, 25):
                name_parts = [
                    clean_str(row[f'{i}: {x} Name'])
                    for x in ['First', 'Middle', 'Last']]
                name = ' '.join(x for x in name_parts if x)
                if name:
                    affiliation = clean_str(row[f"{i}: Affiliation"])
                    authors_parts.append(f'{name} ({affiliation})')
            authors = '\n'.join(authors_parts)

            # write out the copyright signature in the standard ACL format
            indent = " " * 4
            output_file.write(f"""
Submission # {submission_id}
Title: {row["Title"]}
Authors:
{textwrap.indent(authors, indent)}
Signature: {signature}
Your job title (if not one of the authors): {clean_str(row["jobTitle"])}
Name and address of your organization:
{textwrap.indent(org_name, indent)}
{textwrap.indent(org_address, indent)}

=================================================================
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--submissions', dest='submissions_path',
                        default='Submission_Information.csv')
    args = parser.parse_args()
    write_copyright_signatures(**vars(args))
