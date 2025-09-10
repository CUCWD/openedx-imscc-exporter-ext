"""
Methods for exporting course data to IMSCC
"""

import lxml.etree
from django.conf import settings
from fs.osfs import OSFS
from lms.djangoapps.courseware.exceptions import CourseRunNotFound
from opaque_keys.edx.locator import CourseLocator
from xmodule.modulestore import ModuleStoreEnum

import uuid
from datetime import datetime
import re


class SerializableChapterSequential:
    """
    A serialized chapter or sequential object which allows for access of only the important attributes
    as well as hashing without objet memory related issues
    """
    def __init__(self, display_name, format, url_name, category, course_id):
        """
        'display_name': The display name of the chapter or sequential
        'format': The format of the chapter or sequential (commonly used with assignment types)
        'url_name': The ending url of the chapter or sequential (used with creating LTI links)
        'category': The category of the module (chapter or sequential)
        'course_id': The course id in which the chapter or sequential is from
        """
        self.display_name = display_name
        self.format = format
        self.url_name = url_name
        self.category = category
        self.course_id = course_id

    def __hash__(self):
        """
        Provides an overriden hash function to be used for dictionaries to prevent memory related issues
        when hashing objects
        """
        # Use a combination of the attributes to generate a hash value
        return hash((self.display_name, self.format, self.url_name, self.category, self.course_id))

    def __eq__(self, other):
        """
        Provides an equals function for comparing 2 serialized chapter or sequential objects
        """
        # Define equality based on the attributes
        if isinstance(other, SerializableChapterSequential):
            return (self.display_name == other.display_name and
                    self.format == other.format and
                    self.url_name == other.url_name and
                    self.category == other.category and
                    self.course_id == other.course_id)
        return False

class CourseExportManager:
    """
    Manages IMSCC exporting for courselike objects.
    """
    def __init__(self, modulestore, contentstore, courselike_keys, root_dir, target_dir, external_tool_only):
        """
        Export all modules from `modulestore` and content from `contentstore` as xml to `root_dir`.

        `modulestore`: A `ModuleStore` object that is the source of the modules to export
        `contentstore`: A `ContentStore` object that is the source of the content to export, can be None
        `courselike_keys`: The Locators of the Descriptor to export
        `root_dir`: The directory to write the exported xml to
        `target_dir`: The name of the directory inside `root_dir` to write the content to
        'external_tool_only': Option to export the course without any assignments and have everything be set as an external tool
        """
        self.modulestore = modulestore
        self.contentstore = contentstore
        self.courselike_keys = courselike_keys
        self.root_dir = root_dir
        self.target_dir = str(target_dir)
        self.external_tool_only = external_tool_only
        self.lms_base = settings.LMS_BASE
        self.platform_name = settings.PLATFORM_NAME

        """
        Sets up some information to share between export functions

        'sequential_to_identifier': A dictionary mapping each sequential to an unique identifier
                                    An 'identifier' attribute of the 'item' element containing sequential information in the 'imsmanifest.xml' file
                                    An 'identifier' attribute of the 'item' element containing sequential information in the 'course_settings/module_meta.xml' file
                                    Links sequentials with all the information above

        'sequential_to_identifierref': A dictionary mapping each sequential to an unique identifier
                                       An 'identifierref' attribute of the 'item' element containing sequential information in the 'imsmanifest.xml file'
                                       An 'identifierref' attribute of the 'item' element containing sequential information in the 'course_settings/module_meta.xml' file
                                       An 'identifier' attribute of the assignment object in each sequential's individual assignment_settings.xml file
                                       The name of the folder that stores a sequential's assignment_settings.xml file and html file
                                       Links sequentials with all the information above

        'chapter_to_identifier': A dictionary mapping each chapter to an unique identifier
                                 An 'identifier' attribute of the 'item' element containing chapter information in the 'imsmanifest.xml' file
                                 An 'identifier' attribute of the 'item' element containing chapter information in the 'course_settings/module_meta.xml' file
                                 Links sequentials with all the information above

        'assignment_group_to_identifier': A dictionary mapping the names of each assignment group to an unique identifier
                                          An 'identifier' attribute of the 'assignmentGroup' element in 'assignment_groups.xml'
                                          An 'assignment_group_identifierref' child element of the 'assignment' element in an assignment's 'assignment_settings.xml' file
                                          Links assignments to their assignment types

        'external_tool_identifierref': A pre-made identifier to link all external tool references
                                       The name of the file + '.xml' in the root directory containing LTI metadata and information
                                       An 'identifierref' attrubute of the 'item' element containing LTI data
                                       An 'external_tool_identifierref' element of the 'assignment' element in an assignment's 'assignment_settings.xml' file

        'course_settings_identifier': A pre-made identifier to link course_settings references
                                       An 'identifier' attrubute of the 'course' element in 'course_settings/course_settings.xml' file containing course settings
                                       An 'identifier' attribute of the 'resource' element in 'imsmanifest.xml' file containing course settings information

        'module_identifier': A pre-made identifier to link the only module that is created
                             An 'identifier' attribute of the 'item' element under the 'item' element with the identifier 'LearningModules' in 'imsmanfiest.xml'
                             An 'identifier' attribute of the 'module' element in the 'course_settings/module_meta.xml' file
        """

        self.sequential_to_identifier = {}
        self.sequential_to_identifierref = {}
        self.chapter_to_identifier = {}
        self.module_identifiers = {}

        # Fill out the sequential, chapter, and module dictionaries with identifiers
        for courselike_key in self.courselike_keys:
            sequential_modules = self.get_sequential_modules(self.modulestore, courselike_key)
            for sequential in sequential_modules:
                sequential = self.serialize_chapter_sequential(sequential)
                self.sequential_to_identifier[sequential] = self.create_uuid()
                self.sequential_to_identifierref[sequential] = self.create_uuid()

        for courselike_key in self.courselike_keys:
            chapter_modules = self.get_chapter_modules(self.modulestore, courselike_key)
            for chapter in chapter_modules:
                chapter = self.serialize_chapter_sequential(chapter)
                self.chapter_to_identifier[chapter] = self.create_uuid()

        for courselike_key in self.courselike_keys:
            self.module_identifiers[courselike_key] = self.create_uuid()

        self.assignment_group_to_identifier = {}
        self.external_tool_identifierref = self.create_uuid()
        self.course_settings_identifier = self.create_uuid()

    def get_key(self, courselike_key):
        """
        Get the courselike locator key based on the input courselike_key
        """
        return CourseLocator(
            courselike_key.org, courselike_key.course, courselike_key.run, deprecated=True
        )

    def get_courselike(self, courselike_key):
        """
        Get the target courselike object based on the input courselike_key
        """
        # depth = None: Traverses down the entire course structure.
        # lazy = False: Loads and caches all block definitions during traversal for fast access later
        #               -and- to eliminate many round-trips to read individual definitions.
        # Why these parameters? Because a course export needs to access all the course block information
        # eventually. Accessing it all now at the beginning increases performance of the export.
        return self.modulestore.get_course(courselike_key, depth=None, lazy=False)


    def _get_ordered_blocks(self, modulestore, courselike_key):
        """
        Get the ordered published blocks for the course.
        """
        ordered_blocks = []

        # Get the unordered list of blocks from the mongo database.
        with modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
            course_descriptor = modulestore.get_course(courselike_key, depth=0)
            if course_descriptor is None:
                raise CourseRunNotFound(course_key=courselike_key)
            
            # Order the blocks by how they appear in the course. This is important for correct order in the export.
            for section in course_descriptor.get_children():
                ordered_blocks.append(section)
                for subsection in section.get_children():
                    ordered_blocks.append(subsection)
                    # Stop at the subsection level for now because we're using LTI external tools for the subsection level only.
                    # for vertical in subsection.get_children():
                    #     ordered_blocks.append(vertical)
                    #     for block in vertical.get_children():
                    #         ordered_blocks.append(block)
        
        return ordered_blocks
    
    def get_sequential_modules(self, modulestore, courselike_key):
        """
        Retrieve all sequential modules from the course
        """
        # with modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
            # Get all top-level modules (e.g., chapters, sequentials)
            # top_level_modules = modulestore.get_items(courselike_key)
        top_level_modules = self._get_ordered_blocks(modulestore, courselike_key)

        sequentials = []
        for module in top_level_modules:
            if module.category == 'sequential':
                sequentials.append(module)
        return sequentials

    def get_chapter_modules(self, modulestore, courselike_key):
        """
        Retrieve all chapter modules from the course
        """
        # with modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
            # Get all top-level modules (e.g., chapters, sequentials)
            # top_level_modules = modulestore.get_items(courselike_key)
        top_level_modules = self._get_ordered_blocks(modulestore, courselike_key)

        chapter = []
        for module in top_level_modules:
            if module.category == 'chapter':
                chapter.append(module)
        return chapter

    def get_chapter_sequential_modules(self, modulestore, courselike_key):
        """
        Retrieve all chapter and sequential modules from the course
        """
        # with modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
            # Get all top-level modules (e.g., chapters, sequentials)
            # top_level_modules = modulestore.get_items(courselike_key)
        top_level_modules = self._get_ordered_blocks(modulestore, courselike_key)

        sequentials_chapters = []
        for module in top_level_modules:
            if module.category == 'sequential' or module.category == 'chapter':
                sequentials_chapters.append(module)
        return sequentials_chapters

    def serialize_chapter_sequential(self, chapter_sequential):
        """
        Return a serialized object of an inputted chpater or sequential in order to bypass dictionary key issues
        """
        return SerializableChapterSequential(
        chapter_sequential.display_name,
        chapter_sequential.format,
        chapter_sequential.url_name,
        chapter_sequential.category,
        chapter_sequential.course_id
    )

    def get_course_abbreviation(self, courselike_key):
        """
        Returns a course abbreviation to append to the start of module and assignment names
        Returns nothing if the courselike_key provided doesn't match the expected pattern in most courses
        """
        # re pattern for extracting the values between the plus signs to find the Course Number.
        between_pluses = r'(?<=\+)(.*?)(?=\+)'

        courselike_key = str(courselike_key)

        match = re.search(between_pluses, courselike_key)

        # Return the full Course Number value if it matches the expected pattern.
        if match:
            return f"{match.group(1)} "
        else:
            return ''

    def get_total_score(self, sequential):
        """
        Returns the total amount of points that can be earned for an assignment sequential
        """
        total_score = 0.0

        # Get the direct children of the sequential
        children = sequential.get_children()

        # Recursively iterate through the children to get access to the max_score of individual problem components
        for child in children:
            # This try statement will attempt to access max_count, a variable of quiz types with a bank of x amount of questions
            # These quizzes randomly choose max_count number of questions to display from the bank
            # Unable to recurse through the quizzes because otherwise, the total_score returned will include every single question in the bank,
            # not just how many are displayed to the student

            try:
                max_count = child.max_count
                # If max_count is 0 or set to None, keep recursing through it's children
                if max_count == 0 or max_count == None:
                    raise Exception()
                total_score += max_count
            except Exception as e:
                # Attempt to grab max_score value
                try:
                    score = child.max_score()
                    total_score += score
                except Exception as e:
                    pass
                # Only recursively get the score if no max_score or max_count is present
                total_score += self.get_total_score(child)

        return total_score

    def create_uuid(self):
        """
        Returns an essentially unique identifier following canvas's default format
        """
        return 'g' + (str(uuid.uuid4())).replace('-', '')

    def export_assignment_groups(self, modulestore, courselikes, export_fs):
        """
        Exports the 'assignment_groups.xml' file in course_settings
        """
        ################### Assignment groups root ###################
        root = lxml.etree.Element(
            'assignmentGroups',
            nsmap = {
                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

        for courselike in courselikes:
            # Accessing each asignment type with their weight and adding it to the xml
            for grade in courselike.grading_policy['GRADER']:
                # Only add the assignmeent group if it doesn't already exist in the xml
                # For CUCWD, this should only add the 4 primary assignment groups (pre-test, activities, module reinforcement, post-tests)
                # For other platforms, all the assignment groups may not add up correctly but can easily be fixed in post on Canvas
                grade_name = grade['type']
                grade_weight = grade['weight'] * 100 # openedx uses 0-1 grading weight, imscc uses 0-100
                if grade_name not in self.assignment_group_to_identifier:
                    self.assignment_group_to_identifier[grade_name] = self.create_uuid()
                    assignment_group = lxml.etree.SubElement(root, 'assignmentGroup', {'identifier': str(self.assignment_group_to_identifier[grade_name])})
                    lxml.etree.SubElement(assignment_group, 'title').text = 'SkilRedi - ' + grade_name
                    lxml.etree.SubElement(assignment_group, 'group_weight').text = str(grade_weight)

                # Write assignment_groups to a file
            with export_fs.open('course_settings/assignment_groups.xml', 'wb') as assignment_groups_xml:
                tree = lxml.etree.ElementTree(root)
                tree.write(assignment_groups_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_media_tracks(self, export_fs):
        """
        Exports the mostly empty 'media_tracks.xml' file in course_settings
        """

        # Create root
        root = lxml.etree.Element(
            'media_tracks',
            nsmap = {
                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

        # Write to file
        with export_fs.open('course_settings/media_tracks.xml', 'wb') as media_tracks_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(media_tracks_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_files_meta(self, export_fs):
        """
        Exports the mostly empty 'files_meta.xml' file in course_settings
        """
        # Create root
        root = lxml.etree.Element(
            'fileMeta',
            nsmap = {
                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

        # Write to file
        with export_fs.open('course_settings/files_meta.xml', 'wb') as files_meta_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(files_meta_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_course_settings(self, modulestore, courselike_keys, export_fs):
        """
        Exports the 'course_settings.xml' file in course_settings
        """
        # Create root
        root = lxml.etree.Element(
            'course',
            {
                'identifier': self.course_settings_identifier
            },
            nsmap = {
                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

        # Sets the title of the course to MULTI-COURSE-EXPORT if it's multi course, the default courselike key otherwise
        if len(courselike_keys) > 1:
            lxml.etree.SubElement(root, 'title').text = 'MULTI-COURSE-EXPORT'
            lxml.etree.SubElement(root, 'course_code').text = 'MULTI-COURSE-EXPORT'
        else:
            lxml.etree.SubElement(root, 'title').text = str(courselike_keys[0])
            lxml.etree.SubElement(root, 'course_code').text = str(courselike_keys[0])
        lxml.etree.SubElement(root, 'group_weighting_scheme').text =  'percent'

        # Write to file
        with export_fs.open('course_settings/course_settings.xml', 'wb') as course_settings_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(course_settings_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

        # There's this file called canvas_export.txt that contains nothing but a pun...
        # It's referenced in the ims_manifest file for some reason so we're adding it
        with export_fs.open('course_settings/canvas_export.txt', 'w') as canvas_export_txt:
            canvas_export_txt.write('Q: What did the panda say when he was forced out of his natural habitat?\nA: This is un-BEAR-able\n')

    def export_assignment_folders(self, modulestore, courselike_keys, courselikes, export_fs):
        """
        Exports all the individual folders for each sequential that is an assignment
        """
        # Iterate through both courselike_keys and courselikes at the same time
        for courselike_key, courselike in zip(courselike_keys, courselikes):
            # Bulk operations and only operate on published content
            with self.modulestore.bulk_operations(courselike_key):
                with self.modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
                    sequential_modules = self.get_sequential_modules(modulestore, courselike_key)

                    # Parse out non assignments
                    assignment_types = {assignment_type['type'] for assignment_type in courselike.grading_policy['GRADER']}
                    only_assignments = (sequential for sequential in sequential_modules if sequential.format in assignment_types)

                    # Course abbreviation to append to the start of the module names
                    course_abbreviation = self.get_course_abbreviation(courselike_key)

                    for sequential in only_assignments:
                        # Store a non serialized version of the sequential in order to get access to its children for points_possible
                        non_serialized_sequential = sequential
                        sequential = self.serialize_chapter_sequential(sequential)
                        # Create root
                        root = lxml.etree.Element(
                            'assignment',
                            {
                                'identifier': self.sequential_to_identifierref[sequential]
                            },
                            nsmap={
                                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
                            }
                        )
                        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                            'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

                        # Add assignment data like points, assignment type, lti, etc.
                        lxml.etree.SubElement(root, 'title').text = course_abbreviation + sequential.display_name
                        lxml.etree.SubElement(root, 'assignment_group_identifierref').text = str(self.assignment_group_to_identifier[sequential.format])

                        # Adds the maximum number of points to the assignment information
                        lxml.etree.SubElement(root, 'points_possible').text = str(self.get_total_score(non_serialized_sequential))
                        lxml.etree.SubElement(root, 'grading_type').text = 'points'

                        lxml.etree.SubElement(root, 'submission_types').text = 'external_tool'
                        lxml.etree.SubElement(root, 'external_tool_identifierref').text = self.external_tool_identifierref
                        lti_link = f'https://{self.lms_base}/lti_provider/courses/' + str(courselike_key) + "/" + (str(courselike_key)).replace('course', 'block') + '+type@sequential+block@' + sequential.url_name
                        lxml.etree.SubElement(root, 'external_tool_url').text = lti_link
                        lxml.etree.SubElement(root, 'external_tool_data_json').text = '\"\"'
                        lxml.etree.SubElement(root, 'external_tool_link_settings_json').text = '{\"selection_width\":\"\",\"selection_height":\"\"}'
                        lxml.etree.SubElement(root, 'external_tool_new_tab').text = 'false'

                        # Create corresponding HTML file
                        # HTML files follow this same cookie cutter format with the only thing changing is the title
                        html_content ='''<html>
                        <head>
                        <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
                        <title>Assignment: '''

                        html_content_pt2 ='''</title>
                        </head>
                        <body>

                        </body>
                        </html>'''

                        # Make the name of the file match conventions with all lower cases and no spaces, and dashes replacing spaces
                        html_file_name = re.sub(r'[^a-zA-Z0-9\s-]', '', course_abbreviation + sequential.display_name)
                        html_file_name = html_file_name.lower()
                        html_file_name = html_file_name.replace(' ', '-')
                        html_file_name = html_file_name + '.html'

                        # Write to file
                        export_fs.makedirs(str(self.sequential_to_identifierref[sequential]), recreate=True)

                        with export_fs.open(str(self.sequential_to_identifierref[sequential]) + '/assignment_settings.xml', 'wb') as assignment_settings_xml:
                            tree = lxml.etree.ElementTree(root)
                            tree.write(assignment_settings_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

                        with export_fs.open(str(self.sequential_to_identifierref[sequential]) + '/' + html_file_name, 'w') as html_file:
                            html_file.write(html_content)
                            html_file.write(sequential.display_name)
                            html_file.write(html_content_pt2)

    def export_imsmanifest_xml(self, modulestore, courselike_keys, courselikes, export_fs, external_tool_only):
        """
        Exports the imsmanifest.xml file
        """

        ############### Metadata section of imsmanifeset.xml ####################

        # Create the root element with proper namespaces
        root = lxml.etree.Element(
            'manifest',
            {
                'identifier': self.create_uuid()
            },
            nsmap={
                None: 'http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1',
                'lom': 'http://ltsc.ieee.org/xsd/imsccv1p1/LOM/resource',
                'lomimscc': 'http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1 http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_imscp_v1p2_v1p0.xsd '
                'http://ltsc.ieee.org/xsd/imsccv1p1/LOM/resource http://www.imsglobal.org/profile/cc/ccv1p1/LOM/ccv1p1_lomresource_v1p0.xsd '
                'http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest http://www.imsglobal.org/profile/cc/ccv1p1/LOM/ccv1p1_lommanifest_v1p0.xsd')

        # Set all the metadata values
        metadata = lxml.etree.SubElement(root, 'metadata')
        lxml.etree.SubElement(metadata, 'schema').text = 'IMS Common Cartridge'
        lxml.etree.SubElement(metadata, 'schemaversion').text = '1.1.0'

        lom = lxml.etree.SubElement(metadata, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}lom')

        general = lxml.etree.SubElement(lom, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}general')
        title = lxml.etree.SubElement(general, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}title')
        # Sets the title of the imsmanifest to MULTI-COURSE-EXPORT if it's multi course, the default courselike key otherwise
        if len(courselike_keys) > 1:
            lxml.etree.SubElement(title, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}string').text = 'MULTI-COURSE-EXPORT'
        else:
            lxml.etree.SubElement(title, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}string').text = str(courselike_keys[0])
        lifecycle = lxml.etree.SubElement(lom, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}lifeCycle')
        contribute = lxml.etree.SubElement(lifecycle, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}contribute')
        date = lxml.etree.SubElement(contribute, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}date')
        lxml.etree.SubElement(date, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}dateTime').text = (datetime.now()).strftime('%Y-%m-%d') # extract current date

        rights = lxml.etree.SubElement(lom, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}rights')
        copyright = lxml.etree.SubElement(rights, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}copyrightAndOtherRestrictions')
        lxml.etree.SubElement(copyright, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}value').text = 'yes'
        description = lxml.etree.SubElement(rights, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}description')
        lxml.etree.SubElement(description, '{http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest}string').text = 'Private (Copyrighted) - http://en.wikipedia.org/wiki/Copyright'

        ######################## Organizations section of imsmanifest.xml ##########################

        # Create organizations and organization element
        organizations = lxml.etree.SubElement(root, 'organizations')
        organization = lxml.etree.SubElement(organizations, 'organization', {'identifier': 'org_1', 'structure': 'rooted-hierarchy'})

        learning_module = lxml.etree.SubElement(organization, 'item', {'identifier': 'LearningModules'})

        # Iterate through all the courselikes keys
        for courselike_key in courselike_keys:
            # Bulk operations and only operate on published content
            with self.modulestore.bulk_operations(courselike_key):
                with self.modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
                    courselike = self.get_courselike(courselike_key)
                    module = lxml.etree.SubElement(learning_module, 'item', {'identifier': self.module_identifiers[courselike_key]})
                    lxml.etree.SubElement(module, 'title').text = f"{(self.get_key(courselike_key)).course} {courselike.display_name}"

                    # Build out all the chapters and sequentials underneath the one learning module (course)
                    chapter_and_sequential_modules = self.get_chapter_sequential_modules(modulestore, courselike_key)

                    # Course abbreviation to append to the start of the module names
                    course_abbreviation = self.get_course_abbreviation(courselike_key)

                    parent_chapter = None
                    for chapter_sequential in chapter_and_sequential_modules:
                        chapter_sequential = self.serialize_chapter_sequential(chapter_sequential)
            
                        if chapter_sequential.category == 'chapter':
                            parent_chapter = chapter = lxml.etree.SubElement(module, 'item', {'identifier': self.chapter_to_identifier[chapter_sequential]})
                            lxml.etree.SubElement(chapter, 'title').text = course_abbreviation + chapter_sequential.display_name

                        if chapter_sequential.category == 'sequential':
                            sequential = lxml.etree.SubElement(parent_chapter, 'item', {'identifier': self.sequential_to_identifier[chapter_sequential], 'identifierref': self.sequential_to_identifierref[chapter_sequential]})
                            lxml.etree.SubElement(sequential, 'title').text = course_abbreviation + chapter_sequential.display_name

        ############################# Resources section of imsmanifest.xml #############################

        # Create resources element
        resources = lxml.etree.SubElement(root, 'resources')
        type_string = 'associatedcontent/imscc_xmlv1p1/learning-application-resource'

        # Course settings
        course_settings_resource = lxml.etree.SubElement(resources, 'resource', {'identifier': self.course_settings_identifier, 'type': type_string, 'href': 'course_settings/canvas_export.txt'})
        course_settings_path = 'course_settings'
        for filename in export_fs.listdir(course_settings_path):
            lxml.etree.SubElement(course_settings_resource, 'file', {'href': 'course_settings/' + filename})

        # Only export assignment resources if we're not doing external_tool_only
        if not external_tool_only:
            # Iterate through all the courselike keys and courselikes
            for courselike_key, courselike in zip(courselike_keys, courselikes):
                # Bulk operations and only operate on published content
                with self.modulestore.bulk_operations(courselike_key):
                    with self.modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
                        # Create resources for assignment sequentials
                        sequential_modules = self.get_sequential_modules(modulestore, courselike_key)

                        assignment_types = {assignment_type['type'] for assignment_type in courselike.grading_policy['GRADER']}
                        for sequential in sequential_modules:
                            sequential = self.serialize_chapter_sequential(sequential)
                            if sequential.format in assignment_types:
                                html_file_path = self.sequential_to_identifierref[sequential]
                                xml_file_path = self.sequential_to_identifierref[sequential]
                                for filename in export_fs.listdir(self.sequential_to_identifierref[sequential]):
                                    if filename.endswith('.html'):
                                        html_file_path = html_file_path + '/' + filename
                                    if filename.endswith('xml'):
                                        xml_file_path = xml_file_path + '/' + filename
                                resource = lxml.etree.SubElement(resources , 'resource', {'identifier': self.sequential_to_identifierref[sequential], 'type': type_string, 'href': html_file_path})
                                lxml.etree.SubElement(resource, 'file', {'href': html_file_path})
                                lxml.etree.SubElement(resource, 'file', {'href': xml_file_path})

        # Additional last resource for the external tool xml
        external_tool_resource = lxml.etree.SubElement(resources, 'resource', {'identifier': self.external_tool_identifierref, 'type': 'imsbasiclti_xmlv1p0'})
        lxml.etree.SubElement(external_tool_resource, 'file', {'href': self.external_tool_identifierref + '.xml'})

        # Write to file
        with export_fs.open('imsmanifest.xml', 'wb') as imsmanifest_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(imsmanifest_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_external_tool(self, export_fs):
        # Create the root element
        root = lxml.etree.Element(
            'cartridge_basiclti_link',
            nsmap={
                None: 'http://www.imsglobal.org/xsd/imslticc_v1p0',
                'blti': 'http://www.imsglobal.org/xsd/imsbasiclti_v1p0',
                'lticm': 'http://www.imsglobal.org/xsd/imslticm_v1p0',
                'lticp': 'http://www.imsglobal.org/xsd/imslticp_v1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                    'http://www.imsglobal.org/xsd/imslticc_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticc_v1p0.xsd'
                    'http://www.imsglobal.org/xsd/imsbasiclti_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imsbasiclti_v1p0p1.xsd'
                    'http://www.imsglobal.org/xsd/imslticm_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticm_v1p0.xsd'
                    'http://www.imsglobal.org/xsd/imslticp_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticp_v1p0.xsd')

        # Basic metadata content
        lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}title', nsmap={'blti': 'http://www.imsglobal.org/xsd/imsbasiclti_v1p0'}).text = f'{self.platform_name} ({self.lms_base})'
        lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}description').text = ''
        lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}secure_launch_url').text = f'https://{self.lms_base}/lti_provider/'
        vendor = lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}vendor')
        lxml.etree.SubElement(vendor, '{http://www.imsglobal.org/xsd/imslticp_v1p0}code').text = 'unknown'
        lxml.etree.SubElement(vendor, '{http://www.imsglobal.org/xsd/imslticp_v1p0}name').text = 'unknown'
        lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}custom')
        extensions = lxml.etree.SubElement(root, '{http://www.imsglobal.org/xsd/imsbasiclti_v1p0}extensions', platform='canvas.instructure.com')
        lxml.etree.SubElement(extensions, '{http://www.imsglobal.org/xsd/imslticm_v1p0}property', name='privacy_level').text = 'public'
        lxml.etree.SubElement(extensions, '{http://www.imsglobal.org/xsd/imslticm_v1p0}property', name='domain').text = f'{self.lms_base}'
        lxml.etree.SubElement(extensions, '{http://www.imsglobal.org/xsd/imslticm_v1p0}property', name='lti_version').text = '1.1'

        with export_fs.open(self.external_tool_identifierref + '.xml', 'wb') as external_tool_identifierref_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(external_tool_identifierref_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_module_meta_xml(self, modulestore, courselike_keys, courselikes, export_fs, external_tool_only):
        """
        Exports the module_meta.xml file in course_settings
        """

        # Create the root element
        root = lxml.etree.Element(
            'modules',
            nsmap = {
                None: 'http://canvas.instructure.com/xsd/cccv1p0',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            }
        )

        root.set('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation',
                'http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd')

        # Iterate through all the courselikes keys
        for courselike_key, courselike in zip(courselike_keys, courselikes):
            # Bulk operations and only operate on published content
            with self.modulestore.bulk_operations(courselike_key):
                with self.modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, courselike_key):
                    courselike = self.get_courselike(courselike_key)
                    # Get all the chapter and sequential modules to appear under the modules page
                    chapter_sequential_modules = self.get_chapter_sequential_modules(modulestore, courselike_key)
                    # Parse out assignments (assignments are sequentials with a 'format' in the grading policy)
                    assignment_types = {assignment_type['type'] for assignment_type in courselike.grading_policy['GRADER']}

                    module = lxml.etree.SubElement(root, 'module', {'identifier': self.module_identifiers[courselike_key]})
                    lxml.etree.SubElement(module, 'title').text =  f"{(self.get_key(courselike_key)).course} {courselike.display_name}"
                    lxml.etree.SubElement(module, 'workflow_state').text = 'active'

                    items = lxml.etree.SubElement(module, 'items')

                    # Course abbreviation to append to the start of the module names
                    course_abbreviation = self.get_course_abbreviation(courselike_key)

                    # Iterate through chapter_sequential_modules and assign their type as they would appear in the modules page
                    # Example types: Header that just has text, external tool, assignment, etc.

                    # Keep track of the position of the element in the course to keep the same order as the course
                    course_position = 0

                    # Check for external_tool_only
                    if not external_tool_only:
                        # external_tool_only is false, add these types of modules (Assignment, ExternalTool, ModuleSubHeader) to the modules page.
                        # Assignment types are recognized correctly by external LMS (e.g. Blackboard, Canvas) on import.
                        for chapter_sequential in chapter_sequential_modules:
                            course_position += 1
                            chapter_sequential = self.serialize_chapter_sequential(chapter_sequential)
                            if chapter_sequential.format in assignment_types:
                                item = lxml.etree.SubElement(items, 'item', {'identifier': self.sequential_to_identifier[chapter_sequential]})
                                lxml.etree.SubElement(item, 'content_type').text = 'Assignment'
                                lxml.etree.SubElement(item, 'title').text = course_abbreviation + chapter_sequential.display_name
                                lxml.etree.SubElement(item, 'workflow_state').text= 'active'
                                lxml.etree.SubElement(item, 'identifierref').text = self.sequential_to_identifierref[chapter_sequential]
                                lxml.etree.SubElement(item, 'indent').text = '1'
                                lxml.etree.SubElement(item, 'position').text = str(course_position)
                                lxml.etree.SubElement(item, 'new_tab').text = 'false'
                                lxml.etree.SubElement(item, 'link_settings_json').text = 'null'
                            elif chapter_sequential.category == 'sequential':
                                item = lxml.etree.SubElement(items, 'item', {'identifier': self.sequential_to_identifierref[chapter_sequential]})
                                lxml.etree.SubElement(item, 'content_type').text = 'ContextExternalTool'
                                lxml.etree.SubElement(item, 'title').text = course_abbreviation + chapter_sequential.display_name
                                lxml.etree.SubElement(item, 'workflow_state').text= 'active'
                                lxml.etree.SubElement(item, 'identifierref').text = self.external_tool_identifierref
                                lti_link = f'https://{self.lms_base}/lti_provider/courses/' + str(courselike_key) + "/" + (str(courselike_key)).replace('course', 'block') + '+type@sequential+block@' + chapter_sequential.url_name
                                lxml.etree.SubElement(item, 'url').text = lti_link
                                lxml.etree.SubElement(item, 'indent').text = '1'
                                lxml.etree.SubElement(item, 'position').text = str(course_position)
                                lxml.etree.SubElement(item, 'new_tab').text = 'false'
                                lxml.etree.SubElement(item, 'link_settings_json').text = '{"selection_width":"","selection_height":""}'
                            else:
                                item = lxml.etree.SubElement(items, 'item', {'identifier': self.chapter_to_identifier[chapter_sequential]})
                                lxml.etree.SubElement(item, 'content_type').text = 'ContextModuleSubHeader'
                                lxml.etree.SubElement(item, 'title').text = chapter_sequential.display_name
                                lxml.etree.SubElement(item, 'workflow_state').text= 'active'
                                lxml.etree.SubElement(item, 'indent').text = '0'
                                lxml.etree.SubElement(item, 'position').text = str(course_position)
                                lxml.etree.SubElement(item, 'new_tab').text = 'false'
                                lxml.etree.SubElement(item, 'link_settings_json').text = 'null'
                    else:
                        # external_tool_only is true, add these types of modules (ExternalTool, ModuleSubHeader) external tools to the modules page.
                        # We want to exclude Assignment types because they are not recognized correctly by external LMS (e.g. D2L) on import.
                        for chapter_sequential in chapter_sequential_modules:
                            course_position += 1
                            chapter_sequential = self.serialize_chapter_sequential(chapter_sequential)
                            if chapter_sequential.category == 'sequential':
                                item = lxml.etree.SubElement(items, 'item', {'identifier': self.sequential_to_identifierref[chapter_sequential]})
                                lxml.etree.SubElement(item, 'content_type').text = 'ContextExternalTool'
                                lxml.etree.SubElement(item, 'title').text = course_abbreviation + chapter_sequential.display_name
                                lxml.etree.SubElement(item, 'workflow_state').text= 'active'
                                lxml.etree.SubElement(item, 'identifierref').text = self.external_tool_identifierref
                                lti_link = f'https://{self.lms_base}/lti_provider/courses/' + str(courselike_key) + "/" + (str(courselike_key)).replace('course', 'block') + '+type@sequential+block@' + chapter_sequential.url_name
                                lxml.etree.SubElement(item, 'url').text = lti_link
                                lxml.etree.SubElement(item, 'indent').text = '1'
                                lxml.etree.SubElement(item, 'position').text = str(course_position)
                                lxml.etree.SubElement(item, 'new_tab').text = 'false'
                                lxml.etree.SubElement(item, 'link_settings_json').text = '{"selection_width":"","selection_height":""}'
                            else:
                                item = lxml.etree.SubElement(items, 'item', {'identifier': self.chapter_to_identifier[chapter_sequential]})
                                lxml.etree.SubElement(item, 'content_type').text = 'ContextModuleSubHeader'
                                lxml.etree.SubElement(item, 'title').text = chapter_sequential.display_name
                                lxml.etree.SubElement(item, 'workflow_state').text= 'active'
                                lxml.etree.SubElement(item, 'indent').text = '0'
                                lxml.etree.SubElement(item, 'position').text = str(course_position)
                                lxml.etree.SubElement(item, 'new_tab').text = 'false'
                                lxml.etree.SubElement(item, 'link_settings_json').text = 'null'

        # Write to file
        with export_fs.open('course_settings/module_meta.xml', 'wb') as module_meta_xml:
            tree = lxml.etree.ElementTree(root)
            tree.write(module_meta_xml, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    def export_all_course_settings(self, modulestore, courselike_keys, courselikes, export_fs, external_tool_only):
        """
        Function to export all course_settings at once
        """
        export_fs.makedirs('course_settings', recreate=True)
        if not external_tool_only:
            self.export_assignment_groups(modulestore, courselikes, export_fs)
        self.export_media_tracks(export_fs)
        self.export_files_meta(export_fs)
        self.export_course_settings(modulestore, courselike_keys, export_fs)

    def export(self):
        """
        Perform the export given the parameters handed to this class at init.
        """
        # Create a list of all the different courselikes
        courselikes = []
        for courselike_key in self.courselike_keys:
            courselikes.append(self.get_courselike(courselike_key))

        fsm = OSFS(self.root_dir)

        # Make the directory to export to
        export_fs = fsm.makedir(self.target_dir, recreate=True)

        # Call export functions
        self.export_external_tool(export_fs)
        self.export_all_course_settings(self.modulestore, self.courselike_keys, courselikes, export_fs, self.external_tool_only)
        if not self.external_tool_only:
            self.export_assignment_folders(self.modulestore, self.courselike_keys, courselikes, export_fs)
        self.export_imsmanifest_xml(self.modulestore, self.courselike_keys, courselikes, export_fs, self.external_tool_only)
        self.export_module_meta_xml(self.modulestore, self.courselike_keys, courselikes, export_fs, self.external_tool_only)



"""
Function "export_course_to_imscc" below get called by the django management comman from export_olx.py
"""

def export_course_to_imscc(modulestore, contentstore, course_key, root_dir, course_dir, external_tool_only):
    """
    Thin wrapper for the Export Manager. See ExportManager for details.
    """
    CourseExportManager(modulestore, contentstore, course_key, root_dir, course_dir, external_tool_only).export()
    