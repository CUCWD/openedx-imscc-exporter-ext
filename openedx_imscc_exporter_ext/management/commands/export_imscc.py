"""
A Django command that exports a course to a tar.gz file using IMSCC protocol

At present, it differs from Studio exports in several ways:

* It does not include static content.
* It only supports the export of courses.  It does not export libraries.
"""

import os
import re
import shutil
import tarfile
from tempfile import mkdtemp, mktemp
from textwrap import dedent

from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from path import Path as path

from xmodule.modulestore.django import modulestore
# from xmodule.modulestore.imscc_exporter import export_course_to_imscc
from openedx_imscc_exporter_ext.exporter import export_course_to_imscc

class Command(BaseCommand):
    """
    Export a course to IMSCC. The output is compressed as a tar.gz file.
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument('course_id',  nargs="+") #nargs = "+" allows parsing of unlimited course ids
        parser.add_argument('--output')
        parser.add_argument(
            '--external-tool-only',
            action = 'store_true',
            help = 'Export Common Cartridge file using only external tools and no assignment types (e.g. D2L)')

    def handle(self, *args, **options):
        external_tool_only = options.get('external_tool_only', False)
        course_ids = options['course_id']

        # stores all the different course keys based on the inputted course ids
        course_keys = []
        for course_id in course_ids:
            try:
                course_keys.append(CourseKey.from_string(course_id))
            except InvalidKeyError:
                raise CommandError("Unparsable course_id")  # lint-amnesty, pylint: disable=raise-missing-from
            except IndexError:
                raise CommandError("Insufficient arguments")  # lint-amnesty, pylint: disable=raise-missing-from

        filename = options['output'] + f"{'-external-tool-only' if external_tool_only else ''}-{datetime.now():%Y%m%d-%H%M%S}"
        pipe_results = False

        if filename is None:
            filename = mktemp()
            pipe_results = True

        export_course_to_tarfile(course_keys, filename, external_tool_only)

        results = self._get_results(filename) if pipe_results else b''

        # results is of type bytes, so we must write the underlying buffer directly.
        self.stdout.buffer.write(results)

    def _get_results(self, filename):
        """
        Load results from file.

        Returns:
            bytes: bytestring of file contents.
        """
        with open(filename, 'rb') as f:
            results = f.read()
            os.remove(filename)
        return results


def export_course_to_tarfile(course_keys, filename, external_tool_only):
    """Exports a course into a tar.gz file"""
    tmp_dir = mkdtemp()
    try:
        course_dir = export_course_to_directory(course_keys, tmp_dir, external_tool_only)
        compress_directory(course_dir, filename)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def export_course_to_directory(course_keys, root_dir, external_tool_only):
    """Export course into a directory"""
    # attempt to get all the courses based on the course_keys
    store = modulestore()
    courses = []
    for course_key in course_keys:
        course = store.get_course(course_key)
        if course is None:
            raise CommandError("Invalid course_id")
        courses.append(course)

    course_ids = []
    for course in courses:
        course_ids.append(course.id)
    # The safest characters are A-Z, a-z, 0-9, <underscore>, <period> and <hyphen>.
    # We represent the first four with \w.
    # TODO: Once we support courses with unicode characters, we will need to revisit this.
    replacement_char = '-'
    course_dir = replacement_char.join([courses[0].id.org, courses[0].id.course, courses[0].id.run])
    course_dir = re.sub(r'[^\w\.\-]', replacement_char, course_dir)

    if len(courses) > 1:
        course_dir = "MULTI-COURSE-EXPORT"
    export_course_to_imscc(store, None, course_ids, root_dir, course_dir, external_tool_only)

    export_dir = path(root_dir) / course_dir
    return export_dir


def compress_directory(directory, filename):
    """Compress a directory into a zip file with .imscc extension"""
    shutil.make_archive(filename, 'zip', directory)
    # Rename the .zip file to .imscc
    os.rename(f"{filename}.zip", f"{filename}.imscc")