from setuptools import setup


install_requires = [
	"tqdm",
	"termcolor",
	"pandas",
	"pdfplumber",
	"rebiber<2.0.0",  # 2.0 introduces breaking changes
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
			"aclpubcheck=aclpubcheck.__main__:main",
  		],	
	},
)
