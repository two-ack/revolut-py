# -*- coding: utf-8 -*-
# Based on http://peterdowns.com/posts/first-time-with-pypi.html

from setuptools import setup
import codecs
import os.path


def read(rel_path):
    here = os.path.abspath(os.path.dirname(__file__))
    with codecs.open(os.path.join(here, rel_path), 'r') as fp:
        return fp.read()


def get_version(rel_path):
    for line in read(rel_path).splitlines():
        if line.startswith('__version__'):
            delim = '"' if '"' in line else "'"
            return line.split(delim)[1]
    else:
        raise RuntimeError('Unable to find version string.')


_NAME = 'revolut-py'
_PACKAGE_LIST = ['revolut', 'revolut_bot', 'token_renewal']  # alternatively setuptools.find_packages(".", exclude=["test"])
_URL_GITHUB = 'https://github.com/laur89/revolut-py'
_DESCRIPTION = 'Unofficial Revolut api client for python'
_MOTS_CLES = ['api', 'revolut', 'bank', 'parsing', 'cli',
              'python-wrapper', 'scraping', 'scraper', 'parser',
              'lib', 'library']
_SCRIPTS = ['revolut_cli.py', 'revolutbot.py', 'revolut_transactions.py']
__version__ = get_version('revolut/__init__.py')
# To delete here + 'scripts' dans setup()
# if no command is used in the package

with open('requirements.txt') as reqs_file:
    requirements = reqs_file.read().splitlines()

setup(
    name=_NAME,
    packages=_PACKAGE_LIST,
    package_data={},
    scripts=_SCRIPTS,
    version=__version__,
    license='MIT',
    platforms='Posix; MacOS X',
    description=_DESCRIPTION,
    long_description=_DESCRIPTION,
    author='Laur',
    url=_URL_GITHUB,
    download_url='%s/tarball/%s' % (_URL_GITHUB, __version__),
    keywords=_MOTS_CLES,
    setup_requires=requirements,
    install_requires=requirements,
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    python_requires='>=3.7',
    tests_require=['pytest'],
)

# ------------------------------------------
# To upload a new version on pypi
# ------------------------------------------
# Make sure everything was pushed (with a git status)
# (or git commit --am "Comment" and git push)
# export VERSION=0.1.4; git tag $VERSION -m "Update X-Client-Version + allow passing a selfie when Third factor authentication is required"; git push --tags

# If you need to delete a tag
# git push --delete origin $VERSION; git tag -d $VERSION
