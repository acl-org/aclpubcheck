from setuptools import setup, find_packages


install_requires = [
	"tqdm",
	"termcolor",
	"pandas",
	"pdfplumber",
	"rebiber @ git+https://github.com/yuchenlin/rebiber@b917e36ec94084a84025eb06781b5aac96d1cedd",
	"pybtex",
	"pylatexenc",
	"setuptools",
	"Unidecode",
	"tsv"
]


setup(
	name="aclpubcheck",
	install_requires=install_requires,
	version="0.1",
	python_requires=">=3.10",
	scripts=[],
	packages=find_packages(include=["aclpubcheck*"]),
	entry_points = {
		'console_scripts': [
			"aclpubcheck=aclpubcheck.__main__:main",
  		],	
	},
)
