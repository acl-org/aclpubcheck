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
    parser.add_argument('--paper_type', choices={"short", "long", "other"},
                        default='long')
    parser.add_argument('--papers_yaml_path', default="papers.yaml", help='Path of input papers.yaml file.')
    parser.add_argument('--output_log', default="output.tsv", help='Path of output TSV files with the information about problematic papers.')


    args = parser.parse_args()

    with open(args.output_log, 'wt') as out_file:
        tsv_writer = csv.writer(out_file, delimiter='\t')

        tsv_writer.writerow(['id', 'file', 'authors', 'emails'])

        with open(args.papers_yaml_path) as file:
            papers = yaml.load(file, Loader=yaml.FullLoader)

            for paper in papers:
                input_file_name = paper["file"]
                is_correct = worker(os.path.join(args.papers_dir, input_file_name), args.paper_type)

                authors = paper["authors"]
                id = paper["id"]
                file = paper["file"]

                names = []
                emails = []

                for author in authors:
                    names.append(author["name"])
                    emails.append(author["emails"])

                tsv_writer.writerow([id, file, ";".join(names), ";".join(emails)])


if __name__ == "__main__":
    main()
