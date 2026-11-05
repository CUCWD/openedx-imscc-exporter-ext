Change Log
##########

..
   All enhancements and patches to openedx_imscc_exporter_ext will be documented
   in this file.  It adheres to the structure of https://keepachangelog.com/ ,
   but in reStructuredText instead of Markdown (for ease of incorporation into
   Sphinx documentation and the PyPI description).

   This project adheres to Semantic Versioning (https://semver.org/).

.. There should always be an "Unreleased" section for changes pending release.

Unreleased
**********

0.1.1 - 2026-11-04
******************

Changed
========

* Defer runtime dependency versions to the host Ulmo platform and declare the
  exporter's direct dependencies. Move Atlas to development requirements.
* Snapshot platform constraints for standalone tooling and generate reproducible
  requirement locks without compiling pins into runtime dependencies.
* Target Ulmo Django 5.2.11 in tox and CI on Python 3.11 and 3.12.

Fixed
=====

* Fix standalone documentation builds by mocking edx-platform imports during
  autodoc and using valid Python 3.11 and Django 5.2 documentation inventories.
* Clean up exporter formatting, docstrings, imports, and compatibility with
  Ulmo's quality tooling while preserving public interfaces and export output.
* Limit standalone import-check exceptions to modules supplied by edx-platform.
* Add regression coverage for assignment and external-tool exports, score
  calculation, and stable serialized block identifiers.

0.1.0 - 2025-12-11
**********************************************

Added
=====

* First release on PyPI.
