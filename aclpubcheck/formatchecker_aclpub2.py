'''
python3 formatchecker.py [-h] [--paper_type {long,short,other}] file_or_dir [file_or_dir ...]
'''

from formatchecker import *
import argparse
import yaml
import os

import tsv
import csv

def main():
    global args
    parser = argparse.ArgumentParser()
    parser.add_argument('--papers_dir', default=".", help='Path of the folder containing the camera ready papers.')
    parser.add_argument('--paper_type', choices={"short", "long", "other", "auto"},
                        default='long')
    parser.add_argument('--papers_yaml_path', default="papers.yaml", help='Path of input papers.yaml file.')
    parser.add_argument('--output_file', default="output.tsv", help='Path of output TSV files with the information about problematic papers.')
    parser.add_argument('--output_dir', default=".", help='Path of output folder with the output JSON and PNG files.')


    args = parser.parse_args()

    input_paper_type = args.paper_type

    formatter = Formatter()

    # Relaxing the margin on ONE point
    formatter.right_offset = 5 #4.5
    formatter.left_offset = 5 #2
    formatter.top_offset = 5 #1

    print_only_errors = True

    with open(args.output_file, 'wt') as out_file:
        tsv_writer = csv.writer(out_file, delimiter='\t')

        tsv_writer.writerow(['correct', 'id', 'file', 'title', 'authors', 'emails', 'logs'])

        with open(args.papers_yaml_path) as file:
            papers = yaml.load(file, Loader=yaml.FullLoader)

            for paper in papers:
                input_file_name = paper["file"]

                authors = paper["authors"]
                id = paper["id"]
                file = paper["file"]
                title = paper["title"]

                paper_type = input_paper_type

                if input_paper_type == "auto":
                    yaml_paper_type = paper["attributes"]["paper_type"]
                    if "long" in yaml_paper_type:
                        paper_type = "long"
                    if "short" in yaml_paper_type:
                        paper_type = "short"

                names = []
                emails = []

                logs_json = formatter.format_check(os.path.join(args.papers_dir, input_file_name), paper_type, output_dir = args.output_dir, print_only_errors = print_only_errors)

                # it is empty
                if not logs_json:
                    is_correct = True
                else:
                    is_correct = False

                for author in authors:
                    names.append(author["name"])
                    emails.append(author["emails"])


                tsv_writer.writerow([is_correct, id, file, title, ";".join(names), ";".join(emails), str(logs_json)])
                out_file.flush()


if __name__ == "__main__":
    main()
