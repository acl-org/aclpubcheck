from setuptools import setup


install_requires = [
	"tqdm",
	"termcolor",
	"pandas",
	"pdfplumber",
	"rebiber",
	"pybtex",
	"pylatexenc",
	"Unidecode",
	"tsv"
]


setup(
	name="aclpubcheck",
	install_requires=install_requires,
	version="0.1",
	scripts=[],
	entry_points = {
		'console_scripts': [
			"aclpubcheck=aclpubcheck.formatchecker:main",
  		],	
	},
)
