import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name='picturec',
    version='0.3.3',
    author='Noah Swimmer',
    author_email='nswimmer@ucsb.edu',
    description='PICTURE-C Control Software',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/MazinLab/picturec.git',
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ]
)