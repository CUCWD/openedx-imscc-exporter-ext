"""Exercise export output with lightweight stand-ins for platform services."""

import importlib.util
import sys
import uuid
from contextlib import nullcontext
from itertools import count
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from lxml import etree
from opaque_keys.edx.locator import CourseLocator


@pytest.fixture(name='exporter')
def exporter_fixture():
    """Load real exporter code with only unavailable platform imports replaced."""
    modules = {
        name: ModuleType(name)
        for name in (
            'lms', 'lms.djangoapps', 'lms.djangoapps.courseware',
            'lms.djangoapps.courseware.exceptions', 'xmodule', 'xmodule.modulestore',
        )
    }
    modules['lms.djangoapps.courseware.exceptions'].CourseRunNotFound = LookupError
    modules['xmodule.modulestore'].ModuleStoreEnum = SimpleNamespace(
        Branch=SimpleNamespace(published_only='published-only')
    )
    source = Path(__file__).parents[1] / 'openedx_imscc_exporter_ext' / 'exporter.py'
    spec = importlib.util.spec_from_file_location('_exporter_under_test', source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def make_block(category, name, course_key, children=(), **fields):
    """Create the small block interface used by the exporter."""
    return SimpleNamespace(
        category=category, display_name=name, url_name=name.lower(),
        course_id=course_key, format='Homework' if category == 'sequential' else None, get_children=lambda: children,
        **fields,
    )


def make_manager(exporter, destination, external_only):
    """Construct a published course with an assignment and a reading subsection."""
    key = CourseLocator('Example', 'DEMO', '2026')
    problem = make_block('problem', 'Problem', key, max_score=lambda: 3)
    assignment = make_block('sequential', 'Assignment', key, [problem])
    reading = make_block('sequential', 'Reading', key)
    reading.format = ''
    chapter = make_block('chapter', 'Chapter', key, [assignment, reading])
    course = make_block('course', 'Demo Course', key, [chapter])
    course.grading_policy = {'GRADER': [{'type': 'Homework', 'weight': 1}]}
    store = SimpleNamespace(
        get_course=lambda *_args, **_kwargs: course,
        branch_setting=lambda *_args: nullcontext(),
        bulk_operations=lambda *_args: nullcontext(),
    )
    return exporter.CourseExportManager(store, None, [key], str(destination), 'export', external_only)


@pytest.mark.parametrize('external_only', [False, True])
def test_export_files(exporter, tmp_path, settings, monkeypatch, external_only):
    """Keep manifest resources, assignment scores, and LTI links consistent."""
    settings.LMS_BASE = 'lms.example.test'
    settings.PLATFORM_NAME = 'Example'
    identifiers = count(1)
    monkeypatch.setattr(exporter.uuid, 'uuid4', lambda: uuid.UUID(int=next(identifiers)))
    manager = make_manager(exporter, tmp_path, external_only)
    manager.export()
    folder = tmp_path / 'export'
    manifest = etree.parse(str(folder / 'imsmanifest.xml'))
    assert manifest.xpath('string(//*[local-name()="schemaversion"])') == '1.1.0'
    for filename in manifest.xpath('//*[local-name()="file"]/@href'):
        assert (folder / filename).is_file()
    tool = etree.parse(str(folder / (manager.external_tool_identifierref + '.xml')))
    assert tool.xpath('string(//*[local-name()="secure_launch_url"])') == 'https://lms.example.test/lti_provider/'
    assert tool.xpath('string(//*[local-name()="title"])') == 'Example (lms.example.test)'
    assignment_files = list(folder.glob('*/assignment_settings.xml'))
    assert len(assignment_files) == (0 if external_only else 1)
    if assignment_files:
        assignment = etree.parse(str(assignment_files[0]))
        assert assignment.xpath('string(//*[local-name()="points_possible"])') == '3.0'
        assert assignment.xpath('string(//*[local-name()="external_tool_url"])') == (
            'https://lms.example.test/lti_provider/courses/course-v1:Example+DEMO+2026/'
            'block-v1:Example+DEMO+2026+type@sequential+block@assignment'
        )
    metadata = etree.parse(str(folder / 'course_settings/module_meta.xml'))
    assert metadata.xpath('//*[local-name()="content_type"]/text()') == (
        ['ContextModuleSubHeader', 'ContextExternalTool', 'ContextExternalTool'] if external_only
        else ['ContextModuleSubHeader', 'Assignment', 'ContextExternalTool']
    )


@pytest.mark.parametrize('max_count, expected', [(0, 3.0), (None, 3.0), (2, 2.0)])
def test_score_randomized_problem_bank(exporter, max_count, expected):
    """Use the bank count when present and otherwise score its children."""
    manager = exporter.CourseExportManager.__new__(exporter.CourseExportManager)
    problem = make_block('problem', 'Problem', None, max_score=lambda: 3)
    bank = make_block('random', 'Bank', None, [problem], max_count=max_count)
    subsection = make_block('sequential', 'Quiz', None, [bank])
    assert manager.get_total_score(subsection) == expected


def test_serialized_blocks_have_stable_keys(exporter):
    """Equivalent block metadata must retrieve the same exported identifiers."""
    first = exporter.SerializableChapterSequential('Quiz', 'Homework', 'quiz', 'sequential', 'demo')
    second = exporter.SerializableChapterSequential('Quiz', 'Homework', 'quiz', 'sequential', 'demo')
    assert {first: 'identifier'}[second] == 'identifier'
